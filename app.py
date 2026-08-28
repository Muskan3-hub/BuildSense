import os
import json
import secrets
import logging
import time
from datetime import date, datetime, timedelta, timezone
from flask import Flask, request, jsonify, send_from_directory, render_template, session, redirect, url_for, Response, g
from flask_cors import CORS
from werkzeug.utils import secure_filename

from agents.metrics import (
    HTTP_REQUESTS_TOTAL,
    HTTP_REQUEST_DURATION_SECONDS,
    ACTIVE_REQUESTS_IN_FLIGHT,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

# Configure structured logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('BuildSenseEngine')

# Import agent package
from agents.config import set_api_key, is_live_mode, get_api_key
from agents.coordinator import run_coordination_pipeline
from agents.tools import tool_registry
from agents.tools.weather_api import get_weather_advisory
from agents.tools.json_report import generate_json_report
from agents.memory import conversation_memory, knowledge_store
from agents.calendar_service import calendar_service
from agents.tools.design_catalog import STYLE_PRESETS
from agents.auth import authenticate, register_user, get_current_user, login_required
from agents.database import db
from agents.workforce import analyse_project_profile, minimum_workers_for_duration

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)
# Use the configured secret; otherwise persist one generated key per install
# so dev reloads / restarts never silently invalidate everyone's session.
app.secret_key = os.getenv("FLASK_SECRET_KEY")
if not app.secret_key:
    _SECRET_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.flask_secret')
    try:
        if os.path.exists(_SECRET_FILE):
            with open(_SECRET_FILE) as _fh:
                app.secret_key = _fh.read().strip()
        if not app.secret_key:
            app.secret_key = secrets.token_hex(32)
            with open(_SECRET_FILE, 'w') as _fh:
                _fh.write(app.secret_key)
    except OSError:
        app.secret_key = secrets.token_hex(32)



# Track metrics for monitoring
system_metrics = {
    "start_time": time.time(),
    "total_requests": 0,
    "failed_requests": 0
}

@app.before_request
def track_metrics():
    system_metrics["total_requests"] += 1
    g._request_start_time = time.time()
    endpoint = request.endpoint or request.path
    try:
        ACTIVE_REQUESTS_IN_FLIGHT.labels(endpoint=endpoint).inc()
    except Exception:
        pass


@app.after_request
def record_prometheus_metrics(response):
    endpoint = request.endpoint or request.path
    try:
        ACTIVE_REQUESTS_IN_FLIGHT.labels(endpoint=endpoint).dec()
    except Exception:
        pass
    if hasattr(g, '_request_start_time'):
        duration = time.time() - g._request_start_time
        try:
            HTTP_REQUEST_DURATION_SECONDS.labels(
                endpoint=endpoint,
            ).observe(duration)
        except Exception:
            pass
    try:
        HTTP_REQUESTS_TOTAL.labels(
            endpoint=endpoint,
            method=request.method,
            status=str(response.status_code),
        ).inc()
    except Exception:
        pass
    return response


@app.route('/metrics', methods=['GET'])
def metrics_endpoint():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


# Ensure folders exist
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
REPORTS_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
os.makedirs(REPORTS_FOLDER, exist_ok=True)
app.config['REPORTS_FOLDER'] = REPORTS_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload


ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'pdf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _resolve_conversation_id(raw_conv_id, user):
    """Normalize a client-supplied conversation id and verify ownership.

    Returns the conversation dict when the authenticated user owns it,
    otherwise None. Never raises.
    """
    if raw_conv_id is None:
        return None
    try:
        conv_id = int(raw_conv_id)
    except (TypeError, ValueError):
        return None
    if conv_id <= 0 or not user:
        return None
    try:
        return db.get_conversation(conv_id, user['id'])
    except Exception:
        return None


def _persist_exchange_messages(user, conversation, user_query, synthesis, blueprint_id=None):
    """Persist the user question and final assistant answer for a query.

    This is the single authoritative save point for chat history so that
    Recent Chats always load both sides of every exchange. Returns the
    persisted message ids (assistant entry is omitted when no synthesis).
    """
    persisted = {}
    if not user or not conversation:
        return persisted
    try:
        query_text = (user_query or '').strip() if isinstance(user_query, str) else ''
        if query_text:
            user_msg = db.add_message(conversation['id'], 'user', query_text)
            persisted['user_message_id'] = user_msg['id']
        synthesis_text = (synthesis or '').strip() if isinstance(synthesis, str) else ''
        if synthesis_text:
            metadata = {'blueprint_id': blueprint_id} if blueprint_id else None
            assistant_msg = db.add_message(
                conversation['id'], 'assistant', synthesis_text, metadata
            )
            persisted['assistant_message_id'] = assistant_msg['id']
    except Exception as db_err:
        logger.warning(f"Chat message persistence failed: {db_err}")
    return persisted

@app.route('/')
def index():
    if not get_current_user():
        return redirect(url_for('login_page'))
    return render_template('index.html')

@app.route('/login')
def login_page():
    if get_current_user():
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    user = authenticate(username, password)
    if user:
        session['user'] = user
        logger.info(f"User '{username}' logged in successfully.")
        return jsonify({"success": True, "user": user})
    
    logger.warning(f"Failed login attempt for username: '{username}'")
    return jsonify({"error": "Invalid username or password"}), 401

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.pop('user', None)
    return jsonify({"success": True, "message": "Logged out successfully"})

@app.route('/api/auth/status', methods=['GET'])
def auth_status():
    user = get_current_user()
    if user:
        return jsonify({"authenticated": True, "user": user})
    return jsonify({"authenticated": False})

@app.route('/api/auth/register', methods=['POST'])
def register():
    """Register a new user account."""
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    confirm_password = data.get('confirm_password', '').strip()

    # Validation
    if not username:
        return jsonify({"error": "Username is required"}), 400
    if not password:
        return jsonify({"error": "Password is required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    if password != confirm_password:
        return jsonify({"error": "Passwords do not match"}), 400
    if not name:
        name = username

    user = register_user(username=username, password=password, name=name, email=email)
    if user is None:
        return jsonify({"error": "Username or email already exists"}), 409

    logger.info(f"New user registered: '{username}'")
    return jsonify({"success": True, "message": "Account created successfully", "user": user}), 201

@app.route('/register')
def register_page():
    """Render the registration page."""
    if get_current_user():
        return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health endpoint for Docker/Orchestrator monitoring."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat() if 'datetime' in globals() else time.time()
    })

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    if request.method == 'POST':
        data = request.json or {}
        api_key = data.get('api_key', '').strip()
        set_api_key(api_key)
        return jsonify({
            "success": True,
            "is_configured": is_live_mode(),
            "message": (
                "Gemini API key updated successfully."
                if api_key
                else "Gemini API key cleared. Live blueprint analysis is disabled."
    )
})
    else:
        return jsonify({
            "is_configured": is_live_mode(),
            "mode": (
                "Live (Gemini API Connected)" 
                if is_live_mode() 
                else "Gemini API Not Configured"
            )
        })


@app.route('/api/tools', methods=['GET'])
def get_tool_manifest():
    """Returns JSON manifest of all registered enterprise tools."""
    from agents.tools import tool_registry
    return jsonify({
        "tools": tool_registry.get_tool_manifest(),
        "total_tools": len(tool_registry.get_tool_manifest())
    })


@app.route('/api/weather', methods=['GET'])
def get_weather():
    """Direct weather tool query for the dashboard weather widget."""
    city = request.args.get('city', '')
    country_code = request.args.get('country', 'IN')
    lat = request.args.get('lat', type=float)
    lon = request.args.get('lon', type=float)
    # Require either coordinates or a city name — never default to a hardcoded city
    has_coords = lat is not None and lon is not None
    has_city = bool(city and city.strip())
    if not has_coords and not has_city:
        return jsonify({"error": "Location not available. Please enable location permissions or enter a city."}), 400
    extra = {"lat": lat, "lon": lon} if has_coords else {}
    result = tool_registry.invoke("get_weather_advisory", city=city, country_code=country_code, **extra)
    if result.get("status") == "success":
        return jsonify(result.get("output", {}))
    else:
        return jsonify({"error": result.get("error", "Weather tool failed"), "city": city}), 500

@app.route('/api/calendar', methods=['GET'])
def get_calendar():
    """Generate a project calendar from an explicit timeline.

    Requires `timeline` as a JSON array of {"phase": str, "duration_days": int}
    (URL-encoded) — the server never fabricates a default project plan.
    """
    start_date = request.args.get('start_date', date.today().isoformat())
    raw_timeline = request.args.get('timeline', '')
    if not raw_timeline.strip():
        return jsonify({
            "error": "Missing required parameter 'timeline' (JSON array of "
                     "{phase, duration_days}). Run an analysis first or supply a plan."
        }), 400
    try:
        timeline = json.loads(raw_timeline)
    except json.JSONDecodeError:
        return jsonify({"error": "Parameter 'timeline' must be valid JSON"}), 400
    if not isinstance(timeline, list) or not timeline:
        return jsonify({"error": "Parameter 'timeline' must be a non-empty JSON array"}), 400
    result = tool_registry.invoke("generate_project_calendar", timeline=timeline, start_date_str=start_date)
    if result.get("status") == "success":
        return jsonify(result.get("output", {}))
    return jsonify({"error": "Failed to generate calendar"}), 500


@app.route('/api/calendar/events', methods=['GET', 'POST'])
def calendar_events():
    """Create and retrieve durable project-calendar events."""
    user = get_current_user()

    if request.method == 'GET':
        # Use DB-backed calendar for authenticated users
        if user:
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')
            events = db.get_calendar_events(user['id'], start_date, end_date)
            return jsonify({"calendar_status": "database", "events": events})
        # Fallback to JSON file for unauthenticated
        events = calendar_service.list_events(
            request.args.get('start_date'),
            request.args.get('end_date'),
            request.args.get('project_id'),
            request.args.get('include_legacy') == 'true',
        )
        logger.info("Calendar API listed events: count=%d", len(events))
        return jsonify({"calendar_status": "local_fallback", "events": events})

    try:
        data = request.json or {}
        # Persist to DB for authenticated users
        if user:
            event = db.create_calendar_event(user['id'], **data)
            return jsonify({"calendar_status": "database", "event": event, "created": True}), 201
        result = calendar_service.create_event(data)
        logger.info(
            "Calendar API created event: id=%s status=%s",
            result["event"]["id"],
            result["calendar_status"],
        )
        return jsonify(result), 201 if result.get("created") else 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Calendar API event creation failed")
        return jsonify({"error": f"Calendar event creation failed: {exc}"}), 500


def _add_months_clamped(start_date, months):
    """Calendar-accurate month addition.

    Uses real calendar month arithmetic (never a fixed 30-day month):
    - different month lengths handled via calendar.monthrange
    - February / leap years / year transitions all resolve naturally
    - month-end starts clamp to the target month's last day
      (e.g. 31 Jan + 1 month -> 28/29 Feb)
    """
    from calendar import monthrange
    total_month_index = start_date.month - 1 + int(months)
    year = start_date.year + total_month_index // 12
    month = total_month_index % 12 + 1
    day = min(start_date.day, monthrange(year, month)[1])
    return date(year, month, day)


def _parse_duration_months(raw):
    """Strict 1-12 integer parse; anything else is None (never guessed)."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 12 else None


def _resolve_workforce_minimum(user, raw_conversation_id):
    """Structured blueprint-specific minimum workforce for THIS user's
    CURRENT conversation.

    The value is stored per user by the coordinator (from the Gemini-first
    blueprint analysis) together with the conversation and blueprint ids it
    belongs to. A different user can never read another's entry
    (knowledge_store scope), and a different conversation/blueprint simply
    does not match — so nothing carries over into new chats.
    """
    conversation_ctx = _resolve_conversation_id(raw_conversation_id, user)
    entries = knowledge_store.retrieve(
        topic="project", key="workforce_minimum", user_id=user['id'],
    )
    for entry in reversed(entries):
        value = entry.get("value")
        if not isinstance(value, dict):
            continue
        minimum_workers = value.get("minimum_workers")
        if not isinstance(minimum_workers, int) or isinstance(minimum_workers, bool) \
                or minimum_workers < 1:
            continue
        stored_conv_id = value.get("conversation_id")
        if conversation_ctx is None:
            # No active conversation context: only a requirement recorded
            # without one may apply.
            if stored_conv_id is None:
                return value
            continue
        if str(stored_conv_id) == str(conversation_ctx['id']):
            return value
    return None


@app.route('/api/calendar/generate', methods=['POST'])
@login_required
def generate_construction_plan():
    """Generate a day-by-day construction plan from Start Date + Duration +
    No. of Workers.

    The END DATE is derived server-side from start date + duration using
    proper calendar month arithmetic (the scheduling engine still receives
    an explicit range internally). Workers must be a whole number >= the
    blueprint-specific structured minimum (when one was determined for this
    user's current conversation); generation is refused otherwise BEFORE any
    planning happens.

    The plan is built ONLY from the user's real stored project schedule
    (produced by a prior blueprint analysis) — the server never fabricates
    one. Working days come from the existing calendar engine (Sundays and
    gazetted holidays are skipped); every other day in the derived range is
    reported honestly as off/free. Generated work days are persisted in the
    per-user calendar_events table so they survive reloads and month
    navigation; regenerating replaces the previous generated plan.
    """
    user = get_current_user()
    data = request.get_json(silent=True) or {}

    start_raw = str(data.get('start_date') or '').strip()
    if not start_raw:
        return jsonify({"error": "Please select a start date."}), 400
    try:
        start = datetime.strptime(start_raw, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Start date is invalid. Use the date picker (YYYY-MM-DD)."}), 400

    # Duration — the single authoritative plan length (1-12 months).
    try:
        duration_months = int(str(data.get('duration_months', '')).strip())
    except (TypeError, ValueError):
        return jsonify({"error": "Please select a duration between 1 and 12 months."}), 400
    if not 1 <= duration_months <= 12:
        return jsonify({"error": "Duration must be between 1 and 12 months."}), 400

    # Workers — whole positive numbers only (decimals, negatives, zero rejected).
    workers_raw = str(data.get('workers', '')).strip()
    invalid_workers_msg = {
        "error": "Please enter a valid number of workers (a whole number of at least 1)."
    }
    try:
        workers = int(workers_raw)
    except (TypeError, ValueError):
        return jsonify(invalid_workers_msg), 400
    if workers < 1 or workers_raw != str(workers):
        # The string round-trip rejects decimals ("17.5"), signs ("-5", "+7").
        return jsonify(invalid_workers_msg), 400

    # The project's real phase timeline is required for planning AND for
    # duration-specific workforce sizing — retrieve it once up front.
    schedule_entries = knowledge_store.retrieve(
        topic="project", key="schedule", user_id=user['id'],
    )
    timeline = None
    for entry in reversed(schedule_entries):
        value = entry.get("value") or {}
        if isinstance(value, dict) and value.get("timeline"):
            timeline = value["timeline"]
            break
    if not timeline:
        # Check if a blueprint analysis exists for this user / conversation
        spatial_entries = knowledge_store.retrieve(
            topic="project", key="blueprint_analysis", user_id=user['id'],
        )
        spatial_data = None
        for entry in reversed(spatial_entries):
            val = entry.get("value") or {}
            if isinstance(val, dict):
                spatial_data = val
                break
        if not spatial_data:
            user_bps = db.get_blueprints(user['id'])
            if user_bps:
                bp_analysis = db.get_analysis(user_bps[0]['id'])
                if bp_analysis:
                    spatial_data = bp_analysis.get('spatial_data')
        if spatial_data:
            from agents.scheduling import generate_schedule
            sched_result = generate_schedule(scope_data=spatial_data)
            if sched_result and isinstance(sched_result, dict) and sched_result.get("timeline"):
                timeline = sched_result["timeline"]
                knowledge_store.store(
                    "project",
                    "schedule",
                    sched_result,
                    "Scheduling Agent",
                    user_id=user['id'],
                )
    if not timeline:
        return jsonify({
            "error": "No construction schedule found yet. Upload and analyze a "
                     "blueprint first — the plan is generated from your project's "
                     "real phase timeline."
        }), 400

    # ── Backend enforcement of the DURATION-SPECIFIC minimum ──────────────
    # A crafted request cannot bypass what the UI enforces. The minimum is
    # recalculated here from THIS blueprint's stored analysis for THIS exact
    # duration and start date — never reused from a previous selection.
    reference_requirement = _resolve_workforce_minimum(
        user, data.get('conversation_id'),
    )
    profile = analyse_project_profile(
        spatial_data=None,
        timeline=timeline,
        reference_minimum=reference_requirement,
    )
    duration_requirement = None
    if profile.get("reference_workers") and profile.get("planned_working_days"):
        duration_requirement = minimum_workers_for_duration(
            profile, duration_months, start.isoformat(),
        )
        if duration_requirement.get("feasible") is False:
            return jsonify({
                "error": duration_requirement.get("reason")
                or "The selected duration is not realistically achievable "
                   "for this project.",
                "feasible": False,
                "duration_months": duration_months,
            }), 400

    effective_minimum = None
    if isinstance(duration_requirement, dict) \
            and isinstance(duration_requirement.get("minimum_workers"), int):
        effective_minimum = duration_requirement["minimum_workers"]
    elif reference_requirement:
        # No duration-specific sizing possible yet — fall back to the
        # blueprint's duration-independent reference crew.
        effective_minimum = reference_requirement["minimum_workers"]

    if effective_minimum and workers < effective_minimum:
        return jsonify({
            "error": (
                f"This project requires at least {effective_minimum} workers "
                f"to meet the selected {duration_months}-month duration."
            ),
            "minimum_workers": effective_minimum,
            "duration_months": duration_months,
        }), 400

    # End date derived internally from Start Date + Duration (calendar math).
    end = _add_months_clamped(start, duration_months)

    result = tool_registry.invoke(
        "generate_project_calendar",
        timeline=timeline,
        start_date_str=start.isoformat(),
        duration_months=duration_months,
        workers=workers,
        reference_workers=(reference_requirement or {}).get("minimum_workers"),
    ).get("output", {})
    if result.get("error") or not isinstance(result.get("daily_schedules"), dict):
        return jsonify({"error": "Failed to map the project timeline onto calendar dates."}), 500

    daily_schedules = result["daily_schedules"]
    holiday_names = {h.get("date"): h.get("name") for h in result.get("holidays", []) if h.get("date")}

    from agents.tools.calendar_engine import INDIAN_HOLIDAYS_2026

    days = []
    working_days = 0
    cursor_date = start
    while cursor_date <= end:
        iso = cursor_date.isoformat()
        day_items = daily_schedules.get(iso) or []
        if day_items:
            work = day_items[0]
            working_days += 1
            days.append({
                "date": iso,
                "day_name": cursor_date.strftime("%A"),
                "activity": work.get("phase") or "Construction work",
                "type": "work",
                "tasks": work.get("tasks", []),
                "location": work.get("location", ""),
                "start_time": work.get("start_time"),
                "end_time": work.get("end_time"),
            })
        elif cursor_date.weekday() == 6:
            days.append({
                "date": iso,
                "day_name": cursor_date.strftime("%A"),
                "activity": "Weekly Off — Sunday",
                "type": "sunday",
            })
        elif iso in holiday_names:
            days.append({
                "date": iso,
                "day_name": cursor_date.strftime("%A"),
                "activity": f"Holiday — {holiday_names[iso]}",
                "type": "holiday",
            })
        else:
            days.append({
                "date": iso,
                "day_name": cursor_date.strftime("%A"),
                "activity": "No scheduled work",
                "type": "free",
            })
        cursor_date += timedelta(days=1)

    conversation_id = data.get('conversation_id')
    conversation_scoped_id = (
        int(conversation_id) if str(conversation_id or "").isdigit() else None
    )

    # Persist the planning constraints for THIS conversation so follow-up
    # chat queries ("generate the construction plan") schedule against the
    # same duration + crew the user selected here. Scoped per conversation —
    # a new chat never inherits another chat's plan context.
    knowledge_store.store(
        "project",
        "plan_context",
        {
            "duration_months": duration_months,
            "workers": workers,
            "conversation_id": conversation_scoped_id,
        },
        "Project Timeline",
        user_id=user['id'],
    )

    # Phase 2: Force Wipe Before Insert
    # Right BEFORE the for loop that iterates through the new schedule to create events,
    # perform an aggressive wipe of all old construction plan events.
    raw_conv_id = data.get('conversation_id')
    active_conv_id = conversation_scoped_id if conversation_scoped_id is not None else raw_conv_id
    deleted_count = db.delete_events_by_conversation(user['id'], conversation_id=active_conv_id)
    logger.info("Aggressive Wipe: Deleted %d old events before saving new plan.", deleted_count)
    for day in days:
        if day["type"] != "work":
            continue
        tasks = ", ".join(day.get("tasks") or [])
        description = " | ".join(part for part in (
            f"Location: {day['location']}" if day.get("location") else "",
            tasks,
            f"Crew: {workers} workers",
        ) if part)
        db.create_calendar_event(
            user['id'],
            title=day["activity"],
            description=description,
            date=day["date"],
            start_time=day.get("start_time"),
            end_time=day.get("end_time"),
            all_day=False,
            location=day.get("location", ""),
            category="construction_plan",
            conversation_id=conversation_scoped_id,
        )

    return jsonify({
        "status": "ok",
        "plan_start": start.isoformat(),
        "plan_end": end.isoformat(),
        "duration_months": duration_months,
        "workers": workers,
        "total_days": len(days),
        "working_days": working_days,
        "project_end": result.get("project_end"),
        "planned_end_target": result.get("planned_end_target"),
        "feasibility": result.get("schedule_feasibility"),
        "holidays": result.get("holidays", []),
        "days": days,
    })


def _resolve_duration_workforce(user, raw_conversation_id, duration_months):
    """Cached duration-specific workforce requirement for THIS user's
    conversation (stored by the coordinator or a prior calculation).

    Matching requires BOTH the conversation and the exact duration — a
    requirement computed for 3 months can never validate a 6-month plan.
    """
    conversation_ctx = _resolve_conversation_id(raw_conversation_id, user)
    entries = knowledge_store.retrieve(
        topic="project", key="duration_workforce", user_id=user['id'],
    )
    for entry in reversed(entries):
        value = entry.get("value")
        if not isinstance(value, dict):
            continue
        if value.get("duration_months") != int(duration_months):
            continue
        stored_conv_id = value.get("conversation_id")
        if conversation_ctx is None:
            if stored_conv_id is None:
                return value
            continue
        if str(stored_conv_id) == str(conversation_ctx['id']):
            return value
    return None


def _build_duration_workforce_requirement(
    user, raw_conversation_id, duration_months, start_date=None,
):
    """Calculate the duration-specific minimum from THIS project's stored
    blueprint analysis (Gemini-derived schedule timeline + reference crew).

    Returns the structured result dict, or None when no blueprint schedule /
    reference crew exists yet (nothing is ever invented).
    """
    schedule_entries = knowledge_store.retrieve(
        topic="project", key="schedule", user_id=user['id'],
    )
    timeline = None
    for entry in reversed(schedule_entries):
        sched_value = entry.get("value") or {}
        if isinstance(sched_value, dict) and sched_value.get("timeline"):
            timeline = sched_value["timeline"]
            break

    reference = _resolve_workforce_minimum(user, raw_conversation_id)
    profile = analyse_project_profile(
        spatial_data=None,
        timeline=timeline,
        reference_minimum=reference,
    )
    if not profile.get("reference_workers") \
            or not profile.get("planned_working_days"):
        return None

    result = minimum_workers_for_duration(profile, duration_months, start_date)
    result["basis"] = result.get("basis") or {}
    return result


@app.route('/api/calendar/workforce-minimum', methods=['GET'])
@login_required
def get_workforce_minimum():
    """Structured blueprint-specific minimum workforce requirement.

    Without a duration parameter this returns the blueprint's reference crew
    (duration-independent). WITH ``duration_months`` it returns the
    DURATION-SPECIFIC minimum: cached per user + conversation + duration,
    calculated on demand from the stored Gemini blueprint analysis when not
    cached yet. Infeasible durations are reported honestly with
    feasible=false and no invented worker count.
    """
    user = get_current_user()
    raw_conv = request.args.get('conversation_id')

    duration_months = _parse_duration_months(request.args.get('duration_months'))
    if duration_months is None:
        # Legacy/reference behaviour (no duration selected).
        requirement = _resolve_workforce_minimum(user, raw_conv)
        if not requirement:
            return jsonify({"minimum_workers": None})
        return jsonify({
            "minimum_workers": requirement["minimum_workers"],
            "basis": str(requirement.get("basis") or ""),
        })

    start_raw = str(request.args.get('start_date') or '').strip()
    start_iso = None
    if start_raw:
        try:
            start_iso = datetime.strptime(start_raw, "%Y-%m-%d").date().isoformat()
        except ValueError:
            start_iso = None

    cached = _resolve_duration_workforce(user, raw_conv, duration_months)
    if cached is not None:
        cached_basis = cached.get("basis")
        if not isinstance(cached_basis, dict):
            cached_basis = {}
        # A cached value stays valid while it was computed for the same
        # start date; a changed start changes the real working-day window.
        if not start_iso or cached_basis.get("start_date_used") == start_iso:
            return jsonify({
                "duration_months": duration_months,
                "feasible": cached.get("feasible"),
                "minimum_workers": cached.get("minimum_workers"),
                "reason": cached.get("reason"),
                "source": "cached",
            })

    calculated = _build_duration_workforce_requirement(
        user, raw_conv, duration_months, start_iso,
    )
    if calculated is None:
        return jsonify({
            "duration_months": duration_months,
            "feasible": None,
            "minimum_workers": None,
            "reason": None,
            "source": "unavailable",
        })

    conversation_ctx = _resolve_conversation_id(raw_conv, user)
    knowledge_store.store(
        "project",
        "duration_workforce",
        {
            "duration_months": duration_months,
            "feasible": calculated.get("feasible"),
            "minimum_workers": calculated.get("minimum_workers"),
            "reason": calculated.get("reason"),
            "basis": {**calculated.get("basis", {}), "start_date_used": start_iso},
            "conversation_id": conversation_ctx['id'] if conversation_ctx else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        "Duration Workforce Analysis",
        user_id=user['id'],
    )
    return jsonify({
        "duration_months": duration_months,
        "feasible": calculated.get("feasible"),
        "minimum_workers": calculated.get("minimum_workers"),
        "reason": calculated.get("reason"),
        "source": "calculated",
    })


@app.route('/api/calendar/sync', methods=['POST'])
def sync_calendar_events():
    """Retry pending local events when an external connector is configured."""
    result = calendar_service.sync_pending()
    logger.info(
        "Calendar API synchronization completed: status=%s synced=%s",
        result.get("calendar_status"),
        result.get("synced"),
    )
    return jsonify(result)

@app.route('/api/design/styles', methods=['GET'])
def get_design_styles():
    """Endpoint to fetch available interior design styles."""
    styles = []
    for key, val in STYLE_PRESETS.items():
        styles.append({
            "id": key,
            "name": val["name"],
            "description": val["description"],
            "icon": val["icon"]
        })
    return jsonify({"styles": styles})

@app.route('/api/memory/history', methods=['GET'])
@login_required
def get_memory_history():
    """Endpoint to fetch short-term memory (conversation history).

    Strictly scoped to the authenticated caller: no user can ever read
    another user's conversation turns or knowledge entries.
    """
    user = get_current_user()
    return jsonify({
        "conversation": conversation_memory.get_history(user_id=user['id']),
        "knowledge": knowledge_store.get_all(user_id=user['id'])
    })

@app.route('/api/memory/clear', methods=['POST'])
@login_required
def clear_memory():
    """Endpoint to clear memory.

    Clears ONLY the authenticated user's own memory partitions.
    """
    user = get_current_user()
    conversation_memory.clear(user_id=user['id'])
    knowledge_store.clear(user_id=user['id'])
    return jsonify({"success": True, "message": "Memory cleared."})


@app.route('/reports/<filename>')
def serve_report(filename):
    """Serves generated JSON report files from the reports/ directory."""
    return send_from_directory(REPORTS_FOLDER, filename)

@app.route('/api/upload', methods=['POST'])
def upload_blueprint():
    if 'file' not in request.files:
        return jsonify({"error": "No file part in the request"}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    if not allowed_file(file.filename):
        return jsonify({
            "error": "Unsupported file format. "
                     "Please upload PNG, JPG, JPEG, or WEBP."
        }), 400

    filename = secure_filename(file.filename)

    if not filename:
        return jsonify({
            "error": "Invalid filename."
        }), 400

    # Unique filename prevents browser caching old analysis
    unique_filename = f"{int(time.time())}_{filename}"
    file_path = os.path.join(
        app.config['UPLOAD_FOLDER'],
        unique_filename
    )

    try:
        file.save(file_path)

        from agents.blueprint import analyze_blueprint

        logger.info(
            f"Starting blueprint analysis: {unique_filename}"
        )

        spatial_data = analyze_blueprint(file_path)

        logger.info(
            "Blueprint analysis completed successfully."
        )

        # Store blueprint in DB if user is logged in
        blueprint_db = None
        user = get_current_user()
        if user:
            conv_id = request.form.get('conversation_id', type=int)
            try:
                blueprint_db = db.create_blueprint(
                    user_id=user['id'],
                    conversation_id=conv_id,
                    filename=unique_filename,
                    file_path=file_path,
                )
                db.create_analysis(blueprint_db['id'], spatial_data)
                # Record activity
                db.add_activity(
                    user_id=user['id'],
                    activity_type="blueprint_analyzed",
                    description=f"Blueprint analyzed: {unique_filename}",
                    conversation_id=conv_id,
                    blueprint_id=blueprint_db['id'],
                )
            except Exception as db_err:
                logger.warning(f"DB blueprint storage failed: {db_err}")

        return jsonify({
            "success": True,
            "filename": unique_filename,
            "file_url": f"/uploads/{unique_filename}",
            "image_path": file_path,
            "spatial_data": spatial_data,
            "blueprint_id": blueprint_db['id'] if blueprint_db else None,
        })

    except Exception as e:
        logger.exception(
            "Blueprint analysis failed."
        )

        # Remove failed upload so it isn't reused later
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

        return jsonify({
            "success": False,
            "error": (
                "Blueprint analysis failed. "
                f"{str(e)}"
            )
        }), 500
            
    return jsonify({"error": "Unsupported file format. Please upload PNG, JPG, JPEG, or WEBP."}), 400

def _generate_demo_blueprint_asset():
    """
    Generate the optional 'Load Demo Blueprint' sample schematic into
    static/demo/ at startup. This asset is clearly labelled as a demo and is
    only used when a user explicitly clicks Load Demo Blueprint (which
    performs a REAL upload through /api/upload). It is never injected into
    user queries.
    """
    from PIL import Image, ImageDraw
    demo_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'demo')
    os.makedirs(demo_dir, exist_ok=True)
    file_path = os.path.join(demo_dir, "mock_blueprint.png")
    if os.path.exists(file_path):
        return file_path

    width, height = 1000, 750
    img = Image.new('RGB', (width, height), color='#0b0e1a')
    draw = ImageDraw.Draw(img)
    
    # Draw fine blueprint grid lines
    grid = 50
    for x in range(0, width, grid):
        draw.line([(x, 0), (x, height)], fill='#141a30', width=1)
    for y in range(0, height, grid):
        draw.line([(0, y), (width, y)], fill='#141a30', width=1)
        
    def get_box(pct_coords):
        rx, ry, rw, rh = pct_coords
        return [
            int(rx / 100 * width),
            int(ry / 100 * height),
            int((rx + rw) / 100 * width),
            int((ry + rh) / 100 * height)
        ]
        
    regions = [
        {"name": "Main Office Area\n(40' x 24')", "coords": [10, 10, 40, 35], "color": "#00d2ff"},
        {"name": "Conference Room A\n(24' x 20')", "coords": [55, 10, 35, 20], "color": "#00d2ff"},
        {"name": "Manager Office\n(16' x 24')", "coords": [55, 62, 20, 28], "color": "#00d2ff"},
        {"name": "Pantry & Restroom\n(10' x 25')", "coords": [77, 62, 13, 28], "color": "#00d2ff"},
        {"name": "Corridor A\n(W: 0.9m)", "coords": [10, 48, 70, 10], "color": "#ffaa00"},
        {"name": "Main Exit", "coords": [5, 49, 5, 8], "color": "#00ffaa"},
        {"name": "Fire Exit", "coords": [80, 49, 5, 8], "color": "#00ffaa"}
    ]
    
    for r in regions:
        box = get_box(r["coords"])
        draw.rectangle(box, outline=r["color"], width=3)
        inner_box = [box[0]+4, box[1]+4, box[2]-4, box[3]-4]
        draw.rectangle(inner_box, outline='#1c233a', width=1)
        draw.text((box[0] + 12, box[1] + 12), r["name"], fill='#8b9bb4')

    # Draw door swing details (diagonal lines/arcs)
    draw.line([(50, 370), (20, 340)], fill='#00ffaa', width=2)
    draw.line([(800, 370), (830, 340)], fill='#00ffaa', width=2)
    
    # Title Block
    draw.rectangle([650, 680, 970, 730], outline='#00d2ff', width=2)
    draw.text((665, 690), "BUILDSENSE DEMO SCHEMATIC (SAMPLE)", fill='#00d2ff')
    draw.text((665, 708), "SCALE: 1:50 | AREA: 2400 SQ FT | BYELAWS: NBC", fill='#8b9bb4')

    img.save(file_path)
    return file_path


# Generate the demo asset once at startup so the Load Demo Blueprint button
# can upload a real file. Failures are non-fatal — the button simply reports
# that the demo asset is unavailable.
try:
    DEMO_BLUEPRINT_PATH = _generate_demo_blueprint_asset()
except Exception as _demo_exc:  # pragma: no cover
    logger.warning("Demo blueprint asset unavailable: %s", _demo_exc)
    DEMO_BLUEPRINT_PATH = None

@app.route('/api/export', methods=['POST'])
def export_report():
    """Generates a JSON report from the last pipeline result."""
    data = request.json or {}
    pipeline_result = data.get('pipeline_result', {})
    report_title = data.get('report_title', 'BuildSense Analysis Report')

    if not pipeline_result:
        return jsonify({"error": "No pipeline_result provided"}), 400

    from agents.tools import tool_registry
    result = tool_registry.invoke(
        "generate_json_report",
        pipeline_result=pipeline_result,
        report_title=report_title
    )
    if result.get("status") == "success":
        return jsonify(result.get("output", {}))
    else:
        return jsonify({"error": result.get("error", "Export failed")}), 500


@app.route('/api/query', methods=['POST'])
def process_query():
    data = request.json or {}
    user_query = data.get('query', '').strip()
    image_path = data.get('image_path', '').strip()
    spatial_data = data.get('spatial_data')
    budget_limit_raw = data.get('budget_limit', None)
    site_city = data.get('site_city', 'Pune').strip()
    region = data.get('region', 'Pune').strip()
    style_preset = data.get('style_preset')
    if isinstance(style_preset, str):
        style_preset = style_preset.strip() or None
    else:
        style_preset = None
    start_date = data.get('start_date')
    if not isinstance(start_date, str) or not start_date.strip():
        start_date = date.today().isoformat()
    else:
        start_date = start_date.strip()
    project_id = data.get('project_id')
    if not isinstance(project_id, str) or not project_id.strip():
        project_id = None
    else:
        project_id = project_id.strip()
    
    if not user_query:
        return jsonify({"error": "Query cannot be empty"}), 400

    if not isinstance(spatial_data, dict):
        spatial_data = None
        
    # Budget parsing helper (e.g. ₹15 lakh -> 1500000, 1500000 -> 1500000)
    budget_limit = None
    if budget_limit_raw is not None:
        try:
            budget_limit = int(float(budget_limit_raw))
        except (ValueError, TypeError):
            pass
            
    # Try parsing budget from query text if not explicitly provided
    if budget_limit is None:
        # Detect '15 lakh' -> 1500000, etc.
        import re
        lakh_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:lakh|l)', user_query, re.IGNORECASE)
        if lakh_match:
            try:
                budget_limit = int(float(lakh_match.group(1)) * 100000)
            except ValueError:
                pass
        else:
            # Check for regular numbers e.g. 1500000 or 15,00,000
            numbers_match = re.findall(r'₹?\s*(\d[\d,\s]*)', user_query)
            for num_str in numbers_match:
                clean_num = num_str.replace(',', '').replace(' ', '')
                if clean_num:
                    try:
                        val = int(clean_num)
                        if val > 10000:  # Sensible minimum to ignore small counts
                            budget_limit = val
                            break
                    except ValueError:
                        pass

    # ── Resolve the blueprint for THIS user / conversation ──────────────
    # Multi-user isolation: the blueprint analysed for a follow-up must be
    # the one associated with the CURRENT conversation of the AUTHENTICATED
    # user — never a global folder scan (which would leak other users'
    # uploads into this query).
    #
    # Resolution order:
    #   1. explicit blueprint_id  (ownership-checked via db)
    #   2. latest blueprint in the current conversation
    #   3. user's latest blueprint
    user_ctx = get_current_user()
    conversation_ctx = _resolve_conversation_id(data.get('conversation_id'), user_ctx)

    if user_ctx:
        blueprint_row = None
        bp_id_raw = data.get('blueprint_id')
        if bp_id_raw:
            try:
                blueprint_row = db.get_blueprint(int(bp_id_raw), user_ctx['id'])
            except (TypeError, ValueError):
                blueprint_row = None

        if blueprint_row is None and conversation_ctx:
            conv_blueprints = db.get_blueprints(
                user_ctx['id'], conversation_id=conversation_ctx['id']
            )
            blueprint_row = conv_blueprints[0] if conv_blueprints else None

        if blueprint_row is None:
            user_blueprints = db.get_blueprints(user_ctx['id'])
            blueprint_row = user_blueprints[0] if user_blueprints else None

        if blueprint_row is not None:
            image_path = blueprint_row['file_path']
            data['blueprint_id'] = blueprint_row['id']
        elif not isinstance(spatial_data, dict):
            return jsonify({
                "error": "No blueprint uploaded yet for this account. "
                         "Upload a blueprint image first "
                         "(or use Load Demo Blueprint, which uploads a sample schematic)."
            }), 400
    else:
        # Unauthenticated session: only a path returned by this client's own
        # upload response may be used — there is no global fallback.
        # A request that carries its own spatial_data may still proceed
        # (the pipeline degrades honestly when no image exists); one with
        # nothing to ground on gets a clear error.
        has_own_spatial = isinstance(spatial_data, dict)
        if (not image_path or not os.path.exists(image_path)) and not has_own_spatial:
            return jsonify({
                "error": "No blueprint available for this request. "
                         "Log in and upload a blueprint image first."
            }), 400
                
    try:
        # Run orchestration
        logger.info(
            "Starting coordinator query: spatial_data_source=%s",
            "request" if spatial_data is not None else "blueprint_reanalysis",
        )

        # Persist the user question up-front so it survives even if the
        # pipeline fails mid-flight. The assistant reply is persisted after
        # the completed response below.
        persisted = _persist_exchange_messages(
            user_ctx, conversation_ctx, user_query, None,
            blueprint_id=data.get('blueprint_id'),
        )

        result = run_coordination_pipeline(
            image_path, user_query,
            spatial_data=spatial_data,
            budget_limit=budget_limit,
            site_city=site_city,
            region=region,
            style_preset=style_preset,
            start_date=start_date,
            project_id=project_id,
            user_id=user_ctx.get('id') if isinstance(user_ctx, dict) else None,
            conversation_id=conversation_ctx['id'] if conversation_ctx else data.get('conversation_id'),
            blueprint_id=data.get('blueprint_id'),
            plan_duration_months=_parse_duration_months(data.get('plan_duration_months')),
        )

        # Include budget limit in return
        result["budget_limit_parsed"] = budget_limit

        # Persist the completed assistant response so Recent Chats can later
        # reload BOTH sides of this exchange without regenerating anything.
        assistant_ids = _persist_exchange_messages(
            user_ctx, conversation_ctx, None,
            result.get('synthesized_recommendation'),
            blueprint_id=data.get('blueprint_id'),
        )
        persisted.update(assistant_ids)
        result["persisted"] = persisted

        logger.info(
            "Coordinator query completed: routed=%s, specialist_outputs=%s",
            result.get("routing_plan", []),
            list(result.get("specialist_outputs", {}).keys()),
        )
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Error running coordination pipeline: {str(e)}")
        system_metrics["failed_requests"] += 1
        return jsonify({"error": f"Error running coordination pipeline: {str(e)}"}), 500


@app.route('/api/contractors', methods=['GET'])
def list_contractors():
    """Return the enrolled contractor directory used by the Workforce Agent."""
    return jsonify({"contractors": db.list_contractors()})


@app.route('/api/contractors', methods=['POST'])
@login_required
def create_contractor():
    """Enroll a real contractor into the workforce directory."""
    user = get_current_user()
    data = request.json or {}
    name = str(data.get('name', '')).strip()
    if not name:
        return jsonify({"error": "Contractor name is required"}), 400
    rating = data.get('rating')
    if rating is not None:
        try:
            rating = float(rating)
            if not 0 <= rating <= 5:
                return jsonify({"error": "Rating must be between 0 and 5"}), 400
        except (TypeError, ValueError):
            return jsonify({"error": "Rating must be a number"}), 400
    daily_rate = data.get('daily_rate_inr')
    if daily_rate is not None:
        try:
            daily_rate = float(daily_rate)
        except (TypeError, ValueError):
            return jsonify({"error": "daily_rate_inr must be a number"}), 400
    capacity = data.get('capacity_workers', 0)
    try:
        capacity = int(capacity or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "capacity_workers must be an integer"}), 400
    try:
        contractor = db.add_contractor(
            name=name,
            trade=str(data.get('trade', '')).strip(),
            rating=rating,
            location=str(data.get('location', '')).strip(),
            capacity_workers=capacity,
            daily_rate_inr=daily_rate,
            status=str(data.get('status', 'Available')).strip() or 'Available',
            phone=str(data.get('phone', '')).strip(),
            notes=str(data.get('notes', '')).strip(),
            enrolled_by_user_id=user['id'],
        )
    except Exception as exc:
        # Unique constraint on name → already exists
        if 'UNIQUE' in str(exc).upper():
            return jsonify({"error": "A contractor with that name already exists"}), 409
        raise
    return jsonify({"contractor": contractor}), 201


@app.route('/api/contractors/<int:contractor_id>', methods=['DELETE'])
@login_required
def delete_contractor(contractor_id):
    """Remove a contractor from the directory."""
    deleted = db.delete_contractor(contractor_id)
    if not deleted:
        return jsonify({"error": "Contractor not found"}), 404
    return jsonify({"success": True})


# ═══════════════════════════════════════════════════════════════════════════════
# Conversation & Message APIs (database-backed, per-user)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/conversations', methods=['GET'])
@login_required
def list_conversations():
    user = get_current_user()
    convs = db.get_conversations(user['id'])
    # Annotate each conversation with blueprint presence so the client can
    # display a blueprint indicator in the Recent Chats panel without an
    # extra per-row round-trip.
    for c in convs:
        bps = db.get_blueprints(user['id'], conversation_id=c['id'])
        c['has_blueprint'] = bool(bps)
        c['blueprint_filename'] = bps[0]['filename'] if bps else None
    return jsonify({"conversations": convs})


@app.route('/api/conversations', methods=['POST'])
@login_required
def create_conversation():
    user = get_current_user()
    data = request.json or {}
    title = data.get('title', 'New Chat')
    conv = db.create_conversation(user['id'], title)
    return jsonify({"conversation": conv}), 201


@app.route('/api/conversations/<int:conv_id>', methods=['GET'])
@login_required
def get_conversation(conv_id):
    user = get_current_user()
    conv = db.get_conversation(conv_id, user['id'])
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404
    messages = db.get_messages(conv_id)
    return jsonify({"conversation": conv, "messages": messages})


@app.route('/api/conversations/<int:conv_id>', methods=['PUT'])
@login_required
def update_conversation(conv_id):
    user = get_current_user()
    data = request.json or {}
    conv = db.update_conversation(conv_id, user['id'], **data)
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify({"conversation": conv})


@app.route('/api/conversations/<int:conv_id>', methods=['DELETE'])
@login_required
def delete_conversation(conv_id):
    user = get_current_user()
    db.delete_conversation(conv_id, user['id'])
    return jsonify({"success": True})


@app.route('/api/conversations/<int:conv_id>/messages', methods=['GET'])
@login_required
def list_messages(conv_id):
    user = get_current_user()
    conv = db.get_conversation(conv_id, user['id'])
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404
    messages = db.get_messages(conv_id)
    return jsonify({"messages": messages})


@app.route('/api/conversations/<int:conv_id>/messages', methods=['POST'])
@login_required
def add_message(conv_id):
    user = get_current_user()
    conv = db.get_conversation(conv_id, user['id'])
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404
    data = request.json or {}
    role = data.get('role', 'user')
    content = data.get('content', '')
    metadata = data.get('metadata')
    msg = db.add_message(conv_id, role, content, metadata)
    return jsonify({"message": msg}), 201


@app.route('/api/conversations/<int:conv_id>/messages/<int:msg_id>', methods=['DELETE'])
@login_required
def delete_messages_from(conv_id, msg_id):
    """Delete a message and every later message in the conversation.

    Used when an earlier user question is edited and resubmitted so the
    stored history stays consistent with what is on screen.
    """
    user = get_current_user()
    conv = db.get_conversation(conv_id, user['id'])
    if not conv:
        return jsonify({"error": "Conversation not found"}), 404
    deleted = db.delete_messages_from(conv_id, msg_id)
    return jsonify({"success": True, "deleted": deleted})


# ═══════════════════════════════════════════════════════════════════════════════
# Blueprint Persistence APIs
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/blueprints', methods=['GET'])
@login_required
def list_blueprints():
    user = get_current_user()
    conv_id = request.args.get('conversation_id', type=int)
    bps = db.get_blueprints(user['id'], conv_id)
    return jsonify({"blueprints": bps})


@app.route('/api/blueprints/<int:bp_id>', methods=['GET'])
@login_required
def get_blueprint(bp_id):
    user = get_current_user()
    bp = db.get_blueprint(bp_id, user['id'])
    if not bp:
        return jsonify({"error": "Blueprint not found"}), 404
    analysis = db.get_latest_analysis(bp_id)
    return jsonify({"blueprint": bp, "analysis": analysis})


@app.route('/api/blueprints/<int:bp_id>/analysis', methods=['GET'])
@login_required
def get_blueprint_analysis(bp_id):
    user = get_current_user()
    bp = db.get_blueprint(bp_id, user['id'])
    if not bp:
        return jsonify({"error": "Blueprint not found"}), 404
    analysis = db.get_latest_analysis(bp_id)
    if not analysis:
        return jsonify({"error": "No analysis found"}), 404
    return jsonify({"analysis": analysis})


# ═══════════════════════════════════════════════════════════════════════════════
# Per-User Memory APIs
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/user/memory', methods=['GET'])
@login_required
def get_user_memory():
    user = get_current_user()
    topic = request.args.get('topic')
    memories = db.get_memories(user['id'], topic)
    return jsonify({"memories": memories})


@app.route('/api/user/memory', methods=['POST'])
@login_required
def upsert_user_memory():
    user = get_current_user()
    data = request.json or {}
    topic = data.get('topic', 'general')
    key = data.get('key', '')
    value = data.get('value', '')
    if not key:
        return jsonify({"error": "Key is required"}), 400
    mem = db.upsert_memory(user['id'], topic, key, value)
    return jsonify({"memory": mem}), 201


@app.route('/api/user/memory/<int:mem_id>', methods=['DELETE'])
@login_required
def delete_user_memory(mem_id):
    user = get_current_user()
    db.delete_memory(mem_id, user['id'])
    return jsonify({"success": True})


# ═══════════════════════════════════════════════════════════════════════════════
# Calendar Events API (database-backed, per-user)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/user/calendar', methods=['GET'])
@login_required
def get_user_calendar():
    user = get_current_user()
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    events = db.get_calendar_events(user['id'], start_date, end_date)
    return jsonify({"events": events})


@app.route('/api/user/calendar', methods=['POST'])
@login_required
def create_user_calendar_event():
    user = get_current_user()
    data = request.json or {}
    required = ['title', 'date']
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"'{field}' is required"}), 400
    event = db.create_calendar_event(user['id'], **data)
    return jsonify({"event": event}), 201


@app.route('/api/user/calendar/<int:event_id>', methods=['GET'])
@login_required
def get_user_calendar_event(event_id):
    user = get_current_user()
    event = db.get_calendar_event(event_id, user['id'])
    if not event:
        return jsonify({"error": "Event not found"}), 404
    return jsonify({"event": event})


@app.route('/api/user/calendar/<int:event_id>', methods=['PUT'])
@login_required
def update_user_calendar_event(event_id):
    user = get_current_user()
    data = request.json or {}
    event = db.update_calendar_event(event_id, user['id'], **data)
    if not event:
        return jsonify({"error": "Event not found"}), 404
    return jsonify({"event": event})


@app.route('/api/user/calendar/<int:event_id>', methods=['DELETE'])
@login_required
def delete_user_calendar_event(event_id):
    user = get_current_user()
    db.delete_calendar_event(event_id, user['id'])
    return jsonify({"success": True})


if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    logger.info("Starting BuildSense server on port %d...", port)
    app.run(host='0.0.0.0', port=port, debug=True)

"""Durable local calendar storage with safe external-calendar fallback."""

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone


logger = logging.getLogger(__name__)

CALENDAR_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory"
)
CALENDAR_FILE = os.path.join(CALENDAR_DIR, "calendar_events.json")


class ExternalCalendarError(RuntimeError):
    """Normalised external calendar failure with an optional HTTP status."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def _is_external_unavailable(error):
    """Recognise authentication, quota, and rate-limit failures safely."""
    status_code = getattr(error, "status_code", None)
    message = str(error).lower()
    return status_code in {401, 403, 429} or any(
        token in message
        for token in ("quota", "rate limit", "rate-limit", "api limit", "401", "403", "429")
    )


class LocalCalendarStore:
    """JSON-backed event store; deterministic IDs make retries idempotent."""

    def __init__(self, filepath=CALENDAR_FILE):
        self._filepath = filepath
        self._lock = threading.RLock()
        self._events = []
        self._load()

    def _load(self):
        os.makedirs(os.path.dirname(self._filepath), exist_ok=True)
        if not os.path.exists(self._filepath):
            return
        try:
            with open(self._filepath, "r", encoding="utf-8") as file:
                self._events = json.load(file).get("events", [])
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Calendar store could not load %s: %s", self._filepath, exc)
            self._events = []

    def _save(self):
        os.makedirs(os.path.dirname(self._filepath), exist_ok=True)
        with open(self._filepath, "w", encoding="utf-8") as file:
            json.dump({"events": self._events}, file, indent=2, ensure_ascii=False)

    @staticmethod
    def _event_id(event):
        source = event.get("source_id") or "|".join(
            str(event.get(key, ""))
            for key in ("title", "date", "start_time", "end_time", "description")
        )
        return "cal_" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _validate(event):
        if not isinstance(event, dict):
            raise ValueError("Calendar event must be an object.")
        for field in ("title", "date"):
            if not str(event.get(field, "")).strip():
                raise ValueError(f"Calendar event requires {field}.")
        datetime.strptime(str(event["date"]), "%Y-%m-%d")
        if not event.get("start_time"):
            event["start_time"] = "09:00"
        if not event.get("end_time") and not event.get("duration_minutes"):
            event["duration_minutes"] = 60
        return event

    def upsert(self, event):
        event = self._validate(dict(event))
        event_id = self._event_id(event)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            for existing in self._events:
                if existing["id"] == event_id:
                    existing.update(event)
                    existing["updated_at"] = now
                    self._save()
                    logger.info("Calendar event updated locally: id=%s title=%s", event_id, event["title"])
                    return existing, False

            stored = dict(event)
            stored.update({
                "id": event_id,
                "title": str(event["title"]).strip(),
                "date": str(event["date"]),
                "start_time": str(event["start_time"]),
                "end_time": event.get("end_time"),
                "duration_minutes": event.get("duration_minutes"),
                "description": event.get("description", ""),
                "project_context": event.get("project_context", ""),
                "source_id": event.get("source_id"),
                "external_event_id": event.get("external_event_id"),
                "sync_state": event.get("sync_state", "pending"),
                "created_at": now,
                "updated_at": now,
            })
            self._events.append(stored)
            self._save()
            logger.info("Calendar event created locally: id=%s title=%s", event_id, stored["title"])
            return stored, True

    def list_events(self, start_date=None, end_date=None, project_id=None, include_legacy=False):
        with self._lock:
            events = list(self._events)
        if start_date:
            events = [event for event in events if event["date"] >= start_date]
        if end_date:
            events = [event for event in events if event["date"] <= end_date]
        logger.info("Calendar events retrieved: count=%d", len(events))
        return sorted(events, key=lambda event: (event["date"], event["start_time"], event["id"]))


class CalendarService:
    """Writes locally first and optionally mirrors events to an external client."""

    def __init__(self, store=None, external_client=None):
        self.store = store or LocalCalendarStore()
        self.external_client = external_client

    def create_event(self, event):
        stored, created = self.store.upsert(event)
        if self.external_client is None:
            return {
                "calendar_status": "local_fallback",
                "message": "External calendar unavailable; event saved locally.",
                "event": stored,
                "created": created,
            }

        try:
            external_id = self.external_client.create_or_update(stored)
            stored, _ = self.store.upsert({
                **stored,
                "external_event_id": external_id,
                "sync_state": "synced",
            })
            logger.info("Calendar event synchronized externally: id=%s", stored["id"])
            return {
                "calendar_status": "external_synced",
                "message": "Event saved locally and synchronized externally.",
                "event": stored,
                "created": created,
            }
        except Exception as exc:
            status = "local_fallback" if _is_external_unavailable(exc) else "local_fallback"
            logger.warning("External calendar failed; local event retained: %s", exc)
            return {
                "calendar_status": status,
                "message": "External calendar unavailable; event saved locally.",
                "event": stored,
                "created": created,
                "external_error": str(exc),
            }

    def list_events(self, start_date=None, end_date=None, project_id=None, include_legacy=False):
        return self.store.list_events(start_date, end_date, project_id, include_legacy)

    def sync_pending(self):
        if self.external_client is None:
            return {"calendar_status": "local_fallback", "synced": 0, "message": "External calendar unavailable; local events remain saved."}
        synced = 0
        for event in self.store.list_events():
            if event.get("sync_state") == "synced":
                continue
            result = self.create_event(event)
            if result["calendar_status"] == "external_synced":
                synced += 1
        return {"calendar_status": "external_synced", "synced": synced}


calendar_service = CalendarService()

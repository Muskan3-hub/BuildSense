"""
BuildSense — Authentication & Role Management

Handles user authentication, registration, and role-based access control.
Uses SQLite via agents.database for persistent user storage.
Passwords are hashed using werkzeug.security.
"""

import os
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from flask import session, jsonify

from agents.database import db

# ── Demo seed users (development convenience ONLY) ─────────────
# These fictional accounts are NEVER created automatically. Set
# BUILDSENSE_SEED_DEMO_USERS=1 explicitly to provision them in a
# local development database.
DEMO_USERS = [
    {"username": "admin",   "password": "admin123",   "role": "user",       "name": "Project Admin",  "email": None},
    {"username": "ramesh",  "password": "ramesh123",  "role": "contractor", "name": "Ramesh Sharma",  "email": None},
    {"username": "verma",   "password": "verma123",   "role": "contractor", "name": "Ramesh Verma",   "email": None},
    {"username": "sunil",   "password": "sunil123",   "role": "coworker",   "name": "Sunil Kumar",    "email": None},
    {"username": "manoj",   "password": "manoj123",   "role": "coworker",   "name": "Manoj Singh",    "email": None},
    {"username": "anil",    "password": "anil123",    "role": "coworker",   "name": "Anil Das",       "email": None},
]


def _seed_demo_users_if_enabled():
    """Insert demo users only when explicitly requested via environment."""
    if os.getenv("BUILDSENSE_SEED_DEMO_USERS", "").strip().lower() not in ("1", "true", "yes"):
        return
    for u in DEMO_USERS:
        if not db.username_exists(u["username"]):
            db.create_user(
                username=u["username"],
                email=u.get("email"),
                password_hash=generate_password_hash(u["password"]),
                name=u["name"],
                role=u["role"],
            )


_seed_demo_users_if_enabled()


# ── Authentication ──────────────────────────────────────────────

def authenticate(username: str, password: str) -> dict | None:
    """
    Verify credentials against the database.
    Returns {"username", "role", "name", "id"} on success, None on failure.
    """
    user = db.get_user_by_username(username)
    if user and check_password_hash(user["password_hash"], password):
        return {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "name": user["name"],
        }
    return None


def register_user(
    username: str,
    password: str,
    name: str = "",
    email: str = "",
    role: str = "user",
) -> dict | None:
    """
    Register a new user.  Returns the created user dict or None on failure.
    Validates username uniqueness and email uniqueness.
    """
    username = username.strip()
    if not username or not password:
        return None

    if db.username_exists(username):
        return None

    if email and db.email_exists(email):
        return None

    return db.create_user(
        username=username,
        email=email.strip() if email else None,
        password_hash=generate_password_hash(password),
        name=name.strip(),
        role=role,
    )


# ── Session helpers ─────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user" not in session:
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated_function


def get_current_user():
    return session.get("user")

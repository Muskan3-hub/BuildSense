"""
BuildSense — SQLite Database Module

Provides persistent storage for users, conversations, messages,
blueprints, analyses, memory, and calendar events.
The database file is stored in `database/buildsense.db` at the project root.
"""

import os
import sqlite3
import threading
from datetime import datetime, timezone

# Database path: <project_root>/database/buildsense.db
_DB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "database",
)
_DB_PATH = os.path.join(_DB_DIR, "buildsense.db")


class Database:
    """
    Thread-safe SQLite wrapper for BuildSense persistence.
    """

    def __init__(self, db_path: str = _DB_PATH):
        self._db_path = db_path
        self._local = threading.local()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()

    # ── Connection helpers ──────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    # ── Schema ──────────────────────────────────────────────────

    def _init_schema(self) -> None:
        conn = self._get_conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT    UNIQUE NOT NULL,
                email           TEXT    UNIQUE,
                password_hash   TEXT    NOT NULL,
                name            TEXT    NOT NULL DEFAULT '',
                role            TEXT    NOT NULL DEFAULT 'user',
                created_at      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                title           TEXT    NOT NULL DEFAULT 'New Chat',
                is_pinned       INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);

            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role            TEXT    NOT NULL DEFAULT 'user',
                content         TEXT    NOT NULL DEFAULT '',
                metadata        TEXT    DEFAULT NULL,
                created_at      TEXT    NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);

            CREATE TABLE IF NOT EXISTS blueprints (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                conversation_id INTEGER DEFAULT NULL,
                filename        TEXT    NOT NULL,
                file_path       TEXT    NOT NULL,
                created_at      TEXT    NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_bp_user ON blueprints(user_id);

            CREATE TABLE IF NOT EXISTS blueprint_analyses (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                blueprint_id    INTEGER NOT NULL,
                spatial_data    TEXT    NOT NULL DEFAULT '{}',
                created_at      TEXT    NOT NULL,
                FOREIGN KEY (blueprint_id) REFERENCES blueprints(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_bpa_bp ON blueprint_analyses(blueprint_id);

            CREATE TABLE IF NOT EXISTS memories (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                topic           TEXT    NOT NULL DEFAULT 'general',
                key             TEXT    NOT NULL,
                value           TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_mem_user ON memories(user_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_mem_user_topic_key ON memories(user_id, topic, key);

            CREATE TABLE IF NOT EXISTS calendar_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                title           TEXT    NOT NULL,
                description     TEXT    DEFAULT '',
                date            TEXT    NOT NULL,
                start_time      TEXT    DEFAULT '09:00',
                end_time        TEXT    DEFAULT NULL,
                all_day         INTEGER DEFAULT 0,
                location        TEXT    DEFAULT '',
                category        TEXT    DEFAULT 'general',
                reminder_minutes INTEGER DEFAULT NULL,
                recurrence_rule TEXT    DEFAULT NULL,
                blueprint_id    INTEGER DEFAULT NULL,
                conversation_id INTEGER DEFAULT NULL,
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (blueprint_id) REFERENCES blueprints(id) ON DELETE SET NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cal_user ON calendar_events(user_id);
            CREATE INDEX IF NOT EXISTS idx_cal_date ON calendar_events(date);

            CREATE TABLE IF NOT EXISTS agent_activity (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                conversation_id INTEGER DEFAULT NULL,
                blueprint_id    INTEGER DEFAULT NULL,
                activity_type   TEXT    NOT NULL,
                description     TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE SET NULL,
                FOREIGN KEY (blueprint_id) REFERENCES blueprints(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_act_user ON agent_activity(user_id);
            CREATE INDEX IF NOT EXISTS idx_act_created ON agent_activity(created_at);

            CREATE TABLE IF NOT EXISTS contractors (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                name                TEXT    UNIQUE NOT NULL,
                trade               TEXT    NOT NULL DEFAULT '',
                rating              REAL    DEFAULT NULL,
                location            TEXT    DEFAULT '',
                capacity_workers    INTEGER DEFAULT 0,
                daily_rate_inr      REAL    DEFAULT NULL,
                status              TEXT    NOT NULL DEFAULT 'Available',
                phone               TEXT    DEFAULT '',
                notes               TEXT    DEFAULT '',
                enrolled_by_user_id INTEGER DEFAULT NULL,
                created_at          TEXT    NOT NULL,
                updated_at          TEXT    NOT NULL,
                FOREIGN KEY (enrolled_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_contractor_trade ON contractors(trade);
            """
        )
        conn.commit()

        # Migration: add columns to users table if missing
        try:
            conn.execute("SELECT updated_at FROM users LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE users ADD COLUMN updated_at TEXT DEFAULT NULL")
            conn.commit()

        # Migration: add is_pinned to conversations table if missing
        conv_cols = [r["name"] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()]
        if "is_pinned" not in conv_cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0")
            conn.commit()

    # ── User CRUD ───────────────────────────────────────────────

    def create_user(
        self,
        username: str,
        email: str,
        password_hash: str,
        name: str = "",
        role: str = "user",
    ) -> dict | None:
        """
        Insert a new user.  Returns the created user dict or None on failure.
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                """
                INSERT INTO users (username, email, password_hash, name, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (username.strip(), email.strip() if email else None,
                 password_hash, name.strip(), role, now),
            )
            conn.commit()
            return self.get_user_by_id(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None

    def get_user_by_username(self, username: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip(),)
        ).fetchone()
        return dict(row) if row else None

    def get_user_by_email(self, email: str) -> dict | None:
        if not email:
            return None
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.strip(),)
        ).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def username_exists(self, username: str) -> bool:
        return self.get_user_by_username(username) is not None

    def email_exists(self, email: str) -> bool:
        if not email:
            return False
        return self.get_user_by_email(email) is not None

    # ── Conversations ─────────────────────────────────────────

    def create_conversation(self, user_id: int, title: str = "New Chat") -> dict:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        cursor = conn.execute(
            "INSERT INTO conversations (user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, title, now, now),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM conversations WHERE id = ?", (cursor.lastrowid,)).fetchone())

    def get_conversations(self, user_id: int) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM conversations WHERE user_id = ? ORDER BY is_pinned DESC, updated_at DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self, conv_id: int, user_id: int) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id)
        ).fetchone()
        return dict(row) if row else None

    def update_conversation(self, conv_id: int, user_id: int, **kwargs) -> dict | None:
        allowed = {"title", "is_pinned"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.get_conversation(conv_id, user_id)
        if "is_pinned" in updates:
            updates["is_pinned"] = 1 if updates["is_pinned"] else 0
        # Only a title change counts as activity; pinning must not reorder chats
        if "title" in updates:
            now = datetime.now(timezone.utc).isoformat()
            updates["updated_at"] = now
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [conv_id, user_id]
        conn = self._get_conn()
        conn.execute(f"UPDATE conversations SET {set_clause} WHERE id = ? AND user_id = ?", vals)
        conn.commit()
        return self.get_conversation(conv_id, user_id)

    def delete_conversation(self, conv_id: int, user_id: int) -> bool:
        conn = self._get_conn()
        conn.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id))
        conn.commit()
        return conn.total_changes > 0

    def touch_conversation(self, conv_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id))
        conn.commit()

    # ── Messages ───────────────────────────────────────────────

    def add_message(self, conversation_id: int, role: str, content: str, metadata: dict | None = None) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        import json
        meta_json = json.dumps(metadata) if metadata else None
        conn = self._get_conn()
        cursor = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
            (conversation_id, role, content, meta_json, now),
        )
        conn.commit()
        self.touch_conversation(conversation_id)
        return dict(conn.execute("SELECT * FROM messages WHERE id = ?", (cursor.lastrowid,)).fetchone())

    def get_messages(self, conversation_id: int) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC", (conversation_id,)
        ).fetchall()
        result = []
        import json
        for r in rows:
            d = dict(r)
            if d.get("metadata"):
                try:
                    d["metadata"] = json.loads(d["metadata"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(d)
        return result

    def delete_messages_from(self, conversation_id: int, first_msg_id: int) -> int:
        """Delete a message and all subsequent messages in the conversation.

        Used to keep the database consistent when an earlier user message is
        edited and resubmitted. Returns the number of deleted rows.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND id >= ?",
            (conversation_id, first_msg_id),
        )
        conn.commit()
        if cursor.rowcount > 0:
            self.touch_conversation(conversation_id)
        return cursor.rowcount

    # ── Blueprints ─────────────────────────────────────────────

    def create_blueprint(self, user_id: int, conversation_id: int | None, filename: str, file_path: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        cursor = conn.execute(
            "INSERT INTO blueprints (user_id, conversation_id, filename, file_path, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, conversation_id, filename, file_path, now),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM blueprints WHERE id = ?", (cursor.lastrowid,)).fetchone())

    def get_blueprints(self, user_id: int, conversation_id: int | None = None) -> list:
        conn = self._get_conn()
        if conversation_id:
            rows = conn.execute(
                "SELECT * FROM blueprints WHERE user_id = ? AND conversation_id = ? ORDER BY created_at DESC",
                (user_id, conversation_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM blueprints WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_blueprint(self, bp_id: int, user_id: int) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM blueprints WHERE id = ? AND user_id = ?", (bp_id, user_id)
        ).fetchone()
        return dict(row) if row else None

    # ── Blueprint Analyses ─────────────────────────────────────

    def create_analysis(self, blueprint_id: int, spatial_data: dict) -> dict:
        import json
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        cursor = conn.execute(
            "INSERT INTO blueprint_analyses (blueprint_id, spatial_data, created_at) VALUES (?, ?, ?)",
            (blueprint_id, json.dumps(spatial_data), now),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM blueprint_analyses WHERE id = ?", (cursor.lastrowid,)).fetchone())

    def get_latest_analysis(self, blueprint_id: int) -> dict | None:
        import json
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM blueprint_analyses WHERE blueprint_id = ? ORDER BY created_at DESC LIMIT 1",
            (blueprint_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["spatial_data"] = json.loads(d["spatial_data"])
        except (json.JSONDecodeError, TypeError):
            d["spatial_data"] = {}
        return d

    # ── Memories (per-user) ───────────────────────────────────

    def get_memories(self, user_id: int, topic: str | None = None) -> list:
        conn = self._get_conn()
        if topic:
            rows = conn.execute(
                "SELECT * FROM memories WHERE user_id = ? AND topic = ? ORDER BY updated_at DESC",
                (user_id, topic),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM memories WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_memory(self, user_id: int, topic: str, key: str, value: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        existing = conn.execute(
            "SELECT id FROM memories WHERE user_id = ? AND topic = ? AND key = ?",
            (user_id, topic, key),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE memories SET value = ?, updated_at = ? WHERE id = ?",
                (value, now, existing["id"]),
            )
            conn.commit()
            return dict(conn.execute("SELECT * FROM memories WHERE id = ?", (existing["id"],)).fetchone())
        cursor = conn.execute(
            "INSERT INTO memories (user_id, topic, key, value, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, topic, key, value, now, now),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM memories WHERE id = ?", (cursor.lastrowid,)).fetchone())

    def delete_memory(self, mem_id: int, user_id: int) -> bool:
        conn = self._get_conn()
        conn.execute("DELETE FROM memories WHERE id = ? AND user_id = ?", (mem_id, user_id))
        conn.commit()
        return conn.total_changes > 0

    # ── Calendar Events (per-user, DB-backed) ─────────────────

    def create_calendar_event(self, user_id: int, **kwargs) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO calendar_events
               (user_id, title, description, date, start_time, end_time, all_day,
                location, category, reminder_minutes, recurrence_rule,
                blueprint_id, conversation_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                kwargs.get("title", ""),
                kwargs.get("description", ""),
                kwargs.get("date", ""),
                kwargs.get("start_time", "09:00"),
                kwargs.get("end_time"),
                1 if kwargs.get("all_day") else 0,
                kwargs.get("location", ""),
                kwargs.get("category", "general"),
                kwargs.get("reminder_minutes"),
                kwargs.get("recurrence_rule"),
                kwargs.get("blueprint_id"),
                kwargs.get("conversation_id"),
                now, now,
            ),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM calendar_events WHERE id = ?", (cursor.lastrowid,)).fetchone())

    def get_calendar_events(self, user_id: int, start_date: str | None = None, end_date: str | None = None) -> list:
        conn = self._get_conn()
        if start_date and end_date:
            rows = conn.execute(
                "SELECT * FROM calendar_events WHERE user_id = ? AND date >= ? AND date <= ? ORDER BY date, start_time",
                (user_id, start_date, end_date),
            ).fetchall()
        elif start_date:
            rows = conn.execute(
                "SELECT * FROM calendar_events WHERE user_id = ? AND date >= ? ORDER BY date, start_time",
                (user_id, start_date),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM calendar_events WHERE user_id = ? ORDER BY date, start_time",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_events_by_conversation(
        self,
        user_id: int,
        conversation_id: int | str | None = None,
    ) -> int:
        """Aggressive deletion of old construction_plan events before saving a new plan."""
        conn = self._get_conn()
        if conversation_id is not None and conversation_id != "":
            query = (
                "DELETE FROM calendar_events "
                "WHERE user_id = ? AND category = 'construction_plan' "
                "AND (conversation_id = ? OR CAST(conversation_id AS TEXT) = ? OR conversation_id IS NULL OR conversation_id = '')"
            )
            params = (user_id, conversation_id, str(conversation_id))
        else:
            query = (
                "DELETE FROM calendar_events "
                "WHERE user_id = ? AND category = 'construction_plan'"
            )
            params = (user_id,)

        cursor = conn.execute(query, params)
        conn.commit()
        return cursor.rowcount

    def delete_calendar_events_by_category(
        self,
        user_id: int,
        category: str,
        conversation_id: int | str | None = None,
        scoped: bool = False,
    ) -> int:
        """Deletes THIS USER's calendar events in a category. Returns row count."""
        conn = self._get_conn()
        query = "DELETE FROM calendar_events WHERE user_id = ? AND category = ?"
        params: list = [user_id, category]
        if scoped:
            if conversation_id is None or conversation_id == "":
                query += " AND (conversation_id IS NULL OR conversation_id = '')"
            else:
                query += " AND (conversation_id = ? OR CAST(conversation_id AS TEXT) = ? OR conversation_id IS NULL OR conversation_id = '')"
                params.extend([conversation_id, str(conversation_id)])
        else:
            if conversation_id is not None and conversation_id != "":
                query += " AND (conversation_id = ? OR CAST(conversation_id AS TEXT) = ?)"
                params.extend([conversation_id, str(conversation_id)])
        cursor = conn.execute(query, tuple(params))
        conn.commit()
        return cursor.rowcount

    def get_calendar_event(self, event_id: int, user_id: int) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM calendar_events WHERE id = ? AND user_id = ?", (event_id, user_id)
        ).fetchone()
        return dict(row) if row else None

    def update_calendar_event(self, event_id: int, user_id: int, **kwargs) -> dict | None:
        allowed = {"title", "description", "date", "start_time", "end_time", "all_day",
                   "location", "category", "reminder_minutes", "recurrence_rule",
                   "blueprint_id", "conversation_id"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.get_calendar_event(event_id, user_id)
        now = datetime.now(timezone.utc).isoformat()
        updates["updated_at"] = now
        if "all_day" in updates:
            updates["all_day"] = 1 if updates["all_day"] else 0
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [event_id, user_id]
        conn = self._get_conn()
        conn.execute(f"UPDATE calendar_events SET {set_clause} WHERE id = ? AND user_id = ?", vals)
        conn.commit()
        return self.get_calendar_event(event_id, user_id)

    def delete_calendar_event(self, event_id: int, user_id: int) -> bool:
        conn = self._get_conn()
        conn.execute("DELETE FROM calendar_events WHERE id = ? AND user_id = ?", (event_id, user_id))
        conn.commit()
        return conn.total_changes > 0

    # ── Agent Activity (per-user) ─────────────────────────────

    def add_activity(self, user_id: int, activity_type: str, description: str,
                     conversation_id: int | None = None, blueprint_id: int | None = None) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO agent_activity
               (user_id, conversation_id, blueprint_id, activity_type, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, conversation_id, blueprint_id, activity_type, description, now),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM agent_activity WHERE id = ?", (cursor.lastrowid,)).fetchone())

    def get_activities(self, user_id: int, limit: int = 10) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM agent_activity WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Contractor Directory (shared, real enrollment records) ──

    def list_contractors(self) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM contractors ORDER BY name ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_contractor(self, contractor_id: int) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM contractors WHERE id = ?", (contractor_id,)
        ).fetchone()
        return dict(row) if row else None

    def add_contractor(
        self,
        name: str,
        trade: str = "",
        rating: float | None = None,
        location: str = "",
        capacity_workers: int = 0,
        daily_rate_inr: float | None = None,
        status: str = "Available",
        phone: str = "",
        notes: str = "",
        enrolled_by_user_id: int | None = None,
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO contractors
               (name, trade, rating, location, capacity_workers, daily_rate_inr,
                status, phone, notes, enrolled_by_user_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, trade, rating, location, capacity_workers, daily_rate_inr,
             status, phone, notes, enrolled_by_user_id, now, now),
        )
        conn.commit()
        return self.get_contractor(cursor.lastrowid)

    def delete_contractor(self, contractor_id: int) -> bool:
        conn = self._get_conn()
        conn.execute("DELETE FROM contractors WHERE id = ?", (contractor_id,))
        conn.commit()
        return conn.total_changes > 0


# Module-level singleton
db = Database()

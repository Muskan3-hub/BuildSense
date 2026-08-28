"""
BuildSense — Memory Management System
Agent Coordination & Memory Management

Implements the dual-memory architecture required for contextual decision-making:

1. ConversationMemory  — Short-term sliding window (last N interactions),
   ISOLATED PER AUTHENTICATED USER.
   Enables follow-up queries to reference prior agent outputs without the
   user needing to repeat context.

2. KnowledgeStore      — Long-term project knowledge persisted to a JSON file,
   ISOLATED PER AUTHENTICATED USER.
   Stores design preferences, learned constraints, blueprint metadata, and
   past recommendations so they survive server restarts.

3. SharedMemoryBus     — Key-value store that agents read/write during a
   single pipeline run.  A FRESH INSTANCE is created per pipeline run so
   concurrent runs can never read each other's data.

SECURITY MODEL (multi-user isolation)
-------------------------------------
Every memory record belongs to exactly one user scope.  All public read and
write methods require the caller to pass the ``user_id`` they act on behalf
of; data is never served across scopes.  Two reserved scopes exist:

``_anonymous``
    Used when no authenticated user is available (CLI/tools).  Its contents
    are NEVER visible to any authenticated user.

``_legacy``
    Owner-less entries found in a pre-isolation storage file.  They are kept
    on disk but SEALED: no user-facing API can ever return them.

Isolation is enforced here at the storage layer — callers cannot opt out.
"""

import os
import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# Reserved scopes that are never owned by an authenticated account.
ANONYMOUS_SCOPE = "_anonymous"
LEGACY_SCOPE = "_legacy"

_RESERVED_SCOPES = {ANONYMOUS_SCOPE, LEGACY_SCOPE}


def _scope_for(user_id) -> str:
    """Map a caller-supplied user id to its storage scope key."""
    if user_id is None:
        return ANONYMOUS_SCOPE
    if isinstance(user_id, str) and user_id in _RESERVED_SCOPES:
        raise ValueError("Reserved scope names cannot be used as a user id.")
    return str(user_id)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Short-Term Conversation Memory
# ═══════════════════════════════════════════════════════════════════════════════

class ConversationMemory:
    """
    Maintains a fixed-size sliding window of recent conversation turns,
    strictly partitioned per user scope.

    Each turn is a dict:
        {
            "role": "user" | "assistant",
            "content": str,
            "timestamp": ISO-8601 str,
            "metadata": dict          # optional — routing plan, agent list, etc.
        }

    Thread-safe via a reentrant lock so the Flask request threads don't
    corrupt the windows during concurrent reads/writes.
    """

    def __init__(self, max_turns: int = 10):
        self._turns: Dict[str, List[dict]] = {}
        self._max_turns = max_turns
        self._lock = threading.RLock()

    def add_turn(self, role: str, content: str, metadata: Optional[dict] = None,
                 user_id=None) -> None:
        """Append a turn to THIS USER's window, evicting the oldest at capacity."""
        scope = _scope_for(user_id)
        with self._lock:
            window = self._turns.setdefault(scope, [])
            entry = {
                "role": role,
                "content": content[:2000],  # cap length to prevent memory bloat
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metadata": metadata or {}
            }
            window.append(entry)
            # Evict oldest turns beyond the window
            if len(window) > self._max_turns:
                self._turns[scope] = window[-self._max_turns:]

    def get_history(self, user_id=None) -> List[dict]:
        """Returns THIS USER's conversation history (oldest → newest)."""
        scope = _scope_for(user_id)
        with self._lock:
            return list(self._turns.get(scope, []))

    def get_context_summary(self, last_n: int = 5, user_id=None) -> str:
        """
        Returns a plain-text summary of THIS USER's last N turns, suitable for
        injection into an LLM prompt to provide conversational context.
        Never includes any other user's turns.
        """
        scope = _scope_for(user_id)
        with self._lock:
            window = self._turns.get(scope, [])
            recent = window[-last_n:] if len(window) > last_n else window
            if not recent:
                return "No previous conversation context."

            lines = []
            for turn in recent:
                role_label = "User" if turn["role"] == "user" else "BuildSense"
                # Truncate long assistant responses to keep the prompt lean
                content = turn["content"][:500]
                lines.append(f"[{role_label}]: {content}")
            return "\n".join(lines)

    def clear(self, user_id=None) -> None:
        """Wipes THIS USER's conversation history only."""
        scope = _scope_for(user_id)
        with self._lock:
            self._turns.pop(scope, None)

    def __len__(self) -> int:
        """Total number of turns currently held across all scopes (no exposure)."""
        with self._lock:
            return sum(len(window) for window in self._turns.values())


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Long-Term Knowledge Store
# ═══════════════════════════════════════════════════════════════════════════════

MEMORY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory")
KNOWLEDGE_FILE = os.path.join(MEMORY_DIR, "knowledge_store.json")


class KnowledgeStore:
    """
    Persists project-level knowledge entries to a JSON file, strictly
    partitioned per user scope.

    Each entry is tagged with its owning user scope plus a topic and timestamp
    so agents can retrieve relevant prior knowledge during a pipeline run —
    but ONLY for the user the pipeline is running for.

    Stored structure (on disk):
        {
            "entries": [
                {
                    "id": "ks_0001",
                    "user_id": "12",              <- owning user scope
                    "topic": "design_preference",
                    "key": "style",
                    "value": "Modern Minimalist",
                    "source_agent": "Interior Design Agent",
                    "timestamp": "2026-08-06T12:00:00+00:00"
                },
                ...
            ]
        }

    Entries written before user isolation existed carry no ``user_id``; they
    are assigned the sealed LEGACY_SCOPE on load and can never be served to
    any authenticated user or anonymous caller.
    """

    def __init__(self, filepath: str = KNOWLEDGE_FILE):
        self._filepath = filepath
        self._lock = threading.RLock()
        self._entries: List[dict] = []
        self._load()

    # ── Persistence ────────────────────────────────────────────────────────
    def _load(self) -> None:
        """Load entries from disk, creating the directory/file if needed."""
        os.makedirs(os.path.dirname(self._filepath), exist_ok=True)
        entries: List[dict] = []
        if os.path.exists(self._filepath):
            try:
                with open(self._filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    entries = data.get("entries", [])
            except (json.JSONDecodeError, IOError):
                entries = []

        # Seal owner-less pre-isolation entries so they can never leak into
        # any user's memory view or pipeline context.
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry.setdefault("user_id", LEGACY_SCOPE)

        self._entries = entries

    def _save(self) -> None:
        """Flush current entries to disk."""
        os.makedirs(os.path.dirname(self._filepath), exist_ok=True)
        with open(self._filepath, "w", encoding="utf-8") as f:
            json.dump({"entries": self._entries}, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _matches_scope(entry: dict, scope: str) -> bool:
        return entry.get("user_id", LEGACY_SCOPE) == scope

    def _next_entry_id(self, scope: str) -> str:
        suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        return f"ks_{suffix}_{abs(hash(scope)) % 1000:03d}"

    # ── CRUD (all scoped) ──────────────────────────────────────────────────
    def store(self, topic: str, key: str, value: Any,
              source_agent: str = "system", user_id=None) -> dict:
        """
        Add or update a knowledge entry for THIS USER.

        If the same (user, topic, key) already exists, it is updated in-place.
        Otherwise a new entry is appended.  Entries belonging to other users
        are never read or modified here.
        """
        scope = _scope_for(user_id)
        with self._lock:
            timestamp = datetime.now(timezone.utc).isoformat()

            # Check this user's existing entry with same topic+key
            for existing in self._entries:
                if (
                    self._matches_scope(existing, scope)
                    and existing["topic"] == topic
                    and existing["key"] == key
                ):
                    existing["value"] = value
                    existing["source_agent"] = source_agent
                    existing["timestamp"] = timestamp
                    self._save()
                    return existing

            # New entry
            entry = {
                "id": self._next_entry_id(scope),
                "user_id": scope,
                "topic": topic,
                "key": key,
                "value": value,
                "source_agent": source_agent,
                "timestamp": timestamp,
            }
            self._entries.append(entry)
            self._save()
            return entry

    def retrieve(self, topic: Optional[str] = None, key: Optional[str] = None,
                 user_id=None) -> List[dict]:
        """
        Retrieve THIS USER's entries matching the given topic and/or key.
        If both are None, returns all of that user's entries.  Data stored by
        any other user is structurally invisible here.
        """
        scope = _scope_for(user_id)
        with self._lock:
            results = [e for e in self._entries if self._matches_scope(e, scope)]
            if topic:
                results = [e for e in results if e["topic"] == topic]
            if key:
                results = [e for e in results if e["key"] == key]
            return list(results)

    def get_all(self, user_id=None) -> List[dict]:
        """Returns a copy of THIS USER's knowledge entries."""
        scope = _scope_for(user_id)
        with self._lock:
            return [dict(e) for e in self._entries if self._matches_scope(e, scope)]

    def clear(self, user_id=None) -> None:
        """Wipes THIS USER's knowledge entries only and persists the change."""
        scope = _scope_for(user_id)
        with self._lock:
            self._entries = [
                e for e in self._entries if not self._matches_scope(e, scope)
            ]
            self._save()

    def __len__(self) -> int:
        """Total number of entries currently held across all scopes (no exposure)."""
        with self._lock:
            return len(self._entries)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Shared Memory Bus (Inter-Agent Communication)
# ═══════════════════════════════════════════════════════════════════════════════

class SharedMemoryBus:
    """
    In-process key-value store that agents use to pass context to each other
    within a single pipeline run.

    Example flow:
        1. Compliance Agent writes: bus.write("compliance_flags", ["corridor_too_narrow"])
        2. Design Agent reads:      flags = bus.read("compliance_flags")
           → Avoids placing furniture near the narrow corridor.

    SECURITY/CONCURRENCY: the coordinator creates a FRESH bus instance for
    every pipeline run instead of sharing (and clearing) one global instance,
    so two concurrent requests can never read or overwrite each other's
    inter-agent context.
    """

    def __init__(self):
        self._store: Dict[str, Any] = {}
        self._lock = threading.RLock()

    def write(self, key: str, value: Any) -> None:
        """Write a value to the shared bus."""
        with self._lock:
            self._store[key] = {
                "value": value,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

    def read(self, key: str, default: Any = None) -> Any:
        """Read a value from the shared bus. Returns default if key not found."""
        with self._lock:
            entry = self._store.get(key)
            return entry["value"] if entry else default

    def read_all(self) -> Dict[str, Any]:
        """Returns a snapshot of the entire bus contents."""
        with self._lock:
            return {k: v["value"] for k, v in self._store.items()}

    def clear(self) -> None:
        """Wipes the bus for a fresh pipeline run."""
        with self._lock:
            self._store.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# Module-Level Singletons
# ═══════════════════════════════════════════════════════════════════════════════

# These containers hold PER-USER partitions internally.  Sharing one process
# instance is safe ONLY because every access passes the acting user's id and
# the storage layer enforces the partition (see _scope_for above).

conversation_memory = ConversationMemory(max_turns=10)
knowledge_store = KnowledgeStore()
shared_memory_bus = SharedMemoryBus()  # deprecated global; pipeline uses per-run instances

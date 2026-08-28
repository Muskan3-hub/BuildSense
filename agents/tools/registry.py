"""
BuildSense Tool Registry
BuildSense Tool

Provides a centralized dispatcher for all enterprise tools with:
- Tool registration with metadata and input schemas
- Invocation with retry logic, timeout, and structured error capture
- Full audit logging of every call (name, inputs, output, duration_ms, status)
- Tool manifest endpoint for the Flask API
"""

import time
import traceback
from typing import Any, Callable, Dict, List, Optional


class ToolInvocationError(Exception):
    """Raised when a tool fails all retry attempts."""
    pass


class ToolRegistry:
    """
    Central registry and dispatcher for BuildSense enterprise tools.
    
    Usage:
        registry = ToolRegistry()
        registry.register("my_tool", my_fn, "Description", {...schema})
        result = registry.invoke("my_tool", arg1=val1, arg2=val2)
        manifest = registry.get_tool_manifest()
        audit_log = registry.get_audit_log()
    """
    
    def __init__(self, max_retries: int = 3, timeout_seconds: float = 10.0):
        self._tools: Dict[str, dict] = {}
        self._audit_log: List[dict] = []
        self._last_timestamp = None
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
    
    def register(
        self,
        name: str,
        fn: Callable,
        description: str,
        input_schema: Dict[str, str]
    ) -> None:
        """Register a callable tool with its metadata."""
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered. Use a unique name.")
        
        self._tools[name] = {
            "name": name,
            "fn": fn,
            "description": description,
            "input_schema": input_schema
        }
    
    def invoke(self, name: str, **kwargs) -> dict:
        """
        Invoke a registered tool by name with retry and error capture.
        
        Returns a structured result dict:
        {
          "tool_name": str,
          "status": "success" | "error",
          "inputs": dict,
          "output": any,           # present on success
          "error": str,            # present on error
          "duration_ms": float,
          "timestamp": str
        }
        """
        if name not in self._tools:
            available = list(self._tools.keys())
            entry = self._build_audit_entry(
                name, kwargs, None,
                f"Tool '{name}' not found. Available: {available}", 0.0
            )
            entry["status"] = "error"
            self._audit_log.append(entry)
            return entry
        
        tool = self._tools[name]
        fn = tool["fn"]
        last_error = None
        
        for attempt in range(1, self.max_retries + 1):
            start_time = time.perf_counter()
            try:
                output = fn(**kwargs)
                duration_ms = (time.perf_counter() - start_time) * 1000
                entry = self._build_audit_entry(name, kwargs, output, None, duration_ms)
                entry["status"] = "success"
                entry["attempt"] = attempt
                self._audit_log.append(entry)
                return entry
            except Exception as e:
                duration_ms = (time.perf_counter() - start_time) * 1000
                last_error = f"{type(e).__name__}: {str(e)}"
                if attempt < self.max_retries:
                    time.sleep(0.5 * attempt)  # exponential back-off
        
        # All retries exhausted
        entry = self._build_audit_entry(name, kwargs, None, last_error, duration_ms)
        entry["status"] = "error"
        entry["attempt"] = self.max_retries
        self._audit_log.append(entry)
        return entry
    
    def _build_audit_entry(
        self,
        name: str,
        inputs: dict,
        output: Any,
        error: Optional[str],
        duration_ms: float
    ) -> dict:
        from datetime import datetime, timedelta, timezone
        timestamp = datetime.now(timezone.utc)
        # Some hosts expose a coarse or frozen wall clock.  Trace consumers
        # use timestamps as stable event identifiers, so preserve ordering by
        # advancing duplicate values one microsecond at a time.
        if self._last_timestamp is not None and timestamp <= self._last_timestamp:
            timestamp = self._last_timestamp + timedelta(microseconds=1)
        self._last_timestamp = timestamp
        entry = {
            "tool_name": name,
            "status": "success" if error is None else "error",
            "inputs": inputs,
            "duration_ms": round(duration_ms, 2),
            "timestamp": timestamp.isoformat()
        }
        if error is None:
            entry["output"] = output
        else:
            entry["error"] = error
        return entry
    
    def get_tool_manifest(self) -> List[dict]:
        """Returns a JSON-serializable list of all registered tools (without fn refs)."""
        return [
            {
                "name": meta["name"],
                "description": meta["description"],
                "input_schema": meta["input_schema"]
            }
            for meta in self._tools.values()
        ]
    
    def get_audit_log(self) -> List[dict]:
        """Returns a copy of the full invocation audit log."""
        return list(self._audit_log)
    
    def clear_audit_log(self) -> None:
        """Clears the audit log (useful between pipeline runs in tests)."""
        self._audit_log.clear()
    
    def get_session_trace(self) -> List[dict]:
        """
        Returns a lightweight summary of the current session's audit log
        suitable for inclusion in the coordinator pipeline result.
        """
        return [
            {
                "tool": entry["tool_name"],
                "status": entry["status"],
                "duration_ms": entry["duration_ms"],
                "timestamp": entry["timestamp"],
                # Include abbreviated inputs (no large blobs)
                "inputs_summary": {
                    k: (str(v)[:80] if isinstance(v, (dict, list)) else v)
                    for k, v in entry.get("inputs", {}).items()
                },
                # Include output summary on success
                "output_summary": (
                    str(entry.get("output", ""))[:120]
                    if entry["status"] == "success" else None
                ),
                "error": entry.get("error")
            }
            for entry in self._audit_log
        ]

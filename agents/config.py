"""
BuildSense — LLM Configuration
Supports:
  - Multiple Gemini API keys (GEMINI_API_KEYS=key1,key2,key3 or GEMINI_API_KEY)
  - Round-robin key rotation to distribute load across keys
  - Exponential backoff retry on HTTP 429 rate limit / quota errors
  - Strict max_output_tokens cap on all calls
"""

import os
import time
import logging
import itertools
import threading
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Multi-Key Pool
# ---------------------------------------------------------------------------

def _load_api_keys():
    """Load one or more Gemini API keys from environment variables.

    Supports:
      - GEMINI_API_KEYS=key1,key2,key3   (comma-separated)
      - GEMINI_API_KEY=key1              (single, legacy)
      - GEMINI_API_KEY_1=key1 + GEMINI_API_KEY_2=key2 + ... (indexed)
    """
    keys = []

    # 1. Comma-separated pool
    pool = os.getenv("GEMINI_API_KEYS", "").strip()
    if pool:
        keys = [k.strip() for k in pool.split(",") if k.strip()]

    # 2. Indexed keys GEMINI_API_KEY_1, _2, ...
    if not keys:
        idx = 1
        while True:
            k = os.getenv(f"GEMINI_API_KEY_{idx}", "").strip()
            if not k:
                break
            keys.append(k)
            idx += 1

    # 3. Legacy single key fallback
    if not keys:
        single = os.getenv("GEMINI_API_KEY", "").strip()
        if single:
            keys = [single]

    return keys


_key_pool = _load_api_keys()
_key_lock = threading.Lock()
_key_cycle = itertools.cycle(_key_pool) if _key_pool else iter([])
_exhausted_keys = set()


def _next_api_key():
    """Return the next available API key in the round-robin cycle."""
    with _key_lock:
        if not _key_pool:
            return ""
        # Try up to len(pool) times to get a non-exhausted key
        for _ in range(len(_key_pool)):
            key = next(_key_cycle)
            if key not in _exhausted_keys:
                return key
        # All keys exhausted — reset and retry from scratch
        logger.warning("All API keys exhausted quota; resetting exhausted set.")
        _exhausted_keys.clear()
        return next(_key_cycle)


def _mark_key_exhausted(key):
    """Mark a key as rate-limited / quota-exceeded for this session."""
    with _key_lock:
        _exhausted_keys.add(key)
        logger.warning("API key marked exhausted (rate limited): ...%s", key[-6:])


# ---------------------------------------------------------------------------
# Legacy single-key compatibility shims
# ---------------------------------------------------------------------------

def get_api_key():
    """Return the currently active API key (first available)."""
    return _key_pool[0] if _key_pool else ""


def set_api_key(key):
    """Replace the entire key pool with a single key and persist to .env."""
    global _key_pool, _key_cycle
    key = key.strip()
    with _key_lock:
        _key_pool = [key]
        _key_cycle = itertools.cycle(_key_pool)
        _exhausted_keys.clear()

    os.environ["GEMINI_API_KEY"] = key
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".env"
    )
    with open(env_path, "w") as f:
        f.write(f"PORT=5000\nGEMINI_API_KEY={key}\n")


def is_live_mode():
    return bool(_key_pool and len(_key_pool[0]) > 5)


# ---------------------------------------------------------------------------
# Gemini LLM factory with key rotation
# ---------------------------------------------------------------------------

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "2048"))


def get_llm(temperature=0.2, api_key=None):
    """Return a ChatGoogleGenerativeAI instance with the next available key."""
    if not is_live_mode():
        return None

    key = api_key or _next_api_key()
    if not key:
        return None

    return ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=key,
        temperature=temperature,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


# ---------------------------------------------------------------------------
# Retry-aware LLM invocation (handles HTTP 429 rate limit errors)
# ---------------------------------------------------------------------------

_RATE_LIMIT_SIGNALS = (
    "429",
    "rate limit",
    "quota",
    "resource_exhausted",
    "resourceexhausted",
    "too many requests",
)

MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "5"))
BACKOFF_BASE_SEC = float(os.getenv("LLM_BACKOFF_BASE_SEC", "2.0"))


def _is_rate_limit_error(exc):
    msg = str(exc).lower()
    return any(sig in msg for sig in _RATE_LIMIT_SIGNALS)


def invoke_with_retry(messages, temperature=0.2, max_tokens=None, current_key=None):
    """
    Invoke an LLM call with automatic key rotation and exponential backoff
    on rate-limit (HTTP 429) errors.

    Args:
        messages: list of LangChain message objects or a plain string prompt.
        temperature: model temperature.
        max_tokens: override for max_output_tokens.
        current_key: optionally pin a specific API key.

    Returns:
        LangChain AIMessage response.
    """
    last_exc = None
    used_key = current_key

    for attempt in range(1, MAX_RETRIES + 1):
        if used_key is None:
            used_key = _next_api_key()

        llm = ChatGoogleGenerativeAI(
            model=GEMINI_MODEL,
            google_api_key=used_key,
            temperature=temperature,
            max_output_tokens=max_tokens or MAX_OUTPUT_TOKENS,
        )

        t0 = time.time()
        key_alias = used_key[-4:] if (used_key and len(used_key) >= 4) else "default"
        try:
            response = llm.invoke(messages)
            try:
                from agents.metrics import record_agent_call, record_agent_tokens
                record_agent_call("Gemini Engine", GEMINI_MODEL, time.time() - t0, key_alias=key_alias, status="200")

                prompt_tokens = 0
                completion_tokens = 0
                if hasattr(response, 'usage_metadata') and response.usage_metadata:
                    um = response.usage_metadata
                    if isinstance(um, dict):
                        prompt_tokens = um.get('input_tokens') or um.get('prompt_token_count') or um.get('prompt_tokens', 0)
                        completion_tokens = um.get('output_tokens') or um.get('candidates_token_count') or um.get('completion_tokens', 0)
                    else:
                        prompt_tokens = getattr(um, 'input_tokens', 0) or getattr(um, 'prompt_token_count', 0) or getattr(um, 'prompt_tokens', 0)
                        completion_tokens = getattr(um, 'output_tokens', 0) or getattr(um, 'candidates_token_count', 0) or getattr(um, 'completion_tokens', 0)
                elif hasattr(response, 'response_metadata') and isinstance(response.response_metadata, dict):
                    tu = response.response_metadata.get('token_usage') or response.response_metadata.get('usage_metadata') or {}
                    if isinstance(tu, dict):
                        prompt_tokens = tu.get('input_tokens') or tu.get('prompt_tokens') or tu.get('prompt_token_count', 0)
                        completion_tokens = tu.get('output_tokens') or tu.get('completion_tokens') or tu.get('candidates_token_count', 0)

                record_agent_tokens("Gemini Engine", GEMINI_MODEL, key_alias=key_alias, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
            except Exception:
                pass
            return response

        except Exception as exc:
            st = "429" if _is_rate_limit_error(exc) else "500"
            try:
                from agents.metrics import record_agent_call
                record_agent_call("Gemini Engine", GEMINI_MODEL, time.time() - t0, key_alias=key_alias, status=st)
            except Exception:
                pass
            last_exc = exc

            if _is_rate_limit_error(exc):
                _mark_key_exhausted(used_key)
                used_key = None  # pick next key on next attempt
                wait = BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                logger.warning(
                    "Rate limit hit (attempt %d/%d). Rotating key & waiting %.1fs.",
                    attempt, MAX_RETRIES, wait,
                )
                time.sleep(wait)
            else:
                # Non-rate-limit error — don't retry
                raise

    raise RuntimeError(
        f"LLM call failed after {MAX_RETRIES} attempts. Last error: {last_exc}"
    ) from last_exc


# ---------------------------------------------------------------------------
# Groq Integration
# ---------------------------------------------------------------------------

# Parse single key, comma-separated list of keys, or GROQ_API_KEYS variable
def get_groq_api_keys() -> list[str]:
    """Return list of configured Groq API keys from environment with 4-key placeholder fallback."""
    raw_single = os.getenv("GROQ_API_KEY", "")
    raw_multi = os.getenv("GROQ_API_KEYS", "")
    combined = f"{raw_single},{raw_multi}"
    keys = [k.strip().strip("'\"") for k in combined.split(",") if k.strip()]
    if not keys:
        keys = ["gsk_key_1", "gsk_key_2", "gsk_key_3", "gsk_key_4"]
    return list(dict.fromkeys(keys))  # deduplicate preserving order


GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
if not GROQ_MODEL or GROQ_MODEL in ("openai/gpt-oss-120b", "llama3-70b-8192", "llama-3.3-70b-versatile", "llama-3.1-70b-versatile"):
    GROQ_MODEL = "openai/gpt-oss-20b"

_groq_key_index = 0


def get_next_groq_api_key() -> str:
    """Return next Groq API key in round-robin sequence using modulo arithmetic."""
    global _groq_key_index
    keys = get_groq_api_keys()
    key = keys[_groq_key_index % len(keys)]
    _groq_key_index = (_groq_key_index + 1) % len(keys)
    return key


def is_groq_available():
    """Return True if at least one valid GROQ_API_KEY is configured."""
    keys = get_groq_api_keys()
    return bool(keys and any(len(k) > 5 for k in keys))


def invoke_groq_with_retry(prompt, temperature=0.2, max_tokens=1500):
    """
    Invoke Groq for ultra-fast text synthesis with exponential backoff and
    automatic multi-key round-robin rotation on rate limits (429 errors).

    Args:
        prompt: plain text prompt string or message list.
        temperature: model temperature.
        max_tokens: max output tokens limit.

    Returns:
        String response text.
    """
    if not is_groq_available():
        raise RuntimeError("GROQ_API_KEY is not configured.")

    try:
        from groq import Groq
    except ImportError:
        raise RuntimeError("groq package is not installed. Run: pip install groq")
    
    if isinstance(prompt, list):
        messages = prompt
    else:
        messages = [{"role": "user", "content": str(prompt)}]

    last_exc = None
    keys = get_groq_api_keys()

    for attempt in range(1, MAX_RETRIES + 1):
        active_key = get_next_groq_api_key()
        client = Groq(api_key=active_key)
        t0 = time.time()
        key_alias = active_key[-4:] if (active_key and len(active_key) >= 4) else "default"
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            try:
                from agents.metrics import record_agent_call, record_agent_tokens
                record_agent_call("Groq Agent", GROQ_MODEL, time.time() - t0, key_alias=key_alias, status="200")

                prompt_tokens = 0
                completion_tokens = 0
                if hasattr(response, 'usage') and response.usage:
                    prompt_tokens = getattr(response.usage, 'prompt_tokens', 0)
                    completion_tokens = getattr(response.usage, 'completion_tokens', 0)

                record_agent_tokens("Groq Agent", GROQ_MODEL, key_alias=key_alias, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
            except Exception:
                pass
            return response.choices[0].message.content

        except Exception as exc:
            st = "429" if _is_rate_limit_error(exc) else "500"
            try:
                from agents.metrics import record_agent_call
                record_agent_call("Groq Agent", GROQ_MODEL, time.time() - t0, key_alias=key_alias, status=st)
            except Exception:
                pass
            last_exc = exc
            if _is_rate_limit_error(exc):
                wait = BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                logger.warning(
                    "Groq Rate limit hit on key ending ...%s (attempt %d/%d). Rotating key and waiting %.1fs.",
                    active_key[-4:] if len(active_key) >= 4 else "key", attempt, MAX_RETRIES, wait,
                )
                time.sleep(wait)
            else:
                raise

    raise RuntimeError(f"Groq call failed after {MAX_RETRIES} attempts. Last error: {last_exc}") from last_exc


# ---------------------------------------------------------------------------
# Response text extraction helper
# ---------------------------------------------------------------------------

def extract_text(response_content):
    if isinstance(response_content, list):
        return "".join(
            t.get("text", "") if isinstance(t, dict) else str(t)
            for t in response_content
        )
    return str(response_content)
"""
BuildSense — Deterministic Local Calculation Engine
=====================================================

Ingests raw structured JSON from Gemini Vision and performs ALL mathematical
operations deterministically in Python.  Nothing is delegated to an LLM:

  - Dimension string parsing (metric and imperial)
  - Unit conversion: m → ft, sqm → sqft
  - Per-room area (area_sqm, area_sqft) and perimeter
  - Indoor vs outdoor classification
  - Total built-up area (excludes terraces/balconies/patios)
  - Compact summary dict ready for LLM downstream handoff
"""

import re
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SQM_TO_SQFT: float = 10.7639
M_TO_FT: float = 3.28084
FT_TO_M: float = 0.3048

OUTDOOR_ROOM_TYPES = {
    "terrace", "balcony", "patio", "veranda",
    "garden", "yard", "backyard", "porch",
}

OUTDOOR_NAME_PATTERNS = {
    "terrace", "balcony", "patio", "porch",
    "garden", "yard", "backyard", "open space", "driveway",
}

# ---------------------------------------------------------------------------
# Dimension String Parser
# ---------------------------------------------------------------------------

_DIM_RE = re.compile(
    r"""
    (?:
        (\d+(?:\.\d+)?)\s*ft?\s*[-x×*]\s*(\d+(?:\.\d+)?)\s*ft?  # 10ft x 12ft
        |
        (\d+)['′]\s*(?:(\d+)["″])?\s*[-x×*]\s*(\d+)['′]\s*(?:(\d+)["″])?  # 10'6" x 12'0"
        |
        (\d+(?:\.\d+)?)\s*m\s*[-x×*]\s*(\d+(?:\.\d+)?)\s*m      # 3.2m x 4.5m
        |
        (\d+(?:\.\d+)?)\s*[-x×*]\s*(\d+(?:\.\d+)?)              # 3.00 x 3.60 (generic)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)


def parse_dimension_string(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Parse a raw architectural dimension string into (length_m, width_m, unit).

    Supports:
      "3.00 x 3.60"         → 3.0 m × 3.6 m  (default metres when no unit given)
      "10 ft x 12 ft"       → 10 ft × 12 ft
      "10'-0\" x 12'-6\""   → 10.0 ft × 12.5 ft
      "3.2m x 4.5m"         → 3.2 m × 4.5 m

    Returns dict with keys: length, width, unit, length_m, width_m
    Returns None if no valid pair found.
    """
    if not raw or not isinstance(raw, str):
        return None

    text = raw.strip()
    m = _DIM_RE.search(text)
    if not m:
        return None

    g = m.groups()

    # ft x ft  (g[0], g[1])
    if g[0] is not None and g[1] is not None:
        l, w = float(g[0]), float(g[1])
        return dict(length=l, width=w, unit="ft",
                    length_m=round(l * FT_TO_M, 4),
                    width_m=round(w * FT_TO_M, 4))

    # feet-inches x feet-inches  (g[2..5])
    if g[2] is not None and g[4] is not None:
        l = int(g[2]) + (int(g[3]) / 12 if g[3] else 0)
        w = int(g[4]) + (int(g[5]) / 12 if g[5] else 0)
        return dict(length=round(l, 4), width=round(w, 4), unit="ft",
                    length_m=round(l * FT_TO_M, 4),
                    width_m=round(w * FT_TO_M, 4))

    # m x m  (g[6], g[7])
    if g[6] is not None and g[7] is not None:
        l, w = float(g[6]), float(g[7])
        return dict(length=l, width=w, unit="m",
                    length_m=l, width_m=w)

    # generic NxN — assume metres  (g[8], g[9])
    if g[8] is not None and g[9] is not None:
        l, w = float(g[8]), float(g[9])
        return dict(length=l, width=w, unit="m",
                    length_m=l, width_m=w)

    return None


# ---------------------------------------------------------------------------
# Single-room area helpers
# ---------------------------------------------------------------------------

def _area_from_dimensions(dims: Optional[Dict]) -> Optional[float]:
    """Calculate sqm area from a parsed dimension dict."""
    if not dims:
        return None
    lm = dims.get("length_m")
    wm = dims.get("width_m")
    if lm and wm and lm > 0 and wm > 0:
        return round(lm * wm, 4)
    # Fallback: ft dimensions
    unit = dims.get("unit", "m")
    l = dims.get("length")
    w = dims.get("width")
    if l and w and l > 0 and w > 0:
        if unit == "ft":
            return round((l * FT_TO_M) * (w * FT_TO_M), 4)
        return round(l * w, 4)
    return None


def _is_outdoor(name: str, room_type: str) -> bool:
    n = (name or "").strip().lower()
    t = (room_type or "").strip().lower()
    return (
        t in OUTDOOR_ROOM_TYPES
        or any(p in n for p in OUTDOOR_NAME_PATTERNS)
    )


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------

class BlueprintCalculationEngine:
    """
    Ingests Gemini Vision JSON and outputs deterministically computed metrics.

    Usage:
        engine = BlueprintCalculationEngine(gemini_json)
        result = engine.compute()
    """

    def __init__(self, gemini_data: Dict[str, Any]):
        self._raw = gemini_data

    # ------------------------------------------------------------------
    def compute(self) -> Dict[str, Any]:
        """
        Run all calculations.

        Returns a structured dict containing:
          - rooms        : enriched per-room records with area_sqm, area_sqft,
                           length_m, width_m, perimeter_m
          - outdoor_spaces: same structure for outdoor/semi-outdoor rooms
          - total_indoor_sqm / total_indoor_sqft
          - total_outdoor_sqm / total_outdoor_sqft
          - room_count   : number of indoor rooms
          - summary_text : one-liner for LLM handoff prompt
        """
        raw_rooms: List[Dict] = self._raw.get("rooms", [])
        if not isinstance(raw_rooms, list):
            raw_rooms = []

        indoor_rooms: List[Dict] = []
        outdoor_spaces: List[Dict] = []
        total_indoor_sqm = 0.0
        total_outdoor_sqm = 0.0

        for room in raw_rooms:
            if not isinstance(room, dict):
                continue

            name = str(room.get("name", "")).strip()
            room_type = str(room.get("room_type", "unknown")).lower()

            # Parse dimensions: prefer structured dict, fall back to raw_dimension string
            dims = room.get("dimensions")
            if not dims:
                dims = parse_dimension_string(room.get("raw_dimension"))

            area_sqm = _area_from_dimensions(dims)
            area_sqft = round(area_sqm * SQM_TO_SQFT, 2) if area_sqm else None

            # Perimeter
            if dims and dims.get("length_m") and dims.get("width_m"):
                perim = round(2 * (dims["length_m"] + dims["width_m"]), 2)
            else:
                perim = None

            enriched = {
                **room,
                "dimensions": dims,
                "area_sqm": round(area_sqm, 2) if area_sqm else None,
                "area_sqft": area_sqft,
                "perimeter_m": perim,
            }

            if _is_outdoor(name, room_type):
                outdoor_spaces.append(enriched)
                if area_sqm:
                    total_outdoor_sqm += area_sqm
            else:
                indoor_rooms.append(enriched)
                if area_sqm:
                    total_indoor_sqm += area_sqm

        total_indoor_sqft = round(total_indoor_sqm * SQM_TO_SQFT, 2) if total_indoor_sqm else None
        total_outdoor_sqft = round(total_outdoor_sqm * SQM_TO_SQFT, 2) if total_outdoor_sqm else None
        total_indoor_sqm = round(total_indoor_sqm, 2) if total_indoor_sqm else None

        # Build a compact one-liner summary for the LLM prompt (saves tokens)
        summary_parts = [f"{len(indoor_rooms)} indoor rooms"]
        if total_indoor_sqft:
            summary_parts.append(f"total indoor area: {total_indoor_sqft} sq ft ({total_indoor_sqm} sqm)")
        if outdoor_spaces:
            summary_parts.append(f"{len(outdoor_spaces)} outdoor/semi-outdoor spaces")
        if total_outdoor_sqft:
            summary_parts.append(f"outdoor area: {total_outdoor_sqft} sq ft")
        summary_text = "; ".join(summary_parts) + "."

        return {
            "rooms": indoor_rooms,
            "outdoor_spaces": outdoor_spaces,
            "room_count": len(indoor_rooms),
            "outdoor_space_count": len(outdoor_spaces),
            "total_indoor_sqm": total_indoor_sqm,
            "total_indoor_sqft": total_indoor_sqft,
            "total_outdoor_sqm": round(total_outdoor_sqm, 2) if total_outdoor_sqm else None,
            "total_outdoor_sqft": total_outdoor_sqft,
            "summary_text": summary_text,
        }


# ---------------------------------------------------------------------------
# Standalone helpers for specialist agents
# ---------------------------------------------------------------------------

def compute_room_metrics(rooms: List[Dict]) -> List[Dict]:
    """Enrich a list of room dicts with deterministic area and perimeter values."""
    engine = BlueprintCalculationEngine({"rooms": rooms})
    result = engine.compute()
    return result["rooms"] + result["outdoor_spaces"]


def sqm_to_sqft(sqm: float) -> float:
    return round(sqm * SQM_TO_SQFT, 2)


def sqft_to_sqm(sqft: float) -> float:
    return round(sqft / SQM_TO_SQFT, 2)


def m_to_ft(metres: float) -> float:
    return round(metres * M_TO_FT, 2)

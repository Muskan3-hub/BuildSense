

"""
BuildSense - Blueprint Vision Agent

Single, cleaned implementation of blueprint analysis.

Fixes:
- Removes duplicate analyze_blueprint() implementations.
- Handles Gemini pixel coordinates and percentage coordinates.
- Validates rooms, corridors, and exits consistently.
- Keeps physically separate rooms separate.
- Treats terrace/balcony as spaces, not indoor rooms.
- Calculates total area only when reliable numeric dimensions/areas
  are available; never invents square footage.
"""

import os
import json
import base64
import io
import re
import logging

from PIL import Image
from langchain_core.messages import HumanMessage

from agents.config import get_llm, is_live_mode, invoke_with_retry

logger = logging.getLogger(__name__)


def optimize_image(image_path: str, max_dim: int = 1024, quality: int = 75) -> bytes:
    """Pre-compress blueprint image to max 1024px and JPEG quality 75% to save >75% vision tokens."""
    with Image.open(image_path) as img:
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()


def _extract_json(text):
    """
    Robustly extract the JSON object from Gemini's response.

    Gemini frequently wraps the JSON in Markdown code fences and may
    prepend/append explanatory prose, use any number of backticks, a
    case-insensitive language tag (```json / ```JSON / ``` json), or even
    return a bare JSON object with no fence at all.

    Strategy (in order):
      1. If the text is already valid JSON, return it parsed.
      2. Otherwise, find every Markdown code fence block and try to parse
         each as JSON, skipping an optional case-insensitive language tag
         such as ``json``.
      3. As a last resort, locate the outermost balanced brace pair
         ``{ ... }`` and try to parse just that substring, ignoring any
         surrounding prose or trailing content.

    Returns:
        The parsed JSON value.

    Raises:
        json.JSONDecodeError: if no valid JSON could be isolated.
    """
    text = (text or "").strip()

    if not text:
        raise json.JSONDecodeError("Empty Gemini response.", text, 0)

    # 1. The text may already be clean JSON.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Try each Markdown fenced code block. A fence is a line of 3+
    #    backticks (or tildes) followed by an optional ``json`` language tag
    #    (case-insensitive, allowing a space).
    fence_re = re.compile(
        r"^\s*(`{3,}|~{3,})\s*([A-Za-z]*json[A-Za-z]*)?\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    matches = list(fence_re.finditer(text))
    if len(matches) >= 2:
        for i in range(len(matches) - 1):
            block = text[matches[i].end():matches[i + 1].start()].strip()
            if not block:
                continue
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                continue

    # 3. Last resort: isolate the first balanced ``{ ... }`` object and try
    #    to parse it, ignoring any prose before/after.
    start = text.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break

    # Nothing recovered; re-raise a meaningful decode error on the original.
    raise json.JSONDecodeError(
        "Gemini response contained no valid JSON.", text, 0
    )


def analyze_blueprint_for_question(image_path: str, user_question: str) -> str:
    """
    Question-focused Gemini analysis of the ACTUAL uploaded blueprint.

    This is the internal 'understand the blueprint' step of the follow-up
    pipeline: Gemini receives the real image plus the user's follow-up
    question and returns a structured factual analysis that a downstream
    text LLM (Groq) can use to compose the final answer.

    Responsibilities stay separated:
      - Gemini  : understand the uploaded blueprint (this function)
      - Groq    : generate the user-facing answer from that understanding

    Returns:
        Plain-text factual blueprint analysis.

    Raises:
        FileNotFoundError / ValueError: invalid image.
        RuntimeError: Gemini not configured or Gemini call failed.
                      Callers must NOT fabricate blueprint data on failure.
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Blueprint image not found: {image_path}")

    try:
        with Image.open(image_path) as img:
            width, height = img.size
            format_name = img.format or "JPEG"
    except Exception as exc:
        raise ValueError(f"Invalid blueprint image: {exc}") from exc

    if not is_live_mode():
        raise RuntimeError(
            "Gemini API is not configured. "
            "Please configure GEMINI_API_KEY in .env."
        )

    llm = get_llm()

    if llm is None:
        raise RuntimeError("Gemini model could not be initialized.")

    optimized_bytes = optimize_image(image_path, max_dim=1600, quality=80)
    base64_image = base64.b64encode(optimized_bytes).decode("utf-8")

    prompt = f"""You are analysing an uploaded construction/architectural blueprint.

Analyse the provided blueprint carefully.

Extract only information that can be supported by the blueprint.

Pay particular attention to information relevant to the user's follow-up question.

User's follow-up question:
{user_question}

Return a structured, factual blueprint analysis that another LLM can use to
answer the question. Cover, where present and relevant to the question:
dimensions, rooms and their labels/types, floor layout and adjacencies,
areas, structural elements, doors, windows, walls, materials,
measurements, quantities, and construction details.

Do not invent measurements, rooms, materials, quantities, or structural details.

Clearly distinguish in your output:
1. INFORMATION DIRECTLY VISIBLE IN THE BLUEPRINT — facts read off the drawing
   (verbatim labels/dimensions/annotations where possible).
2. INFORMATION INFERRED FROM THE BLUEPRINT — reasonable derivations (e.g.
   computed areas from annotated dimensions), each with its basis.
3. CANNOT BE DETERMINED — anything the question needs but the blueprint
   does not show clearly enough to state.

IMAGE SIZE:
Width = {width} pixels
Height = {height} pixels

This analysis is internal context for another model — do NOT address the
user and do NOT write a final answer to the question."""

    message = HumanMessage(
        content=[
            {
                "type": "text",
                "text": prompt,
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": (
                        f"data:image/"
                        f"{format_name.lower()};base64,"
                        f"{base64_image}"
                    )
                },
            },
        ]
    )

    try:
        response = invoke_with_retry([message], temperature=0.1, max_tokens=2048)
    except Exception as exc:
        raise RuntimeError(
            "Blueprint question analysis failed because Gemini "
            f"could not analyze the image: {exc}"
        ) from exc

    from agents.config import extract_text

    analysis_text = extract_text(response.content).strip()

    if not analysis_text:
        raise RuntimeError(
            "Gemini returned an empty blueprint analysis for the question."
        )

    return analysis_text


OUTDOOR_TYPES = {
    "terrace",
    "balcony",
    "patio",
    "veranda",
    "garden",
    "yard",
    "backyard",
}

OUTDOOR_NAME_PATTERNS = {
    "backyard",
    "front yard",
    "frontyard",
    "garden",
    "terrace",
    "balcony",
    "patio",
    "open space",
    "driveway",
}

ROOM_TYPES = {
    "bedroom",
    "living room",
    "kitchen",
    "dining room",
    "bathroom",
    "office",
    "conference room",
    "utility room",
    "store room",
    "parking",
    "garage",
    "corridor",
    "staircase",
    "unknown",
    "other",
}


def _to_float(value):
    """Safely convert a value to float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalise_coords(raw_coords, image_width, image_height):
    """
    Convert bounding box coordinates to percentage format [x, y, width, height] (0-100 scale).

    Handles:
    - Dict format with "box_2d": [ymin, xmin, ymax, xmax] (0-1000 integer scale)
    - List format [ymin, xmin, ymax, xmax] (0-1000 scale)
    - List format [x, y, width, height] (0-100 percentage scale)
    - Raw pixel values
    """
    if isinstance(raw_coords, dict):
        coords = raw_coords.get("box_2d") or raw_coords.get("coords")
    else:
        coords = raw_coords

    if not isinstance(coords, list) or len(coords) != 4:
        return None

    values = [_to_float(v) for v in coords]
    if any(v is None for v in values):
        return None

    v0, v1, v2, v3 = values

    # Degenerate zero box
    if all(v == 0 for v in values):
        return None

    # Case 1: Normalized 0.0 - 1.0 float scale
    if max(values) <= 1.0:
        if v2 > v0 and v3 > v1:
            x, y, w, h = v1 * 100.0, v0 * 100.0, (v3 - v1) * 100.0, (v2 - v0) * 100.0
        else:
            x, y, w, h = v0 * 100.0, v1 * 100.0, v2 * 100.0, v3 * 100.0

    # Case 2: Standard Gemini 0-1000 integer scale [ymin, xmin, ymax, xmax]
    elif max(values) > 100.0 and max(values) <= 1000.0:
        if v2 > v0 and v3 > v1:
            ymin, xmin, ymax, xmax = v0, v1, v2, v3
            x = (xmin / 1000.0) * 100.0
            y = (ymin / 1000.0) * 100.0
            w = ((xmax - xmin) / 1000.0) * 100.0
            h = ((ymax - ymin) / 1000.0) * 100.0
        else:
            x = (v0 / 1000.0) * 100.0
            y = (v1 / 1000.0) * 100.0
            w = (v2 / 1000.0) * 100.0
            h = (v3 / 1000.0) * 100.0

    # Case 3: Raw pixel values
    elif max(values) > 100.0 and image_width > 0 and image_height > 0:
        if v2 > v0 and v3 > v1 and v2 <= image_height * 1.1 and v3 <= image_width * 1.1:
            ymin, xmin, ymax, xmax = v0, v1, v2, v3
            x = (xmin / image_width) * 100.0
            y = (ymin / image_height) * 100.0
            w = ((xmax - xmin) / image_width) * 100.0
            h = ((ymax - ymin) / image_height) * 100.0
        else:
            x = (v0 / image_width) * 100.0
            y = (v1 / image_height) * 100.0
            w = (v2 / image_width) * 100.0
            h = (v3 / image_height) * 100.0

    # Case 4: Values <= 100 (Percentages)
    else:
        if v2 > v0 and v3 > v1 and (v2 - v0 > 3 or v3 - v1 > 3) and (v2 > 25 or v3 > 25):
            x, y, w, h = v1, v0, v3 - v1, v2 - v0
        else:
            x, y, w, h = v0, v1, v2, v3

    if w <= 0 or h <= 0:
        return None

    x = max(0.0, min(99.0, x))
    y = max(0.0, min(99.0, y))
    w = max(0.5, min(100.0 - x, w))
    h = max(0.5, min(100.0 - y, h))

    return [
        round(x, 2),
        round(y, 2),
        round(w, 2),
        round(h, 2),
    ]


def _normalise_confidence(value):
    confidence = _to_float(value)

    if confidence is None:
        return 0.0

    return round(
        max(0.0, min(1.0, confidence)),
        2,
    )


def _is_outdoor_space(name, room_type):
    """Identify outdoor areas without relying solely on Gemini's type label."""
    normalised_name = str(name or "").strip().lower()
    normalised_type = str(room_type or "").strip().lower()
    return (
        normalised_type in OUTDOOR_TYPES
        or any(pattern in normalised_name for pattern in OUTDOOR_NAME_PATTERNS)
    )


def _parse_area_sqft(value):
    """Return a numeric area if the value is explicitly available."""
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None

    if not isinstance(value, str):
        return None

    text = value.lower().replace(",", "")

    # Examples:
    # 120 sqft
    # 120 sq ft
    # 120 square feet
    # 10 m2 / 10 sq m (converted to sqft)
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|square\s+feet)",
        text,
    )

    if match:
        area = float(match.group(1))
        return area if area > 0 else None

    metric_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:m2|m²|sq\.?\s*m|square\s+met(?:er|re)s?)",
        text,
    )
    if metric_match:
        area = float(metric_match.group(1)) * 10.7639
        return area if area > 0 else None

    return None


def _parse_dimensions_sqft_legacy(value):
    """
    Parse dimensions such as:
        10 ft x 12 ft
        10' x 12'
        10m x 4m

    Returns sqft or None.
    """
    if not isinstance(value, str):
        return None

    text = value.lower().replace(",", "")

    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(ft|feet|')?\s*[x×]\s*"
        r"(\d+(?:\.\d+)?)\s*(ft|feet|')?",
        text,
    )

    if not match:
        return None

    first = float(match.group(1))
    second = float(match.group(3))

    unit1 = match.group(2) or "ft"
    unit2 = match.group(4) or unit1

    # Convert metres to feet when both dimensions are in metres.
    if unit1 in {"m", "meter", "meters"} or unit2 in {
        "m",
        "meter",
        "meters",
    }:
        first *= 3.28084
        second *= 3.28084

    area = first * second

    return area if area > 0 else None


def _parse_dimensions_sqft(value):
    """Parse explicitly printed imperial or metric dimensions into sqft."""
    if not isinstance(value, str):
        return None

    text = value.lower().replace(",", "").replace("×", "x")
    # Tolerate a common UTF-8 decoding artefact from older model responses.
    text = text.replace("Ã—", "x")

    def side_pattern(prefix):
        return (
            rf"(?P<{prefix}number>\d+(?:\.\d+)?)\s*"
            rf"(?:"
            rf"(?P<{prefix}feet>ft|feet|foot|')"
            rf"\s*(?:-\s*)?(?P<{prefix}inches>\d+(?:\.\d+)?)?"
            rf"\s*(?:in|inch|inches|\")?"
            rf"|(?P<{prefix}metric>mm|cm|m|meter|meters|metre|metres)"
            rf")?"
        )

    match = re.search(
        rf"{side_pattern('a_')}\s*x\s*{side_pattern('b_')}", text
    )
    if not match:
        return None

    def to_feet(prefix, inherited_unit=None):
        number = _to_float(match.group(f"{prefix}number"))
        if number is None or number <= 0:
            return None, None
        if match.group(f"{prefix}feet"):
            inches = _to_float(match.group(f"{prefix}inches")) or 0.0
            return number + (inches / 12.0), "ft"
        metric = match.group(f"{prefix}metric")
        if metric:
            metres = {"m", "meter", "meters", "metre", "metres"}
            factor = 3.28084 if metric in metres else (
                0.0328084 if metric == "cm" else 0.00328084
            )
            return number * factor, "metric"
        # A unit may be printed once, e.g. "10 ft x 12".
        if inherited_unit is not None:
            # A unit may be printed only once, e.g. "3.2 m x 4.5".
            # ``inherited_unit`` is our normalised category, so retain the
            # conversion that was applied to the first dimension.
            if inherited_unit == "metric":
                return number * 3.28084, inherited_unit
            return number, inherited_unit
        return None, None

    first, first_unit = to_feet("a_")
    second, _ = to_feet("b_", first_unit)
    if first is None or second is None:
        return None
    return round(first * second, 2)


def _parse_dimension_object(dimensions):
    """
    Convert structured Gemini dimensions to square feet.

    Supported:
        {"length": 12, "width": 14, "unit": "ft"}
        {"length_ft": 12, "width_ft": 14}
    """
    if not isinstance(dimensions, dict):
        return None

    length = dimensions.get("length_ft", dimensions.get("length"))
    width = dimensions.get("width_ft", dimensions.get("width"))

    length = _to_float(length)
    width = _to_float(width)

    if length is None or width is None or length <= 0 or width <= 0:
        return None

    unit = str(dimensions.get("unit", "ft")).strip().lower()

    if unit in {"m", "meter", "meters", "metre", "metres"}:
        length *= 3.28084
        width *= 3.28084
    elif unit == "cm":
        length *= 0.0328084
        width *= 0.0328084
    elif unit == "mm":
        length *= 0.00328084
        width *= 0.00328084

    return round(length * width, 2)


def _room_area_sqft(room):
    """
    Get room area from explicit area or reliable dimensions.
    Never use pixel coordinates for square-foot calculation.
    """
    area = _parse_area_sqft(room.get("area_sqft"))
    if area is not None:
        return area

    dimensions = room.get("dimensions")

    area = _parse_dimension_object(dimensions)
    if area is not None:
        return area

    return _parse_dimensions_sqft(dimensions)


def _deduplicate_rooms(rooms):
    """
    Remove exact duplicate detections while preserving separate
    physically distinct rooms.
    """
    result = []
    seen = set()

    for room in rooms:
        name = str(
            room.get("name", "")
        ).strip()

        coords = room.get("coords", [])

        key = (
            name.lower(),
            tuple(
                round(float(v), 1)
                for v in coords
            ),
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(room)

    return result



def _extract_total_area_with_gemini(llm, base64_image, format_name, width, height):
    """
    Second-pass vision extraction for total floor area.

    This pass is deliberately conservative: Gemini must read an explicit
    overall dimension/area from the drawing. It must not estimate area from
    pixels, room labels, or visual proportions.
    """
    prompt = """
You are an architectural quantity-extraction specialist.

Inspect ONLY this blueprint image.

Image size: IMAGE_WIDTH x IMAGE_HEIGHT pixels.

Find the OUTER BUILDING/FLOOR-PLAN dimensions or an explicitly printed
TOTAL/BUILT-UP AREA on the drawing.

Return ONLY JSON:
{
  "total_area_sqft": null,
  "overall_dimensions": {
    "length": null,
    "width": null,
    "unit": "ft"
  },
  "confidence": 0.0,
  "evidence": null
}

Rules:
1. If the blueprint explicitly prints a total/built-up area in square feet,
   return it.
2. Otherwise, find clearly readable OUTER building dimensions. If they form
   a rectangular footprint, calculate length x width.
3. Convert metres to feet when necessary.
4. For feet/inches such as 12'-6", convert to decimal feet.
5. If the plan is irregular, do not invent a rectangle from the image.
6. Do NOT estimate from image size, pixel coordinates, or visual proportions.
7. If dimensions are not readable, return null.
8. "evidence" must contain the dimension/area text actually read.
"""
    prompt = prompt.replace("IMAGE_WIDTH", str(width)).replace(
        "IMAGE_HEIGHT", str(height)
    )
    try:
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/{format_name.lower()};base64,{base64_image}"
                    },
                },
            ]
        )
        response = llm.invoke([message])
        from agents.config import extract_text
        raw = extract_text(response.content).strip()
        result = _extract_json(raw)

        area = _parse_area_sqft(result.get("total_area_sqft"))
        if area is None:
            area = _to_float(result.get("total_area_sqft"))
            if area is not None and area <= 0:
                area = None

        # If Gemini read overall dimensions but did not calculate the area,
        # calculate a rectangular footprint.
        if area is None:
            area = _parse_dimension_object(
                result.get("overall_dimensions")
            )

        confidence = _normalise_confidence(result.get("confidence"))
        evidence = result.get("evidence")
        overall_dimensions = result.get("overall_dimensions")

        # Require both a numeric result, readable supporting text, and at
        # least moderate confidence. This prevents an unsupported visual
        # estimate from becoming a reported square footage.
        if (
            area is not None
            and confidence >= 0.60
            and isinstance(evidence, str)
            and evidence.strip()
        ):
            return round(area, 2), overall_dimensions, confidence, evidence

    except Exception as exc:
        print(f"[Blueprint Area Pass] Could not extract total area: {exc}")

    return None, None, 0.0, None


def analyze_blueprint(image_path):
    """
    Analyze an architectural blueprint using Gemini Vision.

    Returns:
        {
            "rooms": [...],
            "outdoor_spaces": [...],
            "corridors": [...],
            "exits": [...],
            "total_area_sqft": number | None,
            "raw_analysis": "...",
            "analysis_engine": "...",
            "image_dimensions": {...}
        }
    """

    # ---------------------------------------------------------
    # 1. Validate image
    # ---------------------------------------------------------
    if not os.path.exists(image_path):
        raise FileNotFoundError(
            f"Blueprint image not found: {image_path}"
        )

    try:
        with Image.open(image_path) as img:
            width, height = img.size
            format_name = img.format or "JPEG"
    except Exception as exc:
        raise ValueError(
            f"Invalid blueprint image: {exc}"
        ) from exc

    if not is_live_mode():
        raise RuntimeError(
            "Gemini API is not configured. "
            "Please configure GEMINI_API_KEY in .env."
        )

    llm = get_llm()

    if llm is None:
        raise RuntimeError(
            "Gemini model could not be initialized."
        )

    # ---------------------------------------------------------
    # 2. Pre-compress image & Encode (saves >60% vision tokens)
    # ---------------------------------------------------------
    optimized_bytes = optimize_image(image_path, max_dim=1600, quality=80)
    base64_image = base64.b64encode(optimized_bytes).decode("utf-8")

    # ---------------------------------------------------------
    # 3. Gemini prompt
    # ---------------------------------------------------------
    prompt = """
You are an expert architectural blueprint vision system.

Analyze ONLY the uploaded blueprint image.

IMAGE SIZE:
Width = IMAGE_WIDTH pixels
Height = IMAGE_HEIGHT pixels

============================================================
ROOM & SPACE EXTRACTION (ZERO AI MATH)
============================================================
Identify EVERY physically separate space visible inside the floor plan.
For each space, return its visible name, room_type, box_2d bounding box, and verbatim dimension annotations.
DO NOT perform calculations or area math. Extract text strings and bounding boxes ONLY.

Bounding box format:
"box_2d": [ymin, xmin, ymax, xmax]
Normalized integer coordinates from 0 to 1000.
(ymin: top, xmin: left, ymax: bottom, xmax: right)

============================================================
DIMENSION TEXT (READ VERBATIM)
============================================================
Read the exact dimension string printed on the blueprint for each room.
Examples: "3.00 x 3.60", "10'-0\\" x 12'-0\\"", "10 ft x 12 ft", "3.2m x 4.5m".
Record the verbatim text in "raw_dimension". Do NOT invent or estimate dimensions.

============================================================
OUTPUT STRUCTURE - HARD JSON REQUIREMENTS
============================================================
CRITICAL OUTPUT RULES:
- Return ONLY a single valid JSON object. Nothing else.
- DO NOT wrap the JSON in Markdown code fences (no ```json ... ```).
- DO NOT use backticks, DO NOT add any prose, explanation, or commas.
- DO NOT write any text before or after the JSON object.
- The very first character of your response must be '{' and the very last
  character must be '}'.
- Every key and string value must be double-quoted. Use no trailing commas.
- When a value cannot be detected, use the specified default
  (null for scalars, [] for arrays, "" for strings) - never omit the key,
  never invent data, never estimate dimensions.
- The output MUST be parseable by a strict JSON parser.

Return exactly this JSON structure:

{
  "plan_title": "",
  "scale": "",
  "rooms": [
    {
      "name": "BEDROOM 1",
      "room_type": "bedroom",
      "box_2d": [140, 220, 320, 420],
      "raw_dimension": "3.00 x 3.60",
      "doors": ["D1"],
      "windows": ["W1"],
      "confidence": 0.95
    }
  ],
  "corridors": [
    {
      "name": "Corridor",
      "box_2d": [420, 220, 480, 680],
      "raw_dimension": null,
      "confidence": 0.90
    }
  ],
  "exits": [
    {
      "name": "Main Entrance",
      "box_2d": [850, 400, 900, 500],
      "type": "door",
      "confidence": 0.95
    }
  ],
  "raw_analysis": "Brief description of the visible blueprint layout."
}

If a category has no detected entries, return an empty array, e.g.
"rooms": []. Never return null for rooms, corridors, or exits - always [].

REMEMBER: Output ONLY the JSON object. No markdown. No fences. No text before or after.
"""
    prompt = prompt.replace("IMAGE_WIDTH", str(width)).replace(
        "IMAGE_HEIGHT", str(height)
    )

    # ---------------------------------------------------------
    # 4. Send to Gemini
    # ---------------------------------------------------------
    message = HumanMessage(
        content=[
            {
                "type": "text",
                "text": prompt,
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": (
                        f"data:image/"
                        f"{format_name.lower()};base64,"
                        f"{base64_image}"
                    )
                },
            },
        ]
    )

    try:
        response = invoke_with_retry([message], temperature=0.1, max_tokens=2048)
    except Exception as exc:
        raise RuntimeError(
            "Blueprint analysis failed because Gemini "
            f"could not analyze the image: {exc}"
        ) from exc

    # ---------------------------------------------------------
    # 5. Parse Gemini JSON
    # ---------------------------------------------------------
    try:
        from agents.config import extract_text

        text = extract_text(
            response.content
        ).strip()

        data = _extract_json(text)

    except json.JSONDecodeError as exc:
        # Log a safely truncated representation of the raw response (which
        # must not contain API keys/secrets) to aid debugging, then raise a
        # clear, non-fabricating error.
        try:
            snippet = (text or "")[:4096]
            logger.error(
                "Blueprint analysis: Gemini returned invalid blueprint JSON. "
                "Raw response (truncated) follows:\n%s",
                snippet,
            )
        except Exception:
            logger.error("Blueprint analysis: Gemini returned invalid blueprint JSON.")
        raise RuntimeError(
            "Gemini returned invalid blueprint JSON."
        ) from exc

    except Exception as exc:
        raise RuntimeError(
            f"Could not process Gemini response: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(
            "Gemini returned an invalid blueprint structure."
        )

    logger.debug(
        "[Blueprint Area Debug] Gemini dimension fields: "
        f"total_area_sqft={data.get('total_area_sqft')!r}, "
        f"overall_dimensions={data.get('overall_dimensions')!r}, "
        f"overall_footprint_is_rectangular="
        f"{data.get('overall_footprint_is_rectangular')!r}, "
        f"dimension_evidence={data.get('dimension_evidence')!r}"
    )

    # ---------------------------------------------------------
    # 6. Validate rooms
    # ---------------------------------------------------------
    validated_rooms = []
    validated_outdoor_spaces = []
    room_classified_corridors = []

    rooms = data.get(
        "rooms",
        [],
    )

    if not isinstance(rooms, list):
        rooms = []

    for room in rooms:

        if not isinstance(room, dict):
            continue

        name = str(
            room.get("name", "")
        ).strip()

        if not name:
            continue

        coords = _normalise_coords(
            room.get("box_2d") or room.get("coords") or room,
            width,
            height,
        )

        if coords is None:
            continue

        confidence = _normalise_confidence(
            room.get("confidence")
        )

        if confidence < 0.45:
            continue

        room_type = str(
            room.get(
                "room_type",
                "unknown",
            )
        ).strip().lower()

        if not room_type:
            room_type = "unknown"

        dimensions = room.get("dimensions")
        raw_dim = room.get("raw_dimension")

        area_sqft = _room_area_sqft(room)
        if area_sqft is None and raw_dim:
            area_sqft = _parse_dimensions_sqft(raw_dim)

        if area_sqft is not None:
            area_sqft = round(area_sqft, 2)

        # Parse width/length from raw_dimension if structured dimensions missing
        if dimensions is None and raw_dim:
            dim_match = re.search(
                r"(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)", str(raw_dim)
            )
            if dim_match:
                d1 = float(dim_match.group(1))
                d2 = float(dim_match.group(2))
                unit = "m" if ("m" in str(raw_dim).lower() and "ft" not in str(raw_dim).lower()) else "ft"
                dimensions = {"length": d1, "width": d2, "unit": unit}

        logger.debug(
            "[Blueprint Area Debug] "
            f"room={name!r}, dimensions={dimensions!r}, "
            f"area_sqft={area_sqft!r}"
        )

        space_record = {
            "name": name,
            "room_type": room_type,
            "coords": coords,
            "area_sqft": (
                round(area_sqft, 2)
                if area_sqft is not None
                else None
            ),
            "dimensions": dimensions,
            "raw_dimension": raw_dim,
            "doors": room.get("doors", []),
            "windows": room.get("windows", []),
            "confidence": confidence,
        }

        if _is_outdoor_space(name, room_type):
            validated_outdoor_spaces.append(space_record)
            print(
                "[Blueprint Space Debug] Classified outdoor/non-room space: "
                f"{name!r} ({room_type})"
            )
            continue

        # Gemini may include a corridor in the generic rooms collection as
        # well as in its dedicated corridors collection. Preserve it as a
        # corridor, but never let it inflate the indoor room count.
        if room_type in {"corridor", "hallway"}:
            room_classified_corridors.append(
                {
                    "name": name,
                    "coords": coords,
                    "width_m": room.get("width_m"),
                    "length_m": room.get("length_m"),
                    "confidence": confidence,
                }
            )
            print(
                "[Blueprint Space Debug] Classified corridor space: "
                f"{name!r}"
            )
            continue

        validated_rooms.append(space_record)

    validated_rooms = _deduplicate_rooms(
        validated_rooms
    )
    validated_outdoor_spaces = _deduplicate_rooms(
        validated_outdoor_spaces
    )

    # ---------------------------------------------------------
    # 7. Validate corridors
    # ---------------------------------------------------------
    validated_corridors = list(room_classified_corridors)

    corridors = data.get(
        "corridors",
        [],
    )

    if not isinstance(corridors, list):
        corridors = []

    for corridor in corridors:

        if not isinstance(corridor, dict):
            continue

        coords = _normalise_coords(
            corridor.get("box_2d") or corridor.get("coords") or corridor,
            width,
            height,
        )

        if coords is None:
            continue

        confidence = _normalise_confidence(
            corridor.get("confidence")
        )

        if confidence < 0.45:
            continue

        validated_corridors.append(
            {
                "name": str(
                    corridor.get(
                        "name",
                        "Corridor",
                    )
                ).strip()
                or "Corridor",
                "coords": coords,
                "width_m": corridor.get("width_m"),
                "length_m": corridor.get("length_m"),
                "confidence": confidence,
            }
        )

    validated_corridors = _deduplicate_rooms(validated_corridors)

    # ---------------------------------------------------------
    # 8. Validate exits
    # ---------------------------------------------------------
    validated_exits = []

    exits = data.get(
        "exits",
        [],
    )

    if not isinstance(exits, list):
        exits = []

    for exit_item in exits:

        if not isinstance(exit_item, dict):
            continue

        coords = _normalise_coords(
            exit_item.get("box_2d") or exit_item.get("coords") or exit_item,
            width,
            height,
        )

        if coords is None:
            continue

        confidence = _normalise_confidence(
            exit_item.get("confidence")
        )

        if confidence < 0.45:
            continue

        validated_exits.append(
            {
                "name": str(
                    exit_item.get(
                        "name",
                        "Exit",
                    )
                ).strip()
                or "Exit",
                "coords": coords,
                "type": exit_item.get(
                    "type",
                    "door",
                ),
                "confidence": confidence,
            }
        )

    # ---------------------------------------------------------
    # 9. Calculate total area safely
    # ---------------------------------------------------------
    explicit_total = _parse_area_sqft(
        data.get("total_area_sqft")
    )

    # Gemini sometimes returns the number as a JSON numeric value.
    if explicit_total is None:
        numeric_total = _to_float(data.get("total_area_sqft"))
        if numeric_total is not None and numeric_total > 0:
            explicit_total = numeric_total

    calculated_total = None
    area_evidence = None
    area_dimensions = None

    if explicit_total is not None:
        calculated_total = round(explicit_total, 2)
        area_evidence = "Primary Gemini total_area_sqft"

    # The first vision pass may already have read reliable exterior
    # dimensions. Previously these values were discarded; use them only when
    # Gemini explicitly confirms the footprint is rectangular.
    elif data.get("overall_footprint_is_rectangular") is True:
        area_dimensions = data.get("overall_dimensions")
        overall_area = _parse_dimension_object(area_dimensions)
        if overall_area is not None:
            calculated_total = overall_area
            area_evidence = data.get("dimension_evidence")
            logger.debug(
                "[Blueprint Area Debug] Calculated total from primary "
                f"overall_dimensions={area_dimensions!r}: {overall_area} sqft"
            )

    if calculated_total is None:
        room_areas = []

        for room in validated_rooms:

            room_type = room.get(
                "room_type",
                "",
            ).lower()

            # Exclude outdoor/semi-outdoor spaces from indoor
            # built-up room-area calculation.
            if room_type in OUTDOOR_TYPES:
                continue

            area = _room_area_sqft(room)

            if area is not None and area > 0:
                room["area_sqft"] = round(area, 2)
                room_areas.append(float(area))

            logger.debug(
                "[Blueprint Area Debug] Indoor room calculation: "
                f"room={room.get('name')!r}, "
                f"dimensions={room.get('dimensions')!r}, area_sqft={area!r}"
            )

        # Only calculate if we have reliable areas for every
        # detected non-outdoor room. Otherwise leave null.
        indoor_rooms = [
            room
            for room in validated_rooms
            if room.get(
                "room_type",
                "",
            ).lower() not in OUTDOOR_TYPES
        ]

        if room_areas:
            calculated_total = round(sum(room_areas), 2)
            area_evidence = "Sum of readable enclosed-room dimensions"

    # ---------------------------------------------------------
    # 10. Final result
    # ---------------------------------------------------------
    area_request = None
    if calculated_total is None:
        area_request = {
            "required": True,
            "message": (
                "No readable numeric dimensions or printed total area were "
                "found. Please provide the overall building measurement "
                "(for example, 30 ft x 40 ft) or the drawing scale together "
                "with an explicitly stated overall dimension."
            ),
            "accepted_examples": [
                "30 ft x 40 ft",
                "30'-0\" x 40'-0\"",
                "9.14 m x 12.19 m",
                "Built-up area: 1200 sq ft",
            ],
        }

    result = {
        "rooms": validated_rooms,
        "outdoor_spaces": validated_outdoor_spaces,
        "corridors": validated_corridors,
        "exits": validated_exits,
        "total_area_sqft": calculated_total,
        "area_evidence": area_evidence,
        "area_dimensions": area_dimensions,
        "area_request": area_request,
        "raw_analysis": data.get(
            "raw_analysis",
            "Blueprint analyzed using Gemini Vision.",
        ),
        "analysis_engine": (
            "Gemini Vision"
        ),
        "image_dimensions": {
            "width": width,
            "height": height,
        },
    }

    logger.info(
        "Blueprint analysis successful: "
        f"{len(validated_rooms)} rooms detected."
    )

    logger.debug(
        "[Blueprint Area Debug] Final total_area_sqft: "
        f"{calculated_total if calculated_total is not None else 'Not available'}"
    )

    return result

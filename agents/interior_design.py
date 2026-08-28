"""
BuildSense — Interior Design Agent

Generates interior design recommendations ONLY for rooms
identified by the Blueprint Analysis Agent.
"""

import json
import logging

from agents.config import get_llm, is_live_mode
from agents.memory import SharedMemoryBus


logger = logging.getLogger(__name__)


def generate_interior_design(
    spatial_data,
    query=None,
    style_preset="modern_minimalist",
    memory_bus=None
):
    """
    Generate interior design recommendations for the
    validated rooms extracted from the blueprint.

    The agent NEVER creates additional rooms.

    ``memory_bus`` is the coordinator's per-run shared-memory bus.  When
    omitted, a fresh private bus is used so this agent can never read
    context from any other pipeline run.
    """

    from agents.tools import tool_registry

    if memory_bus is None:
        memory_bus = SharedMemoryBus()

    compliance_result = memory_bus.read("compliance_result")
    compliance_context = (
        compliance_result.get("summary_findings", "No compliance findings shared.")
        if isinstance(compliance_result, dict)
        else "No compliance findings shared."
    )
    logger.info(
        "Interior Design Agent received compliance context: available=%s",
        isinstance(compliance_result, dict),
    )

    log_start = len(
        tool_registry.get_audit_log()
    )

    # ---------------------------------------------------------
    # 1. Validate spatial data
    # ---------------------------------------------------------
    if not isinstance(spatial_data, dict):
        raise ValueError(
            "Invalid spatial data received by Interior Design Agent."
        )

    rooms = spatial_data.get("rooms", [])

    if not isinstance(rooms, list):
        raise ValueError(
            "Invalid rooms data received from Blueprint Agent."
        )

    # Only use rooms actually returned by Blueprint Agent.
    validated_rooms = []

    for room in rooms:

        if not isinstance(room, dict):
            continue

        room_name = str(
            room.get("name", "")
        ).strip()

        if not room_name:
            continue

        coords = room.get("coords")
        if coords is not None:
            if not isinstance(coords, list) or len(coords) != 4:
                continue
            try:
                x, y, width, height = [float(value) for value in coords]
            except (TypeError, ValueError):
                continue
            if x < 0 or y < 0 or width <= 0 or height <= 0:
                continue
            if x > 100 or y > 100 or x + width > 100 or y + height > 100:
                continue

        # If Blueprint Agent supplied confidence,
        # don't use extremely uncertain rooms.
        confidence = room.get(
            "confidence",
            1.0
        )

        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0

        if confidence < 0.45:
            continue

        validated_rooms.append(room)

    # ---------------------------------------------------------
    # No rooms detected
    # ---------------------------------------------------------
    if not validated_rooms:
        return {
            "overall_theme": (
                "No validated rooms were detected in the "
                "uploaded blueprint. Interior design generation "
                "was not performed."
            ),
            "rooms": [],
            "compliance_context_available": isinstance(compliance_result, dict),
            "tool_calls": _extract_tool_calls(
                tool_registry,
                log_start
            )
        }

    # ---------------------------------------------------------
    # 2. Get curated design catalog
    # ---------------------------------------------------------
    catalog_result = tool_registry.invoke(
        "get_design_catalog",
        style=style_preset,
        category="all"
    )

    catalog = (
        catalog_result.get("output", {})
        if catalog_result.get("status") == "success"
        else {}
    )

    # ---------------------------------------------------------
    # 3. Groq / Gemini Design Agent (Groq preferred for text synthesis)
    # ---------------------------------------------------------
    if catalog:
        from agents.config import is_groq_available, invoke_groq_with_retry, invoke_with_retry, extract_text
        from langchain_core.messages import HumanMessage

        prompt = f"""
You are an expert Interior Design AI system for BuildSense.

You MUST design ONLY the rooms provided in the Blueprint Analysis Agent's validated spatial data.
Do NOT create additional rooms. Do NOT invent rooms that are not in the list below.

--------------------------------------------------
VALIDATED BLUEPRINT ROOMS & DIMENSIONS (SOURCE OF TRUTH)
--------------------------------------------------
{json.dumps(validated_rooms, indent=2)}

--------------------------------------------------
DESIGN STYLE PRESET
--------------------------------------------------
{style_preset}

--------------------------------------------------
COMPLIANCE CONTEXT
--------------------------------------------------
{compliance_context}

--------------------------------------------------
CURATED DESIGN CATALOG REFERENCE
--------------------------------------------------
{json.dumps(catalog, indent=2)}

--------------------------------------------------
USER DESIGN REQUEST
--------------------------------------------------
{query if query else "Generate unique, room-specific interior design recommendations tailored to each room's type and dimensions."}

--------------------------------------------------
INSTRUCTIONS
--------------------------------------------------
For EVERY room in the validated blueprint data:
1. Keep the EXACT room name and room_type.
2. Tailor materials, color palette, lighting, and furniture SPECIFICALLY to that room's type:
   - For a BEDROOM: suggest a Bed (King/Queen), Wardrobe, Nightstands, Bedside lamps, Wooden/Vinyl flooring.
   - For a KITCHEN: suggest Modular Cabinets, Countertops (Granite/Quartz), Chimney/Hob, Backsplash Tiles.
   - For a TOILET/BATHROOM: suggest Vanity Basin, Shower Enclosure, Anti-skid Floor Tiles, Mirror Cabinet.
   - For a LIVING ROOM: suggest Sofa Set, Coffee Table, TV Unit, Accent Chair, Ambient/Chandelier Lighting.
   - For a DINING AREA: suggest Dining Table Set, Sideboard, Pendant Lights.
   - For STORE / UTILITY / FOYER / PARKING / PORCH: suggest room-appropriate storage, lighting, and durable finishes.
3. DO NOT repeat the same furniture items (like "Sofa Set") across bedrooms or bathrooms. Each room MUST receive functionally accurate furniture for its room_type!
4. Return ONLY valid JSON.

Use exactly this JSON schema:
{{
    "overall_theme": "Cohesive design concept description for the overall space",
    "rooms": [
        {{
            "room_name": "EXACT name from blueprint",
            "room_type": "room_type from blueprint",
            "function": "Primary function of this space",
            "color_palette": {{
                "primary": "#HEX",
                "accent": "#HEX"
            }},
            "materials": {{
                "flooring": "Material description (e.g. Italian Marble / Vitrified Tiles / Anti-skid Tiles)",
                "walls": "Wall finish description (e.g. Emulsion Paint with Accent Texture)"
            }},
            "furniture": [
                {{
                    "item": "Specific furniture item appropriate for this room type",
                    "placement": "Specific layout placement advice"
                }}
            ],
            "lighting": "Specific lighting recommendation"
        }}
    ]
}}
"""
        try:
            if is_groq_available():
                logger.info("Interior Design Agent invoking Groq for room-specific design generation...")
                try:
                    text = invoke_groq_with_retry(prompt, temperature=0.5, max_tokens=2048).strip()
                except Exception as exc:
                    logger.error("Interior Design Agent Groq call failed (%s); falling back to Gemini.", exc)
                    res = invoke_with_retry([HumanMessage(content=prompt)], temperature=0.5, max_tokens=2048)
                    text = extract_text(res.content).strip()
            else:
                logger.info("Interior Design Agent invoking Gemini for room-specific design generation...")
                res = invoke_with_retry([HumanMessage(content=prompt)], temperature=0.5, max_tokens=2048)
                text = extract_text(res.content).strip()

            if text.startswith("```json"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]

            result = json.loads(
                text.strip()
            )

            # -------------------------------------------------
            # Validate Gemini's design output
            # -------------------------------------------------
            result = _validate_design_output(
                result,
                validated_rooms
            )

            result["tool_calls"] = (
                _extract_tool_calls(
                    tool_registry,
                    log_start
                )
            )
            result["compliance_context_available"] = isinstance(
                compliance_result,
                dict,
            )

            logger.info("Interior Design Agent completed generation successfully.")
            return result

        except Exception as e:

            logger.warning("Gemini Interior Design Agent failed: %s", e)

            # IMPORTANT:
            # Do NOT invent rooms when Gemini fails.
            # Continue with deterministic catalog-based
            # designs for the validated rooms only.

    # ---------------------------------------------------------
    # 4. Deterministic fallback
    #
    # IMPORTANT:
    # This fallback uses ONLY validated blueprint rooms.
    # ---------------------------------------------------------

    style_name = catalog.get(
        "style",
        "Modern Minimalist"
    )

    colors = catalog.get(
        "color_palette",
        {}
    )

    # Distinct per-room colors: cycle deterministically through the style's
    # own palette so different rooms never render with identical swatches.
    room_palette = _distinct_palette(list(colors.values()))
    if not room_palette:
        room_palette = ["#2C3E50", "#E67E22", "#1ABC9C"]
    if len(room_palette) < 2:
        room_palette.append("#CCCCCC")

    materials = catalog.get(
        "materials",
        {}
    )

    furniture = catalog.get(
        "furniture",
        {}
    )

    rooms_design = []

    for room_index, room in enumerate(validated_rooms):

        room_name = room.get(
            "name",
            "Room"
        )

        room_type = _get_room_type(
            room
        )

        furniture_items = furniture.get(
            room_type,
            furniture.get(
                "general",
                [
                    {"name": "Standard Furniture"},
                    {"name": "Ergonomic Seating"}
                ]
            )
        )

        placement_logic = (
            "Arrange furniture while maintaining "
            "comfortable circulation and access."
        )

        if room_type == "bedroom":

            placement_logic = (
                "Place the bed against a suitable solid wall "
                "and keep clear circulation around the bed."
            )

        elif room_type == "living_room":

            placement_logic = (
                "Arrange seating around a central focal point "
                "while maintaining clear circulation."
            )

        elif room_type == "kitchen":

            placement_logic = (
                "Keep the work triangle efficient and "
                "maintain clear movement between work zones."
            )

        elif room_type == "bathroom":

            placement_logic = (
                "Maintain clear circulation around sanitary "
                "fixtures and adequate access space."
            )

        elif room_type == "office":

            placement_logic = (
                "Position the desk near suitable natural light "
                "while maintaining clear circulation."
            )

        furniture_list = []

        for item in furniture_items[:3]:

            furniture_list.append(
                {
                    "item": item.get(
                        "name",
                        "Standard Furniture"
                    ),
                    "placement": placement_logic
                }
            )

        rooms_design.append(
            {
                "room_name": room_name,

                "function": (
                    room_type
                    .replace("_", " ")
                    .title()
                ),

                "color_palette": {
                    "primary": room_palette[
                        room_index % len(room_palette)
                    ],
                    "accent": room_palette[
                        (room_index + 1) % len(room_palette)
                    ]
                },

                "materials": {
                    "flooring": materials.get(
                        "flooring",
                        {}
                    ).get(
                        "material",
                        "Standard flooring"
                    ),

                    "walls": materials.get(
                        "wall_treatment",
                        {}
                    ).get(
                        "material",
                        "Standard walls"
                    )
                },

                "furniture": furniture_list,

                "lighting": (
                    "Provide ambient lighting with "
                    "task-focused lighting where required."
                )
            }
        )

    return {
        "overall_theme": (
            f"A cohesive {style_name} design "
            "based only on the validated rooms "
            "detected in the blueprint."
        ),

        "rooms": rooms_design,

        "compliance_context_available": isinstance(compliance_result, dict),

        "tool_calls": _extract_tool_calls(
            tool_registry,
            log_start
        )
    }


# =========================================================
# Helper: Determine room type
# =========================================================

def _get_room_type(room):
    """
    Prefer the room_type supplied by the Blueprint Agent.

    Only fall back to the room name when room_type
    is unavailable.
    """

    supplied_type = str(
        room.get(
            "room_type",
            ""
        )
    ).strip().lower()

    if supplied_type and supplied_type != "unknown":
        return supplied_type

    name = str(
        room.get(
            "name",
            ""
        )
    ).lower()

    if any(
        word in name
        for word in [
            "bedroom",
            "bed room",
            "sleeping"
        ]
    ):
        return "bedroom"

    if any(
        word in name
        for word in [
            "living",
            "drawing",
            "lounge"
        ]
    ):
        return "living_room"

    if any(
        word in name
        for word in [
            "kitchen",
            "cook"
        ]
    ):
        return "kitchen"

    if any(
        word in name
        for word in [
            "bath",
            "toilet",
            "washroom",
            "restroom"
        ]
    ):
        return "bathroom"

    if any(
        word in name
        for word in [
            "office",
            "study"
        ]
    ):
        return "office"

    if any(
        word in name
        for word in [
            "conference",
            "meeting"
        ]
    ):
        return "conference"

    return "general"


# =========================================================
# Helper: Distinct color palette extraction
# =========================================================

def _hex_to_rgb(value):
    """
    Parse a hex color string ("#RRGGBB" or "#RGB")
    into an (r, g, b) tuple. Returns None on failure.
    """

    value = str(
        value
    ).strip().lstrip("#")

    if len(value) == 3:
        value = "".join(
            ch * 2 for ch in value
        )

    if len(value) != 6:
        return None

    try:
        return tuple(
            int(value[i: i + 2], 16)
            for i in (0, 2, 4)
        )
    except ValueError:
        return None


def _distinct_palette(hex_colors, min_distance=120):
    """
    Filter a style palette down to visually distinguishable
    colors, preserving order. Deterministic — same input,
    same output, no randomness.

    ``min_distance`` is the minimum Manhattan RGB distance
    between any two retained colors (drops near-duplicate
    whites/creams that would render as identical swatches).
    """

    picked_rgb = []
    picked_hex = []

    for hex_value in hex_colors:

        rgb = _hex_to_rgb(hex_value)

        if rgb is None:
            continue

        normalized = (
            "#"
            + str(hex_value).strip().lstrip("#").upper()
        )

        is_distinct = all(
            sum(
                abs(a - b)
                for a, b in zip(rgb, existing)
            ) >= min_distance
            for existing in picked_rgb
        )

        if is_distinct and normalized not in picked_hex:
            picked_rgb.append(rgb)
            picked_hex.append(normalized)

    return picked_hex


# =========================================================
# Helper: Validate Gemini design output
# =========================================================

def _validate_design_output(
    result,
    validated_rooms
):
    """
    Ensure Gemini did not invent or remove rooms.
    """

    if not isinstance(
        result,
        dict
    ):
        raise ValueError(
            "Gemini returned an invalid design structure."
        )

    generated_rooms = result.get(
        "rooms",
        []
    )

    if not isinstance(
        generated_rooms,
        list
    ):
        raise ValueError(
            "Gemini returned invalid room designs."
        )

    expected_names = [
        str(room.get("name", "")).strip()
        for room in validated_rooms
    ]

    generated_by_name = {}

    for design in generated_rooms:

        if not isinstance(
            design,
            dict
        ):
            continue

        name = str(
            design.get(
                "room_name",
                ""
            )
        ).strip()

        if name:
            generated_by_name[name] = design

    final_rooms = []

    for expected_name in expected_names:

        design = generated_by_name.get(
            expected_name
        )

        if design is not None:
            final_rooms.append(
                design
            )

    # The model must not silently invent/remove rooms.
    if len(final_rooms) != len(
        validated_rooms
    ):
        raise ValueError(
            "Gemini Interior Design Agent did not "
            "return a design for every validated room."
        )

    result["rooms"] = final_rooms

    return result


# =========================================================
# Tool trace
# =========================================================

def _extract_tool_calls(
    tool_registry,
    log_start: int
) -> list:

    return tool_registry.get_audit_log()[
        log_start:
    ]

"""
BuildSense Tool: NBC Code Lookup
BuildSense Tool

Provides structured lookup of National Building Code of India (NBC 2016) regulations.
Fully offline — no external API calls required.
Used by the Code Compliance Agent to retrieve precise, auditable regulation text.
"""

from typing import Optional

# ---------------------------------------------------------------------------
# NBC 2016 Regulation Knowledge Base
# Structure: PART → CLAUSE → {title, text, min_value, unit, applicability, severity}
# ---------------------------------------------------------------------------
NBC_REGULATIONS = {
    "Part 3": {
        "title": "Development Control Rules and General Building Requirements",
        "clauses": {
            "6.2": {
                "title": "Open Space / Setback Requirements",
                "text": (
                    "Every building shall have open spaces around it. The minimum open "
                    "spaces shall be as prescribed by the local municipal authority. For "
                    "plotted developments, a minimum front setback of 3.0m, side setbacks "
                    "of 1.5m (each side), and a rear setback of 3.0m are typically required "
                    "for buildings up to 10m in height."
                ),
                "min_value": 3.0,
                "unit": "meters (front/rear setback)",
                "applicability": "All new constructions and major extensions on plotted land",
                "severity": "MEDIUM",
                "penalty": "Municipal authority may refuse occupancy certificate"
            },
            "6.4": {
                "title": "Floor Area Ratio (FAR) / Floor Space Index (FSI)",
                "text": (
                    "The maximum permissible Floor Area Ratio (FAR) or Floor Space Index (FSI) "
                    "shall be as notified by the local planning authority. Typically 1.0 to 2.5 "
                    "for residential and 1.5 to 3.0 for commercial uses in urban areas."
                ),
                "min_value": None,
                "unit": "ratio (area-specific)",
                "applicability": "All new constructions — verify with local zoning",
                "severity": "MEDIUM",
                "penalty": "Excess construction may be demolished by local authority"
            },
            "6.6": {
                "title": "Minimum Room Dimensions",
                "text": (
                    "No habitable room shall have a floor area less than 9.5 sq m. The "
                    "minimum width of any habitable room shall not be less than 2.4 m. "
                    "Kitchens shall not be less than 4.5 sq m with a minimum width of 1.8 m."
                ),
                "min_value": 9.5,
                "unit": "square meters (habitable room)",
                "applicability": "Residential and commercial office occupancies",
                "severity": "MEDIUM",
                "penalty": "Room may be classified as non-habitable; affects occupancy permit"
            }
        }
    },
    "Part 4": {
        "title": "Fire and Life Safety",
        "clauses": {
            "4.2": {
                "title": "Number of Exits Required",
                "text": (
                    "Every floor and basement shall have a minimum of two exits remote from "
                    "each other for areas exceeding 250 sq m or occupancy exceeding 50 persons. "
                    "Exits shall be arranged so that the travel distance to the nearest exit "
                    "does not exceed 30 m (in sprinklered buildings, 45 m)."
                ),
                "min_value": 2,
                "unit": "exits",
                "applicability": "All commercial, assembly, and institutional occupancies > 250 sq m",
                "severity": "HIGH",
                "penalty": "Building shall not be granted occupancy certificate; immediate closure order"
            },
            "4.2.1": {
                "title": "Exit Remoteness Requirement",
                "text": (
                    "Where more than one exit is required, they shall be placed as remote "
                    "from each other as possible and shall be accessible from any part of the "
                    "building without passing through the other exit. Diagonal distance between "
                    "exits shall not be less than one-third the length of the maximum diagonal "
                    "of the floor area served."
                ),
                "min_value": None,
                "unit": "spatial separation (one-third diagonal rule)",
                "applicability": "Commercial, assembly, and institutional occupancies",
                "severity": "CRITICAL",
                "penalty": "Non-compliance invalidates fire safety NOC"
            },
            "4.3": {
                "title": "Exit Access Corridor / Aisle Width",
                "text": (
                    "The minimum clear width of corridors, aisles, and passageways used as "
                    "exit access shall not be less than 1.0 m for occupant loads below 50 "
                    "persons and not less than 1.2 m for occupant loads of 50 or more persons "
                    "in Group B (commercial/office) and Group C (educational) occupancies. "
                    "For Group A (residential) occupancies, the minimum is 0.9 m."
                ),
                "min_value": 1.2,
                "unit": "meters (commercial occupancy corridor)",
                "applicability": "Commercial offices, institutional buildings, educational facilities",
                "severity": "HIGH",
                "penalty": "Structural modification mandatory; fire NOC withheld until compliant"
            },
            "4.4": {
                "title": "Staircase Width Requirements",
                "text": (
                    "Internal staircases shall have a minimum width of 1.2 m for occupant loads "
                    "of 50 or more. For buildings above 15 m in height, the minimum staircase "
                    "width is 1.5 m. Staircases shall be enclosed in fire-rated (2-hour) "
                    "construction for buildings above 4 floors."
                ),
                "min_value": 1.2,
                "unit": "meters (staircase width)",
                "applicability": "All multi-storey commercial and institutional buildings",
                "severity": "CRITICAL",
                "penalty": "Non-compliant staircases must be reconstructed before occupancy"
            },
            "4.5": {
                "title": "Travel Distance to Exit",
                "text": (
                    "The maximum travel distance from any point on a floor to the nearest exit "
                    "shall not exceed 30 m for unsprinklered buildings and 45 m for buildings "
                    "with a complete automatic fire sprinkler system. Dead-end corridors shall "
                    "not exceed 6 m."
                ),
                "min_value": None,
                "unit": "30m unsprinklered / 45m sprinklered",
                "applicability": "Commercial, institutional, and assembly occupancies",
                "severity": "CRITICAL",
                "penalty": "Floor layout must be redesigned; exit relocation required"
            },
            "4.8": {
                "title": "Fire Extinguisher Placement",
                "text": (
                    "Portable fire extinguishers shall be placed such that no person needs to "
                    "travel more than 15 m to reach one. One extinguisher of minimum 4.5 kg CO₂ "
                    "or 9 litre water type shall be provided for every 200 sq m of floor area."
                ),
                "min_value": 1,
                "unit": "extinguisher per 200 sq m",
                "applicability": "All commercial and institutional occupancies",
                "severity": "MEDIUM",
                "penalty": "Fire safety NOC withheld; penalty under local fire act"
            }
        }
    },
    "Part 8": {
        "title": "Building Services — Section 2: Electrical and Allied Installations",
        "clauses": {
            "2.3": {
                "title": "Earthing and Grounding of Electrical Systems",
                "text": (
                    "All metallic parts of electrical installations and equipment shall be "
                    "effectively earthed. Earthing systems shall comply with IS 3043. "
                    "Earth resistance shall not exceed 1 ohm for main earthing in buildings "
                    "with sensitive electronic equipment."
                ),
                "min_value": None,
                "unit": "< 1 ohm earth resistance",
                "applicability": "All electrical installations in buildings",
                "severity": "CRITICAL",
                "penalty": "Electrical inspector may withhold connection approval"
            }
        }
    }
}


def lookup_nbc_rule(
    part: Optional[str] = None,
    clause: Optional[str] = None,
    query: Optional[str] = None,
) -> dict:
    """
    Look up a specific clause in the NBC 2016.
    
    Args:
        part:   NBC Part identifier, e.g. 'Part 4', 'Part 3'.
        clause: Clause number, e.g. '4.3', '4.2.1', '6.2'.
        query:  Optional plain-language lookup, e.g. 'corridor width'.
    
    Returns:
        dict with title, text, min_value, unit, applicability, severity, penalty
    
    Raises:
        KeyError: If part or clause not found in the knowledge base
    """
    if (not part or not clause) and query:
        query_key = query.lower()
        if "corridor" in query_key or "aisle" in query_key:
            part, clause = "Part 4", "4.3"
        elif "exit" in query_key or "egress" in query_key:
            part, clause = "Part 4", "4.2"
        elif "setback" in query_key or "open space" in query_key:
            part, clause = "Part 3", "6.2"

    if not part or not clause:
        raise ValueError("Provide an NBC part/clause or a supported lookup query.")

    # Normalize input
    part_key = part.strip().title()  # "Part 4"
    clause_key = clause.strip()

    if part_key not in NBC_REGULATIONS:
        available_parts = list(NBC_REGULATIONS.keys())
        raise KeyError(
            f"NBC Part '{part}' not found in knowledge base. "
            f"Available parts: {', '.join(available_parts)}"
        )
    
    part_data = NBC_REGULATIONS[part_key]
    clauses = part_data.get("clauses", {})
    
    if clause_key not in clauses:
        available_clauses = list(clauses.keys())
        # Return a structured "not found" response instead of raising
        return {
            "found": False,
            "part": part_key,
            "clause": clause_key,
            "message": (
                f"Clause {clause_key} not found in {part_key} knowledge base. "
                f"Available clauses in {part_key}: {', '.join(available_clauses)}. "
                "Please verify the clause number or consult the official NBC 2016 document."
            ),
            "source": "NBC 2016 — BuildSense Knowledge Base (Partial Coverage)"
        }
    
    clause_data = clauses[clause_key]
    return {
        "found": True,
        "part": part_key,
        "part_title": part_data["title"],
        "clause": clause_key,
        "title": clause_data["title"],
        "regulation_text": clause_data["text"],
        "minimum_required_value": clause_data.get("min_value"),
        "required_value": (
            f"{clause_data.get('min_value')} {clause_data.get('unit')}"
            if clause_data.get("min_value") is not None
            else clause_data.get("unit")
        ),
        "unit": clause_data.get("unit"),
        "applicability": clause_data.get("applicability"),
        "severity": clause_data.get("severity", "INFO"),
        "non_compliance_penalty": clause_data.get("penalty", "Consult local authority"),
        "full_citation": f"NBC 2016 {part_key}, Clause {clause_key}: {clause_data['title']}",
        "source": "NBC 2016 — BuildSense Knowledge Base"
    }


def get_all_clauses() -> dict:
    """Returns a summary of all available NBC parts and clauses."""
    summary = {}
    for part, data in NBC_REGULATIONS.items():
        summary[part] = {
            "title": data["title"],
            "available_clauses": {
                c: v["title"] for c, v in data.get("clauses", {}).items()
            }
        }
    return summary

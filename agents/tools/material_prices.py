"""
BuildSense Tool: Material Price Lookup

Provides construction material prices in INR from a curated regional
baseline rate card (2025–2026 reference rates for Pune/Mumbai/Delhi/
Bangalore). This is a static reference dataset maintained in code —
NOT a live market feed. Figures are suitable for planning-grade
estimates; verify against supplier quotations before procurement.
"""

from typing import Optional


# ---------------------------------------------------------------------------
# Regional rate database (INR per standard unit, 2025–2026 market rates)
# ---------------------------------------------------------------------------
MATERIAL_RATES = {
    "cement": {
        "unit": "50kg bag",
        "rates": {
            "Pune": 380,
            "Mumbai": 410,
            "Delhi": 360,
            "Bangalore": 395,
            "default": 385
        },
        "description": "OPC 53 Grade Portland Cement"
    },
    "steel_rebar": {
        "unit": "kg",
        "rates": {
            "Pune": 58,
            "Mumbai": 61,
            "Delhi": 55,
            "Bangalore": 60,
            "default": 58
        },
        "description": "Fe 500D TMT Steel Reinforcement Bar"
    },
    "aac_blocks": {
        "unit": "cubic meter",
        "rates": {
            "Pune": 4800,
            "Mumbai": 5200,
            "Delhi": 4600,
            "Bangalore": 5000,
            "default": 4900
        },
        "description": "Autoclaved Aerated Concrete (AAC) Blocks 600x200x150mm"
    },
    "river_sand": {
        "unit": "cubic meter",
        "rates": {
            "Pune": 1800,
            "Mumbai": 2200,
            "Delhi": 1600,
            "Bangalore": 2000,
            "default": 1900
        },
        "description": "M-Sand / River Sand for Masonry and Plastering"
    },
    "crushed_aggregate": {
        "unit": "cubic meter",
        "rates": {
            "Pune": 1400,
            "Mumbai": 1600,
            "Delhi": 1300,
            "Bangalore": 1500,
            "default": 1400
        },
        "description": "20mm Crushed Stone Aggregate for RCC Work"
    },
    "vitrified_tiles": {
        "unit": "square meter",
        "rates": {
            "Pune": 420,
            "Mumbai": 480,
            "Delhi": 400,
            "Bangalore": 450,
            "default": 430
        },
        "description": "600x600mm Double-Charged Vitrified Floor Tiles"
    },
    "standard_tiles": {
        "unit": "square meter",
        "rates": {
            "Pune": 280,
            "Mumbai": 320,
            "Delhi": 260,
            "Bangalore": 300,
            "default": 285
        },
        "description": "300x300mm Standard Ceramic Floor/Wall Tiles"
    },
    "exterior_paint": {
        "unit": "litre",
        "rates": {
            "Pune": 210,
            "Mumbai": 225,
            "Delhi": 200,
            "Bangalore": 215,
            "default": 210
        },
        "description": "Acrylic Weather Shield Exterior Emulsion Paint"
    },
    "interior_paint": {
        "unit": "litre",
        "rates": {
            "Pune": 160,
            "Mumbai": 175,
            "Delhi": 150,
            "Bangalore": 165,
            "default": 160
        },
        "description": "Premium Acrylic Interior Emulsion Paint"
    },
    "gypsum_board": {
        "unit": "square meter",
        "rates": {
            "Pune": 320,
            "Mumbai": 350,
            "Delhi": 300,
            "Bangalore": 330,
            "default": 320
        },
        "description": "12.5mm Standard Gypsum Plasterboard for False Ceiling/Partitions"
    },
    "electrical_conduit": {
        "unit": "meter",
        "rates": {
            "Pune": 45,
            "Mumbai": 50,
            "Delhi": 42,
            "Bangalore": 47,
            "default": 45
        },
        "description": "25mm CPVC / PVC Electrical Conduit Pipe"
    },
    "cpvc_pipe": {
        "unit": "meter",
        "rates": {
            "Pune": 95,
            "Mumbai": 105,
            "Delhi": 88,
            "Bangalore": 98,
            "default": 95
        },
        "description": "25mm CPVC Plumbing Pipe (Hot & Cold Water)"
    }
}

VALID_MATERIALS = list(MATERIAL_RATES.keys())


def get_material_price(
    material: str,
    region: str = "Pune",
    quantity_units: float = 1.0
) -> dict:
    """
    Look up the current market price for a construction material in a given region.
    
    Args:
        material: Material key (e.g. 'cement', 'steel_rebar', 'vitrified_tiles')
        region:   City name (e.g. 'Pune', 'Mumbai', 'Delhi', 'Bangalore')
        quantity_units: Quantity in the material's standard unit
    
    Returns:
        dict with unit_price_inr, total_price_inr, unit, description, region, source
    
    Raises:
        ValueError: If material key is not recognized
    """
    material_key = material.lower().replace(" ", "_").replace("-", "_")
    
    if material_key not in MATERIAL_RATES:
        raise ValueError(
            f"Unknown material '{material}'. "
            f"Valid options: {', '.join(VALID_MATERIALS)}"
        )
    
    rate_data = MATERIAL_RATES[material_key]
    region_rates = rate_data["rates"]
    
    # Use exact region match, then default
    unit_price = region_rates.get(region, region_rates.get("default", 0))
    total_price = round(unit_price * quantity_units, 2)
    
    return {
        "material": material_key,
        "description": rate_data["description"],
        "region": region,
        "unit": rate_data["unit"],
        "quantity": quantity_units,
        "currency": "INR",
        "unit_price_inr": unit_price,
        "total_price_inr": total_price,
        "formatted_total": f"₹{total_price:,.0f}",
        "source": "BuildSense Regional Rate Database (Simulation) — 2025-26 Market Rates"
    }


def get_available_materials() -> list:
    """Returns list of all material keys supported by this tool."""
    return VALID_MATERIALS

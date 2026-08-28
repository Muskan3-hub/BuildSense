"""
BuildSense — Interior Design Catalog Tool
BuildSense Tool

Enterprise tool providing curated interior design reference data:
- Furniture items with dimensions, price ranges, and style tags
- Color palette presets per design style
- Material finish options and lighting specifications
"""

from typing import Optional

STYLE_PRESETS = {
    "modern_minimalist": {
        "name": "Modern Minimalist",
        "description": "Clean lines, neutral palette, functional furniture with Scandinavian influence.",
        "icon": "fa-solid fa-minimize",
        "color_palette": {
            "primary_wall": "#F5F0EB", "accent_wall": "#2C3E50", "trim": "#FFFFFF",
            "accent_1": "#E67E22", "accent_2": "#1ABC9C", "ceiling": "#FAFAFA",
        },
        "mood_keywords": ["serene", "airy", "uncluttered", "light-filled"],
    },
    "traditional_indian": {
        "name": "Traditional Indian",
        "description": "Rich wood tones, warm earthy colors, brass accents, jali patterns.",
        "icon": "fa-solid fa-om",
        "color_palette": {
            "primary_wall": "#FFF5E6", "accent_wall": "#8B4513", "trim": "#D4A76A",
            "accent_1": "#C0392B", "accent_2": "#D4AC0D", "ceiling": "#FFF8F0",
        },
        "mood_keywords": ["warm", "ornate", "heritage", "handcrafted"],
    },
    "contemporary": {
        "name": "Contemporary",
        "description": "Bold geometric patterns, mixed materials, statement lighting.",
        "icon": "fa-solid fa-shapes",
        "color_palette": {
            "primary_wall": "#ECEFF1", "accent_wall": "#37474F", "trim": "#B0BEC5",
            "accent_1": "#7C4DFF", "accent_2": "#00BCD4", "ceiling": "#FAFAFA",
        },
        "mood_keywords": ["bold", "curated", "dynamic", "artistic"],
    },
    "industrial": {
        "name": "Industrial",
        "description": "Exposed brick, metal fixtures, concrete surfaces, Edison bulbs.",
        "icon": "fa-solid fa-industry",
        "color_palette": {
            "primary_wall": "#D7CCC8", "accent_wall": "#4E342E", "trim": "#795548",
            "accent_1": "#FF6F00", "accent_2": "#546E7A", "ceiling": "#EFEBE9",
        },
        "mood_keywords": ["raw", "urban", "textured", "robust"],
    },
}

FURNITURE_CATALOG = {
    "bedroom": [
        {"name": "King/Queen Bed Frame with Headboard", "icon": "fa-solid fa-bed", "dimensions": "200x180 cm", "price_range_inr": "25,000-65,000", "styles": ["modern_minimalist", "traditional_indian", "contemporary", "industrial"]},
        {"name": "Full-Height Sliding Wardrobe", "icon": "fa-solid fa-door-closed", "dimensions": "210x60 cm", "price_range_inr": "35,000-95,000", "styles": ["modern_minimalist", "traditional_indian", "contemporary", "industrial"]},
        {"name": "Matching Nightstands (Pair)", "icon": "fa-solid fa-table-cells", "dimensions": "45x45 cm", "price_range_inr": "6,000-18,000", "styles": ["modern_minimalist", "traditional_indian", "contemporary", "industrial"]},
    ],
    "kitchen": [
        {"name": "Modular Base & Overhead Cabinets", "icon": "fa-solid fa-cubes", "dimensions": "Custom linear/L-shape", "price_range_inr": "80,000-2,50,000", "styles": ["modern_minimalist", "traditional_indian", "contemporary", "industrial"]},
        {"name": "Quartz / Granite Worktop Counter", "icon": "fa-solid fa-square-full", "dimensions": "18mm thick", "price_range_inr": "250-600/sq ft", "styles": ["modern_minimalist", "traditional_indian", "contemporary", "industrial"]},
        {"name": "Auto-Clean Electric Chimney & Hob", "icon": "fa-solid fa-fan", "dimensions": "60/90 cm", "price_range_inr": "18,000-45,000", "styles": ["modern_minimalist", "traditional_indian", "contemporary", "industrial"]},
    ],
    "bathroom": [
        {"name": "Floating Vanity Basin Unit", "icon": "fa-solid fa-sink", "dimensions": "80x45 cm", "price_range_inr": "12,000-32,000", "styles": ["modern_minimalist", "traditional_indian", "contemporary", "industrial"]},
        {"name": "Toughened Glass Shower Partition", "icon": "fa-solid fa-shower", "dimensions": "120x210 cm", "price_range_inr": "15,000-38,000", "styles": ["modern_minimalist", "contemporary", "industrial"]},
        {"name": "Mirrored LED Medicine Cabinet", "icon": "fa-solid fa-box", "dimensions": "60x75 cm", "price_range_inr": "6,000-16,000", "styles": ["modern_minimalist", "traditional_indian", "contemporary", "industrial"]},
    ],
    "living_room": [
        {"name": "L-Shape Sectional Sofa", "icon": "fa-solid fa-couch", "dimensions": "280x180 cm", "price_range_inr": "40,000-1,20,000", "styles": ["modern_minimalist", "traditional_indian", "contemporary", "industrial"]},
        {"name": "Center Coffee Table", "icon": "fa-solid fa-table", "dimensions": "120x60 cm", "price_range_inr": "8,000-28,000", "styles": ["modern_minimalist", "traditional_indian", "contemporary", "industrial"]},
        {"name": "TV Console & Entertainment Unit", "icon": "fa-solid fa-tv", "dimensions": "210x40 cm", "price_range_inr": "20,000-55,000", "styles": ["modern_minimalist", "traditional_indian", "contemporary", "industrial"]},
    ],
    "office": [
        {"name": "Executive Desk", "icon": "fa-solid fa-desktop", "dimensions": "150x75 cm", "price_range_inr": "12,000-35,000", "styles": ["modern_minimalist", "contemporary", "industrial"]},
        {"name": "Ergonomic Chair", "icon": "fa-solid fa-chair", "dimensions": "65x65 cm", "price_range_inr": "8,000-25,000", "styles": ["modern_minimalist", "contemporary", "industrial"]},
        {"name": "Wooden Writing Desk", "icon": "fa-solid fa-desktop", "dimensions": "120x60 cm", "price_range_inr": "15,000-45,000", "styles": ["traditional_indian"]},
        {"name": "Bookshelf Unit", "icon": "fa-solid fa-book-open", "dimensions": "90x35 cm", "price_range_inr": "8,000-22,000", "styles": ["modern_minimalist", "traditional_indian", "contemporary", "industrial"]},
        {"name": "Standing Desk Converter", "icon": "fa-solid fa-arrow-up-from-bracket", "dimensions": "80x50 cm", "price_range_inr": "6,000-18,000", "styles": ["modern_minimalist", "contemporary"]},
    ],
    "conference": [
        {"name": "Conference Table (8-seat)", "icon": "fa-solid fa-table", "dimensions": "240x120 cm", "price_range_inr": "25,000-80,000", "styles": ["modern_minimalist", "contemporary", "industrial"]},
        {"name": "Boardroom Chairs", "icon": "fa-solid fa-chair", "dimensions": "55x55 cm", "price_range_inr": "4,000-12,000 each", "styles": ["modern_minimalist", "contemporary"]},
        {"name": "Whiteboard Wall", "icon": "fa-solid fa-chalkboard", "dimensions": "180x120 cm", "price_range_inr": "3,000-8,000", "styles": ["modern_minimalist", "contemporary", "industrial"]},
        {"name": "Presentation Screen", "icon": "fa-solid fa-tv", "dimensions": "65-inch", "price_range_inr": "35,000-1,20,000", "styles": ["modern_minimalist", "contemporary"]},
        {"name": "Carved Wood Table", "icon": "fa-solid fa-table", "dimensions": "200x100 cm", "price_range_inr": "40,000-1,50,000", "styles": ["traditional_indian"]},
    ],
    "pantry": [
        {"name": "Modular Kitchen Counter", "icon": "fa-solid fa-sink", "dimensions": "200x60 cm", "price_range_inr": "30,000-80,000", "styles": ["modern_minimalist", "contemporary"]},
        {"name": "Bar Stools (set of 4)", "icon": "fa-solid fa-chair", "dimensions": "40x40 cm each", "price_range_inr": "6,000-20,000", "styles": ["modern_minimalist", "contemporary", "industrial"]},
        {"name": "Water Purifier Station", "icon": "fa-solid fa-glass-water", "dimensions": "35x35 cm", "price_range_inr": "5,000-18,000", "styles": ["modern_minimalist", "contemporary", "industrial", "traditional_indian"]},
    ],
    "general": [
        {"name": "Console Table / Accent Storage", "icon": "fa-solid fa-table", "dimensions": "120x35 cm", "price_range_inr": "8,000-22,000", "styles": ["modern_minimalist", "traditional_indian", "contemporary", "industrial"]},
        {"name": "Accent Mirror / Wall Decor", "icon": "fa-solid fa-image", "dimensions": "80x80 cm", "price_range_inr": "4,000-14,000", "styles": ["modern_minimalist", "traditional_indian", "contemporary", "industrial"]},
        {"name": "Indoor Plants + Planters", "icon": "fa-solid fa-seedling", "dimensions": "Various", "price_range_inr": "2,000-8,000", "styles": ["modern_minimalist", "contemporary"]},
    ],
}

MATERIAL_FINISHES = {
    "flooring": {
        "modern_minimalist": {"material": "Matte Porcelain Tiles (600x600mm)", "finish": "Anti-skid matte", "rate_sqft": "85-140/sq ft"},
        "traditional_indian": {"material": "Kota Stone / Italian Marble", "finish": "Mirror polish", "rate_sqft": "120-300/sq ft"},
        "contemporary": {"material": "Engineered Hardwood", "finish": "Satin UV-coated", "rate_sqft": "150-280/sq ft"},
        "industrial": {"material": "Polished Concrete + Epoxy", "finish": "Semi-gloss resin", "rate_sqft": "60-120/sq ft"},
    },
    "wall_treatment": {
        "modern_minimalist": {"material": "Smooth Putty + Emulsion Paint", "finish": "Eggshell sheen", "rate_sqft": "25-45/sq ft"},
        "traditional_indian": {"material": "Textured Plaster + Jali Panels", "finish": "Warm matte", "rate_sqft": "40-80/sq ft"},
        "contemporary": {"material": "PVC Wall Panels + Accent Wallpaper", "finish": "Mixed textures", "rate_sqft": "50-100/sq ft"},
        "industrial": {"material": "Exposed Brick + Clear Sealant", "finish": "Raw natural", "rate_sqft": "35-70/sq ft"},
    },
    "ceiling": {
        "modern_minimalist": {"material": "Flat Gypsum False Ceiling", "finish": "Recessed LED coves", "rate_sqft": "55-90/sq ft"},
        "traditional_indian": {"material": "Wooden Beam False Ceiling", "finish": "Teak veneer", "rate_sqft": "80-150/sq ft"},
        "contemporary": {"material": "Multi-level Gypsum + POP", "finish": "Integrated spot lighting", "rate_sqft": "65-120/sq ft"},
        "industrial": {"material": "Open Ceiling (Exposed Duct)", "finish": "Matte black spray", "rate_sqft": "30-60/sq ft"},
    },
}

LIGHTING_SPECS = {
    "modern_minimalist": [
        {"type": "Recessed LED Downlights", "wattage": "9W each", "color_temp": "4000K (Neutral White)", "placement": "Grid pattern, 4ft spacing"},
        {"type": "LED Strip (Cove)", "wattage": "12W/m", "color_temp": "3000K (Warm White)", "placement": "Perimeter ceiling cove"},
    ],
    "traditional_indian": [
        {"type": "Brass Pendant Lamps", "wattage": "15W LED", "color_temp": "2700K (Warm)", "placement": "Central ceiling mount"},
        {"type": "Wall Sconces (Diya style)", "wattage": "7W LED", "color_temp": "2700K", "placement": "Side walls, 6ft height"},
    ],
    "contemporary": [
        {"type": "Track Lighting System", "wattage": "12W per head", "color_temp": "4000K", "placement": "Adjustable track rail"},
        {"type": "Statement Chandelier", "wattage": "40W LED", "color_temp": "3500K", "placement": "Conference room center"},
    ],
    "industrial": [
        {"type": "Edison Pendant Bulbs", "wattage": "8W LED filament", "color_temp": "2200K (Amber)", "placement": "Cluster hang, varied heights"},
        {"type": "Metal Cage Wall Light", "wattage": "10W", "color_temp": "2700K", "placement": "Exposed brick walls"},
    ],
}


def get_design_catalog(style: str = "modern_minimalist", room_type: Optional[str] = None, category: Optional[str] = None) -> dict:
    """Retrieves design catalog data for a given style and optional room/category filter."""
    style_key = style.lower().replace(" ", "_")
    if style_key not in STYLE_PRESETS:
        return {"error": f"Unknown style '{style}'. Available: {list(STYLE_PRESETS.keys())}"}

    preset = STYLE_PRESETS[style_key]
    result = {"style": preset["name"], "description": preset["description"], "mood_keywords": preset["mood_keywords"]}
    cat = (category or "all").lower()

    if cat in ("colors", "all"):
        result["color_palette"] = preset["color_palette"]
    if cat in ("furniture", "all"):
        furniture = {}
        for rtype, items in FURNITURE_CATALOG.items():
            if room_type and rtype != room_type.lower():
                continue
            matched = [item for item in items if style_key in item["styles"]]
            if matched:
                furniture[rtype] = matched
        result["furniture"] = furniture
    if cat in ("materials", "all"):
        materials = {}
        for finish_type, styles_map in MATERIAL_FINISHES.items():
            if style_key in styles_map:
                materials[finish_type] = styles_map[style_key]
        result["materials"] = materials
    if cat in ("lighting", "all"):
        result["lighting"] = LIGHTING_SPECS.get(style_key, [])

    return result

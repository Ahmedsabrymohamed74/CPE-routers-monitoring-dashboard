"""Color schemes transcribed from /opt/THEMES.jpg."""
THEMES = [
    {"id": "violet-blue", "name": "Violet & Blue", "colors": ["#2C1F73", "#7B3DD9", "#FF9D00", "#338AE8", "#EAF5EA"]},
    {"id": "coral-cobalt", "name": "Coral & Cobalt", "colors": ["#FF3654", "#FFD8AD", "#FDBD99", "#2258B5", "#FF553A"]},
    {"id": "ocean", "name": "Ocean", "colors": ["#1D39CF", "#16CABD", "#9EDFE5", "#9ED0FF", "#2191FF"]},
    {"id": "teal-ember", "name": "Teal & Ember", "colors": ["#FC5732", "#E3F0FF", "#ACC2D4", "#007877", "#009C9A"]},
    {"id": "sunshine-lavender", "name": "Sunshine & Lavender", "colors": ["#FFCC00", "#F1F4F9", "#8F96FF", "#FF8324", "#CFEAFF"]},
    {"id": "mint-indigo", "name": "Mint & Indigo", "colors": ["#00B59A", "#D6E5FF", "#4B3AD1", "#FFCCCD", "#EBE7ED"]},
]
THEME_IDS = {theme["id"] for theme in THEMES}


def valid_preferences(value):
    return (
        isinstance(value, dict)
        and isinstance(value.get("mode"), str)
        and isinstance(value.get("scheme"), str)
        and value.get("mode") in {"dark", "light"}
        and value.get("scheme") in THEME_IDS
    )

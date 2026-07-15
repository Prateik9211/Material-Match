"""Canonical material family normalization.

The classifier row and vision-DNA can each report a `material_family` — but
they use different vocabularies (classifier: "furniture", "flooring", "wall";
vision-DNA: "Laminate", "Paint", "Fabric"). This module gives us:

  - `CANONICAL_FAMILIES`  — the fixed set of material families the catalogue
                            actually stocks and Brain routes on.
  - `to_canonical(value)` — map any variant string to a canonical family, or
                            None when the value is generic/object-based.
  - `is_generic_family(value)` — True when the value is an object/routing
                                 label like "furniture", "wall", "flooring".
  - `pick_final_family(classifier_family, vision_family)`
                          — apply the override rules the product owner
                            approved and return (family, override_reason).
"""
from __future__ import annotations

# Real material families the catalogue actually stocks.
CANONICAL_FAMILIES = frozenset({
    "Laminate", "Veneer", "Paint", "Fabric", "Tile",
    "Stone", "Wood", "Metal", "Wallpaper", "Ceramic",
})

# Common spelling / synonym variants → canonical family.
_ALIASES = {
    # Laminate
    "laminate": "Laminate", "laminates": "Laminate", "hpl": "Laminate",
    "decorative laminate": "Laminate",
    # Veneer
    "veneer": "Veneer", "veneers": "Veneer",
    # Paint
    "paint": "Paint", "paints": "Paint", "wall paint": "Paint",
    "pu paint": "Paint", "emulsion": "Paint",
    # Fabric
    "fabric": "Fabric", "textile": "Fabric", "textiles": "Fabric",
    "upholstery fabric": "Fabric",
    # Tile
    "tile": "Tile", "tiles": "Tile", "ceramic tile": "Tile",
    "porcelain": "Tile", "porcelain tile": "Tile",
    # Stone
    "stone": "Stone", "marble": "Stone", "granite": "Stone",
    "quartz": "Stone", "quartzite": "Stone", "engineered stone": "Stone",
    "natural stone": "Stone",
    # Wood
    "wood": "Wood", "timber": "Wood", "solid wood": "Wood",
    "hardwood": "Wood",
    # Metal
    "metal": "Metal", "brass": "Metal", "steel": "Metal",
    # Wallpaper
    "wallpaper": "Wallpaper",
    # Ceramic
    "ceramic": "Ceramic",
}

# Values the analyser uses that are object / routing labels — NOT material
# families. When the classifier returns any of these, we let vision-DNA
# take over the routing family.
_GENERIC_LABELS = frozenset({
    "furniture", "cabinet", "cabinetry", "flooring", "wall", "walls",
    "ceiling", "surface", "unknown", "other", "decor", "upholstery",
    "lighting", "window", "door", "", "n/a", "none",
})


def to_canonical(value: str | None) -> str | None:
    """Return canonical family name if value maps to a real material family,
    otherwise None (generic / object label / unknown)."""
    if not value:
        return None
    key = str(value).strip().lower()
    if not key or key in _GENERIC_LABELS:
        return None
    if key in _ALIASES:
        return _ALIASES[key]
    # Handle e.g. "wood-grain laminate" → Laminate (last-word wins for compound names).
    for tok in reversed(key.replace("/", " ").replace("-", " ").split()):
        if tok in _ALIASES:
            return _ALIASES[tok]
    return None


def is_generic_family(value: str | None) -> bool:
    """True when the value is an object/routing label rather than a real
    material family (Furniture, Flooring, Wall, Upholstery, …). Also true
    for empty / None / unknown."""
    if not value:
        return True
    key = str(value).strip().lower()
    if key in _GENERIC_LABELS:
        return True
    return to_canonical(value) is None


def pick_final_family(classifier_family: str | None,
                      vision_family: str | None) -> tuple[str | None, str]:
    """Decide which family drives Brain routing.

    Rules (approved by product owner):
      1. Classifier family is generic/object-based AND vision-DNA is canonical
         → use vision.  (e.g. classifier="furniture", vision="Laminate")
      2. Both are canonical AND agree → use classifier (already correct).
      3. Both are canonical but disagree → keep classifier (do NOT let a
         weak DNA overwrite a valid specific classifier family).
      4. Classifier canonical, vision unusable → keep classifier.
      5. Neither usable → return classifier verbatim.

    Returns (final_family, reason) where reason is a short debug string.
    """
    c_canon = to_canonical(classifier_family)
    v_canon = to_canonical(vision_family)

    if c_canon is None and v_canon is not None:
        return v_canon, f"override(generic '{classifier_family}' → vision '{v_canon}')"
    if c_canon is not None and v_canon is not None:
        if c_canon == v_canon:
            return c_canon, "agree"
        return c_canon, f"keep_classifier(disagree with vision '{v_canon}')"
    if c_canon is not None:
        return c_canon, "keep_classifier(no vision)"
    return classifier_family, "no_canonical"

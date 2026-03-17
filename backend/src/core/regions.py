from __future__ import annotations

REGION_FACTORS: dict[str, tuple[float, float]] = {
    "X": (0.2136, 12.21),
    "A": (0.21, 13.0),
    "B": (0.20, 14.0),
    "C": (0.20, 12.0),
    "D": (0.22, 13.0),
    "E": (0.21, 11.0),
    "F": (0.21, 12.0),
    "G": (0.21, 12.0),
    "H": (0.21, 12.0),
    "J": (0.22, 12.0),
    "K": (0.22, 12.0),
    "L": (0.23, 11.0),
    "M": (0.20, 13.0),
    "N": (0.21, 13.0),
    "P": (0.24, 12.0),
}


def normalize_region(region: str) -> str:
    key = region.upper()
    if key not in REGION_FACTORS:
        raise ValueError(f"Unsupported region '{region}'")
    return key

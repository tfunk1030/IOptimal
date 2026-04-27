"""Unified car and track name registry.

Single source of truth for resolving any form of car or track name to its
canonical identity.  Replaces the fragmented mappings previously scattered
across ``watcher/service.py``, ``analyzer/sto_adapters.py``, and
``car_model/cars.py``.

Usage::

    from car_model.registry import resolve_car, resolve_car_from_ibt
    from car_model.registry import resolve_track_from_ibt, track_slug

    identity = resolve_car("BMW M Hybrid V8")   # screen name
    identity = resolve_car("bmwlmdh")            # STO binary ID
    identity = resolve_car("bmw")                # canonical
    # identity.canonical == "bmw" in all cases

    ibt = IBTFile("session.ibt")
    car = resolve_car_from_ibt(ibt)
    track = resolve_track_from_ibt(ibt)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from track_model.ibt_parser import IBTFile


@dataclass(frozen=True)
class CarIdentity:
    """All known identifiers for a single car."""

    canonical: str       # "bmw", "porsche", etc.  Used by get_car() and data paths.
    display_name: str    # Human-readable: "BMW M Hybrid V8"
    screen_name: str     # iRacing CarScreenName (from IBT DriverInfo)
    sto_id: str          # STO binary car ID: "bmwlmdh"
    aero_folder: str     # Folder name under data/aeromaps_parsed/


@dataclass(frozen=True)
class TrackIdentity:
    """Canonical identity for a track configuration."""

    display_name: str    # "Sebring International Raceway"
    config: str          # "International"
    slug: str            # "sebring_international_raceway_international"


# ─── Car registry ──────────────────────────────────────────────────────────

_CAR_REGISTRY: list[CarIdentity] = [
    # GTP class (Hypercar / LMDh)
    CarIdentity("bmw",      "BMW M Hybrid V8",    "BMW M Hybrid V8",    "bmwlmdh",         "bmw"),
    CarIdentity("porsche",  "Porsche 963",        "Porsche 963",        "porsche963",      "porsche"),
    CarIdentity("ferrari",  "Ferrari 499P",       "Ferrari 499P",       "ferrari499p",     "ferrari"),
    CarIdentity("cadillac", "Cadillac V-Series.R", "Cadillac V-Series.R", "cadillacvseriesr", "cadillac"),
    CarIdentity("acura",    "Acura ARX-06",       "Acura ARX-06",       "acuraarx06gtp",   "acura"),
    # GT3 class — canonical names match car_model/cars.py BMW_M4_GT3 / ASTON_MARTIN_VANTAGE_GT3 /
    # PORSCHE_992_GT3R. Without these entries, the substring fallback at lines 91-104 silently
    # routed every GT3 IBT through the GTP BMW spec set (e.g. "bmwm4gt3" → "bmw"), corrupting
    # learner observations and emitting wrong setups. The longest-key-wins rule in the
    # substring fallback now picks the GT3 entry over the bare "bmw" key.
    CarIdentity("bmw_m4_gt3",                "BMW M4 GT3 EVO",                  "BMW M4 GT3 EVO",                  "bmwm4gt3",          "bmw_m4_gt3"),
    CarIdentity("aston_martin_vantage_gt3",  "Aston Martin Vantage GT3 EVO",    "Aston Martin Vantage GT3 EVO",    "amvantageevogt3",   "aston_martin_vantage_gt3"),
    CarIdentity("porsche_992_gt3r",          "Porsche 911 GT3 R (992)",         "Porsche 911 GT3 R (992)",         "porsche992rgt3",    "porsche_992_gt3r"),
]

# Build lookup indices once at import time.
_BY_CANONICAL: dict[str, CarIdentity] = {c.canonical: c for c in _CAR_REGISTRY}
_BY_SCREEN_NAME: dict[str, CarIdentity] = {c.screen_name: c for c in _CAR_REGISTRY}
_BY_STO_ID: dict[str, CarIdentity] = {c.sto_id: c for c in _CAR_REGISTRY}

# Lowercase index for fuzzy fallback (maps every known string form).
_BY_LOWER: dict[str, CarIdentity] = {}
for _car in _CAR_REGISTRY:
    for _key in (_car.canonical, _car.display_name, _car.screen_name,
                 _car.sto_id, _car.aero_folder):
        _BY_LOWER[_key.lower()] = _car


def resolve_car(name: str) -> CarIdentity | None:
    """Resolve any form of car name to a ``CarIdentity``.

    Tries, in order: canonical, screen name, STO ID, case-insensitive
    fallback across all known strings, then substring containment
    (handles iRacing appending suffixes like "GTP" to screen names).
    Returns ``None`` for unknown cars.
    """
    if not name:
        return None
    hit = _BY_CANONICAL.get(name) or _BY_SCREEN_NAME.get(name) or _BY_STO_ID.get(name)
    if hit:
        return hit
    hit = _BY_LOWER.get(name.lower())
    if hit:
        return hit
    # Substring fallback: "Porsche 963 GTP" contains "Porsche 963"
    # Require minimum 4 chars to avoid false positives from short inputs
    name_lower = name.lower()
    if len(name_lower) >= 4:
        best_match: CarIdentity | None = None
        best_len = 0
        for key, car in _BY_LOWER.items():
            if key in name_lower or name_lower in key:
                # Prefer longest matching key (most specific)
                if len(key) > best_len:
                    best_len = len(key)
                    best_match = car
        if best_match is not None:
            return best_match
    return None


def resolve_car_from_ibt(ibt: "IBTFile") -> CarIdentity | None:
    """Extract car identity directly from an opened IBT file."""
    car_info = ibt.car_info()
    screen_name = car_info.get("car", "")
    return resolve_car(screen_name)


def supported_car_names() -> list[str]:
    """Return display names of all supported cars (for error messages)."""
    return [c.display_name for c in _CAR_REGISTRY]


# ─── Track registry ────��───────────────────────────────────────────────────

# Maps iRacing TrackDisplayName (lowercased) → short canonical base slug.
# Only needed for deduplication of known problematic names (long names,
# unicode issues, renamed tracks).  Unknown tracks fall through to the
# default slug generator: display_name.lower().replace(" ", "_").
_TRACK_ALIASES: dict[str, str] = {
    "autodromo internacional do algarve": "algarve",
    "algarve international circuit": "algarve",
    "hockenheimring baden-württemberg": "hockenheim",
    "hockenheimring baden-w\u00fcrttemberg": "hockenheim",  # explicit unicode
    "sebring international raceway": "sebring",
    "daytona international speedway": "daytona",
    "silverstone circuit": "silverstone",
    "weathertech raceway laguna seca": "laguna_seca",
    "weathertech_raceway_laguna_seca": "laguna_seca",
    "long beach street circuit": "long_beach",
    "circuit de barcelona-catalunya": "barcelona",
    "circuit des 24 heures du mans": "le_mans",
    "nürburgring": "nurburgring",
    "suzuka international racing course": "suzuka",
    "circuit de spa-francorchamps": "spa",
    "indianapolis motor speedway": "indianapolis",
    "twin ring motegi": "motegi",
    "road america": "road_america",
    "watkins glen international": "watkins_glen",
    "mount panorama circuit": "bathurst",
    "fuji international speedway": "fuji",
    "imola": "imola",
    "autódromo josé carlos pace": "interlagos",
}


def track_slug(display_name: str, config: str = "") -> str:
    """Generate a stable filesystem slug from track display name + config.

    Uses the full display name (lowercased, spaces replaced with underscores)
    without any alias substitution.  This is the slug used for filesystem paths
    such as ``data/garage_models/{car}/{slug}.json``.

    For the canonical short key used in calibration support checks use
    ``track_key()`` instead.

    Examples::

        track_slug("Sebring International Raceway", "International")
        # → "sebring_international_raceway_international"

        track_slug("Algarve International Circuit", "Grand Prix")
        # → "algarve_international_circuit_grand_prix"

        track_slug("Some New Track")
        # → "some_new_track"
    """
    base = display_name.lower().strip().replace(" ", "_")
    if config:
        suffix = config.lower().replace(" ", "_")
        return f"{base}_{suffix}"
    return base


def track_key(display_name: str) -> str:
    """Return the canonical base track key without configuration suffix.

    This is the stable key used for track-scoped calibration support checks.
    Examples: ``Sebring International Raceway`` -> ``sebring``,
    ``Autodromo Internacional do Algarve`` -> ``algarve``.
    """
    if not display_name:
        return ""
    key = display_name.lower().strip()
    return _TRACK_ALIASES.get(key, key.replace(" ", "_"))


def resolve_track_from_ibt(ibt: "IBTFile") -> TrackIdentity:
    """Extract track identity from an opened IBT file."""
    ti = ibt.track_info()
    name = ti.get("track_name", "Unknown")
    config = ti.get("track_config", "")
    return TrackIdentity(
        display_name=name,
        config=config,
        slug=track_slug(name, config),
    )

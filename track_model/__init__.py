"""Track demand profile from IBT telemetry analysis.

Parse IBT files to extract track characteristics: surface spectrum,
braking zones, corner speeds, speed profile, kerb events, elevation.
"""

from track_model.profile import TrackProfile
from track_model.build_profile import build_profile
from track_model.track_store import TrackProfileStore

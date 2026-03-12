"""Fast IBT binary file parser using numpy for bulk channel extraction.

Parses iRacing IBT files and provides efficient channel reading via numpy
array slicing rather than per-sample struct.unpack loops.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import numpy as np

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


TYPE_MAP = {
    0: ("c", 1, "S1"),    # char
    1: ("?", 1, "?"),     # bool
    2: ("i", 4, "<i4"),   # int32
    3: ("I", 4, "<u4"),   # bitfield/uint32
    4: ("f", 4, "<f4"),   # float32
    5: ("d", 8, "<f8"),   # float64/double
}


class IBTFile:
    """Parsed IBT file with fast channel access.

    Attributes:
        session_info: Parsed YAML session info dict (or raw string)
        var_lookup: Dict of channel name -> {type, offset, count, unit, desc}
        record_count: Total number of data samples
        tick_rate: Sample rate in Hz
    """

    def __init__(self, path: str | Path):
        path = Path(path)

        # Handle zip files containing an IBT
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as zf:
                ibt_names = [n for n in zf.namelist() if n.lower().endswith(".ibt")]
                if not ibt_names:
                    raise ValueError(f"No .ibt file found in {path}")
                raw = zf.read(ibt_names[0])
            self._parse_bytes(raw)
        else:
            with open(path, "rb") as f:
                raw = f.read()
            self._parse_bytes(raw)

    def _parse_bytes(self, raw: bytes) -> None:
        """Parse IBT from raw bytes."""
        self.tick_rate = struct.unpack_from("i", raw, 8)[0]
        sinfo_len = struct.unpack_from("i", raw, 16)[0]
        sinfo_off = struct.unpack_from("i", raw, 20)[0]
        num_vars = struct.unpack_from("i", raw, 24)[0]
        var_hdr_off = struct.unpack_from("i", raw, 28)[0]
        self._buf_len = struct.unpack_from("i", raw, 36)[0]
        self._buf_offset = struct.unpack_from("i", raw, 52)[0]
        self.record_count = struct.unpack_from("i", raw, 140)[0]

        # Parse session info YAML
        sinfo_str = raw[sinfo_off:sinfo_off + sinfo_len].decode("latin-1").rstrip("\x00")
        if _HAS_YAML:
            self.session_info = yaml.safe_load(sinfo_str)
        else:
            self.session_info = sinfo_str

        # Parse variable headers
        self.var_lookup = {}
        offset = var_hdr_off
        for _ in range(num_vars):
            vtype = struct.unpack_from("i", raw, offset)[0]
            voffset = struct.unpack_from("i", raw, offset + 4)[0]
            vcount = struct.unpack_from("i", raw, offset + 8)[0]
            vname = raw[offset + 16:offset + 48].decode("latin-1").rstrip("\x00")
            vdesc = raw[offset + 48:offset + 112].decode("latin-1").rstrip("\x00")
            vunit = raw[offset + 112:offset + 144].decode("latin-1").rstrip("\x00")
            self.var_lookup[vname] = {
                "type": vtype, "offset": voffset,
                "count": vcount, "unit": vunit, "desc": vdesc,
            }
            offset += 144

        # Keep raw data buffer as numpy bytes array for fast slicing
        buf_start = self._buf_offset
        buf_end = buf_start + self._buf_len * self.record_count
        self._raw = raw[buf_start:buf_end]

    def channel(self, name: str) -> np.ndarray | None:
        """Read a single channel as numpy array. Returns None if not found."""
        if name not in self.var_lookup:
            return None
        v = self.var_lookup[name]
        _, fmt_size, np_dtype = TYPE_MAP[v["type"]]

        # Use numpy frombuffer with stride for fast extraction
        arr = np.ndarray(
            shape=(self.record_count,),
            dtype=np.dtype(np_dtype),
            buffer=self._raw,
            offset=v["offset"],
            strides=(self._buf_len,),
        ).copy()  # copy to own memory (frombuffer is a view)
        return arr

    def channels(self, *names: str) -> dict[str, np.ndarray]:
        """Read multiple channels at once. Returns dict of name -> array."""
        result = {}
        for name in names:
            ch = self.channel(name)
            if ch is not None:
                result[name] = ch
        return result

    def has_channel(self, name: str) -> bool:
        return name in self.var_lookup

    @property
    def duration_s(self) -> float:
        return self.record_count / self.tick_rate

    @property
    def dt(self) -> float:
        """Time step between samples in seconds."""
        return 1.0 / self.tick_rate

    def track_info(self) -> dict:
        """Extract track info from session YAML."""
        if not isinstance(self.session_info, dict):
            return {}
        wi = self.session_info.get("WeekendInfo", {})
        return {
            "track_name": wi.get("TrackDisplayName", "Unknown"),
            "track_config": wi.get("TrackConfigName", ""),
            "track_length": wi.get("TrackLength", ""),
            "surface_temp": wi.get("TrackSurfaceTemp", ""),
        }

    def car_info(self) -> dict:
        """Extract car info from session YAML."""
        if not isinstance(self.session_info, dict):
            return {}
        di = self.session_info.get("DriverInfo", {})
        car_idx = di.get("DriverCarIdx", -1)
        for d in di.get("Drivers", []):
            if d.get("CarIdx") == car_idx and not d.get("CarIsPaceCar"):
                return {
                    "driver": d.get("UserName", "Unknown"),
                    "car": d.get("CarScreenName", "Unknown"),
                    "car_idx": car_idx,
                }
        return {}

    def lap_boundaries(self) -> list[tuple[int, int, int]]:
        """Find (lap_number, start_idx, end_idx) for each complete lap."""
        lap_ch = self.channel("Lap")
        if lap_ch is None:
            return []

        laps = []
        current_lap = int(lap_ch[0])
        start_idx = 0
        for i in range(1, self.record_count):
            new_lap = int(lap_ch[i])
            if new_lap != current_lap:
                laps.append((current_lap, start_idx, i - 1))
                start_idx = i
                current_lap = new_lap
        return laps

    def best_lap_indices(self, min_time: float = 60.0) -> tuple[int, int] | None:
        """Find start/end indices of the best valid lap.

        Returns (start_idx, end_idx) or None.
        """
        lap_time_ch = self.channel("LapCurrentLapTime")
        if lap_time_ch is None:
            return None

        boundaries = self.lap_boundaries()
        best_time = float("inf")
        best_range = None

        for lap_num, start, end in boundaries:
            if lap_num <= 0:  # Skip out-lap
                continue
            lt = float(lap_time_ch[end])
            if lt < min_time:  # Too short to be valid
                continue
            if lt < best_time:
                best_time = lt
                best_range = (start, end)

        return best_range

# IBT Binary Format & Parsing Guide — V3

**Verified from real IBT files:** BMW M Hybrid V8 (Sebring, 2026 S1) and Ferrari 499P (Sebring, 2026 S1).

## Table of Contents
1. [File Structure Overview](#overview)
2. [Header Layout](#header)
3. [Variable Headers](#varheaders)
4. [Session Info (YAML)](#sessioninfo)
5. [Data Buffer](#databuffer)
6. [Complete Python Parser](#parser)
7. [Common Pitfalls](#pitfalls)

---

## File Structure Overview {#overview}

An IBT file is a flat binary with this layout:

```
[Main Header: 48 bytes]
[Padding: 16 bytes]  
[Disk Sub Header: 32 bytes]
[Buffer Header: 8 bytes]
... (padding to var_header_offset)
[Variable Headers: num_vars × 144 bytes each]
... (padding to session_info_offset)
[Session Info: YAML text, null-terminated]
... (padding to buf_offset)
[Data Buffer: record_count × buf_len bytes]
```

Total file size = `buf_offset + (record_count × buf_len)`

---

## Header Layout {#header}

### Main Header (offsets 0-47)

| Offset | Type | Field | Typical Value |
|--------|------|-------|---------------|
| 0 | int32 | version | 2 |
| 4 | int32 | status | 1 |
| 8 | int32 | tickRate | 60 (Hz) |
| 12 | int32 | sessionInfoUpdate | 0 |
| 16 | int32 | sessionInfoLen | ~32816 |
| 20 | int32 | sessionInfoOffset | ~43200 |
| 24 | int32 | numVars | 299-302 |
| 28 | int32 | varHeaderOffset | 144 |
| 32 | int32 | numBuf | 1 |
| 36 | int32 | bufLen | ~1152 (bytes per sample) |
| 40-47 | padding | — | 0 |

### Padding (offsets 48-63)

Contains the buffer record(s). For `numBuf=1`:

| Offset | Type | Field | Description |
|--------|------|-------|-------------|
| 48 | int32 | varBuf[0].tickCount | Total ticks in buffer |
| 52 | int32 | varBuf[0].bufOffset | **Byte offset to start of data** |

### Disk Sub Header (offsets 112-143)

| Offset | Type | Field | Description |
|--------|------|-------|-------------|
| 112 | double | startDate | Session start date (Excel serial) |
| 120 | double | startTime | Start time (seconds) |
| 128 | double | endTime | End time (seconds) |
| 136 | int32 | sessionLapCount | Number of laps |
| 140 | int32 | sessionRecordCount | **Total data samples** |

**Key formula:** `duration_seconds = sessionRecordCount / tickRate`

---

## Variable Headers {#varheaders}

Located at `varHeaderOffset` (typically 144). Each variable header is exactly **144 bytes**:

| Offset | Type | Size | Field |
|--------|------|------|-------|
| 0 | int32 | 4 | type (0=char, 1=bool, 2=int, 3=bitfield, 4=float, 5=double) |
| 4 | int32 | 4 | offset (byte offset within each data sample) |
| 8 | int32 | 4 | count (array size, usually 1) |
| 12 | bool+pad | 4 | countAsTime + 3 bytes padding |
| 16 | char[32] | 32 | name (null-terminated ASCII) |
| 48 | char[64] | 64 | description |
| 112 | char[32] | 32 | unit |

**Type → format mapping for struct.unpack:**

| Type ID | Name | Format | Size |
|---------|------|--------|------|
| 0 | char | `c` | 1 byte |
| 1 | bool | `?` | 1 byte |
| 2 | int | `i` | 4 bytes |
| 3 | bitfield | `I` | 4 bytes |
| 4 | float | `f` | 4 bytes |
| 5 | double | `d` | 8 bytes |

Most telemetry channels are type 4 (float). `SessionTime` is type 5 (double).

---

## Session Info (YAML) {#sessioninfo}

Located at `sessionInfoOffset`, length `sessionInfoLen`. Decode as `latin-1`, strip trailing null bytes, then parse as YAML.

### Key YAML Structure

```yaml
WeekendInfo:
  TrackDisplayName: "Sebring International Raceway"
  TrackConfigName: "International"
  TrackLength: "5.7938 km"
  TrackSurfaceTemp: "33.78 C"
  # ...

DriverInfo:
  DriverCarIdx: 11          # YOUR car index
  DriverUserID: 675561
  Drivers:
    - CarIdx: 0
      UserName: "Pace Car"
      CarScreenName: "safety pcporsche911cup"
      CarIsPaceCar: 1        # Filter this out
    - CarIdx: 11
      UserName: "Taylor C Funk"
      CarScreenName: "BMW M Hybrid V8"
      CarIsPaceCar: 0        # This is the real driver

CarSetup:
  UpdateCount: 9
  TiresAero:
    TireType:
      TireType: "Dry"
    LeftFront:
      StartingPressure: "152 kPa"    # 152 kPa is MINIMUM allowed in GTP
      LastHotPressure: "152 kPa"
      LastTempsOMI: "35C, 35C, 35C"    # Left tyres: O=outer, M=mid, I=inner
      TreadRemaining: "100%, 100%, 100%"
    RightFront:
      LastTempsIMO: "35C, 35C, 35C"    # Right tyres: I=inner, M=mid, O=outer
    AeroSettings:
      RearWingAngle: "17 deg"
    AeroCalculator:
      FrontRhAtSpeed: "15.0 mm"
      RearRhAtSpeed: "40.0 mm"
      DownforceBalance: "50.14%"
      LD: 3.795
  Chassis:
    Front:
      HeaveSpring: "30 N/mm"
      HeavePerchOffset: "-13.0 mm"
      ArbSize: "Soft"
      ArbBlades: 1
      ToeIn: "-0.4 mm"
      PushrodLengthOffset: "-22.5 mm"
    LeftFront:
      CornerWeight: "2763 N"
      RideHeight: "30.1 mm"
      TorsionBarOD: "13.90 mm"
      LsCompDamping: "7 clicks"
      # ... all damper settings
      Camber: "-2.9 deg"
    Rear:
      ThirdSpring: "530 N/mm"
      ArbSize: "Medium"
      ArbBlades: 3
  BrakesDriveUnit:          # BMW/LMDh path (Ferrari uses "Systems")
    BrakeSpec:
      BrakePressureBias: "46.00%"
    RearDiffSpec:
      Preload: "20 Nm"
      ClutchFrictionPlates: 4
      CoastDriveRampAngles: "40/65"
    TractionControl:
      TractionControlGain: "4 (TCLAT)"
      TractionControlSlip: "3 (TCLON)"
    GearRatios:
      GearStack: "Short"
```

### Identifying the Driver in Multi-Class Sessions

IBT files from multi-class sessions contain all drivers in `DriverInfo.Drivers[]`. To find the recording driver:
1. Use `DriverInfo.DriverCarIdx` to get the car index
2. Match against `Drivers[].CarIdx`
3. Or filter `CarIsPaceCar == 0` and match `DriverUserID`

---

## Data Buffer {#databuffer}

Starts at `varBuf[0].bufOffset` (offset 52 in header). Contains `sessionRecordCount` samples, each `bufLen` bytes.

To read channel `X` at sample `i`:
```
byte_offset = bufOffset + (i × bufLen) + varHeaders[X].offset
value = struct.unpack_from(format_char, raw_data, byte_offset)
```

---

## Complete Python Parser {#parser}

```python
import struct, yaml, numpy as np

def parse_ibt(path):
    """Parse iRacing IBT file. Returns (session_info, var_lookup, read_channel_fn, record_count, tick_rate)."""
    with open(path, 'rb') as f:
        raw_header = f.read(144)
        
        tick_rate = struct.unpack_from('i', raw_header, 8)[0]
        sinfo_len = struct.unpack_from('i', raw_header, 16)[0]
        sinfo_off = struct.unpack_from('i', raw_header, 20)[0]
        num_vars = struct.unpack_from('i', raw_header, 24)[0]
        var_hdr_off = struct.unpack_from('i', raw_header, 28)[0]
        buf_len = struct.unpack_from('i', raw_header, 36)[0]
        buf_offset = struct.unpack_from('i', raw_header, 52)[0]
        record_count = struct.unpack_from('i', raw_header, 140)[0]
        
        type_map = {
            0: ('c', 1), 1: ('?', 1), 2: ('i', 4),
            3: ('I', 4), 4: ('f', 4), 5: ('d', 8)
        }
        
        # Parse session info YAML
        f.seek(sinfo_off)
        sinfo_str = f.read(sinfo_len).decode('latin-1').rstrip('\x00')
        session_info = yaml.safe_load(sinfo_str)
        
        # Parse variable headers
        f.seek(var_hdr_off)
        var_lookup = {}
        for _ in range(num_vars):
            vtype = struct.unpack('i', f.read(4))[0]
            voffset = struct.unpack('i', f.read(4))[0]
            vcount = struct.unpack('i', f.read(4))[0]
            f.read(4)  # countAsTime + padding
            vname = f.read(32).decode('latin-1').rstrip('\x00')
            vdesc = f.read(64).decode('latin-1').rstrip('\x00')
            vunit = f.read(32).decode('latin-1').rstrip('\x00')
            var_lookup[vname] = {
                'type': vtype, 'offset': voffset,
                'count': vcount, 'unit': vunit, 'desc': vdesc
            }
        
        # Read data buffer
        f.seek(buf_offset)
        raw_data = f.read(buf_len * record_count)
        
        def read_channel(name):
            """Read a single channel as numpy array."""
            if name not in var_lookup:
                return None
            v = var_lookup[name]
            fmt_char, fmt_size = type_map[v['type']]
            arr = np.zeros(record_count)
            for i in range(record_count):
                arr[i] = struct.unpack_from(
                    fmt_char, raw_data, i * buf_len + v['offset']
                )[0]
            return arr
        
        return session_info, var_lookup, read_channel, record_count, tick_rate


# Usage:
# si, vars, read_ch, n, hz = parse_ibt('path/to/file.ibt')
# speed_kmh = read_ch('Speed') * 3.6
# lat_g = read_ch('LatAccel') / 9.81
# setup = si['CarSetup']
```

### Performance Note

For large IBT files (40+ MB, 30k+ records), the per-sample loop can be slow. For faster extraction, use numpy structured arrays:

```python
import numpy as np

# Build dtype from variable headers
dtype_fields = []
for name, v in var_lookup.items():
    np_type = {0: 'S1', 1: '?', 2: '<i4', 3: '<u4', 4: '<f4', 5: '<f8'}[v['type']]
    # Only works for count=1 channels
    if v['count'] == 1:
        dtype_fields.append((name, np_type))

# This approach requires careful offset management — use the simple loop
# for correctness, optimize only if performance is a problem.
```

---

## Common Pitfalls {#pitfalls}

1. **Pace car is driver index 0.** Always filter `CarIsPaceCar` when identifying the recording driver.
2. **Carcass temps may be flat.** `tempCL/CM/CR` channels can stay at ambient for entire short stints. Always verify before using for setup decisions. Use surface temps (`tempL/M/R`) as primary.
3. **Speed is in m/s, not km/h.** Multiply by 3.6.
4. **Accelerations are in m/s², not g.** Divide by 9.81.
5. **Ride heights and shock deflections are in meters.** Multiply by 1000 for mm.
6. **Steering angle is in radians.** Multiply by 180/π for degrees.
7. **Pressures are in kPa.** Divide by 6.895 for PSI.
8. **Fuel may show 0 usage in practice sessions** depending on sim settings (AI mode, fuel consumption off, etc.).
9. **Lap 0 is the out-lap.** Don't include in lap time analysis. Typical valid Sebring lap: 108-115s. Filter laps by reasonable time window.
10. **Session info YAML may contain special characters.** Always decode as `latin-1`, not UTF-8.
11. **Multi-class sessions contain all cars.** The telemetry data only logs YOUR car's channels, but session info lists all drivers/cars on track.
12. **HRshockDefl may be missing.** Some IBT files don't log rear heave deflection (only front). Check before accessing.

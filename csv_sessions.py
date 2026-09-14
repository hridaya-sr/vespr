"""
In-memory storage for uploaded CSV flight logs, keyed by session id.

Mirrors the pattern in dependencies.py/simulation.py: a single process-wide
store, fine for a single-user prototype. Nothing here is persisted to disk,
so sessions disappear on backend restart.
"""

import csv
import io
import math
import re
import uuid
from dataclasses import dataclass, field

import numpy as np

from ekf import BaroInertialEKF
from models import ColumnMapping, UnitSettings

PREVIEW_ROW_COUNT = 5
FEET_TO_METERS = 0.3048

# Matches the tuning used for the live BaroInertialEKF in main.py's
# websocket handler, so replayed flights behave the same as the live demo.
EKF_ACCEL_NOISE_STD = 0.0128
EKF_BARO_NOISE_STD = 2.0
EKF_BIAS_RANDOM_WALK_STD = 0.01

# Loose substring match against lowercased column names, in priority order.
FIELD_KEYWORDS = {
    "timestamp": ["timestamp", "time"],
    "altitude": ["altitude", "alt"],
    "velocity": ["velocity", "speed", "vel"],
    "acceleration": ["acceleration", "accel"],
}

# Odd window (points on each side + the point itself) for the moving-average
# smoothing pass applied before/after differentiating a GPS-derived signal.
# GPS altitude/velocity is noisy enough that raw differentiation would be
# unusable — smoothing first keeps the derivative from blowing up on jitter.
SMOOTHING_WINDOW = 5

# How many non-empty sample values to check when deciding whether a column
# is eligible for the timestamp/altitude/velocity/acceleration dropdowns.
NUMERIC_COLUMN_SAMPLE_SIZE = 10

_NUMERIC_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def _is_strictly_numeric(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def numeric_columns(session: "CsvSession", sample_size: int = NUMERIC_COLUMN_SAMPLE_SIZE) -> list[str]:
    """Columns eligible for the timestamp/altitude/velocity/acceleration
    dropdowns. A column qualifies only if its first `sample_size` non-empty
    values all parse as a plain float with no cleanup — deliberately
    strict, so a GPS coordinate column like "27.932328°" or a free-text
    status column is excluded rather than silently offered as a numeric
    field. (Actual parsing at normalize time is more lenient — see
    `_parse_lenient_float` — as defense-in-depth for a column that passes
    this sample check but has one decorated value later in the file.)"""
    result = []
    for col in session.columns:
        sample: list[str] = []
        for row in session.raw_rows:
            value = row.get(col, "").strip()
            if value:
                sample.append(value)
            if len(sample) >= sample_size:
                break
        if sample and all(_is_strictly_numeric(v) for v in sample):
            result.append(col)
    return result


def _parse_lenient_float(value: str) -> float:
    """Parse a float, ignoring non-numeric decoration around the number
    itself (units, a degree symbol, stray whitespace/punctuation), so a
    GPS coordinate like "27.932328°" parses as 27.932328 instead of
    raising ValueError outright when a user maps a column like that."""
    match = _NUMERIC_RE.search(value)
    if not match:
        raise ValueError(f"Could not parse a number from {value!r}")
    return float(match.group())


@dataclass
class CsvSession:
    columns: list[str]
    raw_rows: list[dict[str, str]]
    normalized_rows: list[dict[str, float]] = field(default_factory=list)
    ekf: BaroInertialEKF | None = None
    acceleration_derived: bool = False


def _moving_average(values: list[float], window: int) -> list[float]:
    """Centered simple moving average. The window shrinks *symmetrically*
    approaching each edge (down to just the point itself at the very
    boundary) rather than clamping to a same-size window skewed toward
    whichever side has room — a skewed window is biased on any trending or
    curved signal (e.g. climb/descent), since it averages more "past" than
    "future" points there. That bias is small on its own but compounds
    badly when this function is used both before and after two rounds of
    differentiation (altitude -> velocity -> acceleration)."""
    n = len(values)
    if n == 0 or window <= 1:
        return list(values)
    half = window // 2
    smoothed = []
    for i in range(n):
        h = min(half, i, n - 1 - i)
        segment = values[i - h : i + h + 1]
        smoothed.append(sum(segment) / len(segment))
    return smoothed


def _differentiate(values: list[float], timestamps: list[float]) -> list[float]:
    """Numerical derivative w.r.t. timestamps. Delegates to numpy's
    gradient, which uses second-order-accurate central differences in the
    interior and second-order-accurate one-sided differences at the two
    endpoints — a naive two-point endpoint difference was tried first and
    rejected: on double-differentiation (altitude -> velocity -> accel) its
    extra endpoint error compounds into wild boundary blow-ups instead of
    staying near the true constant-acceleration value.

    Real GPS logs commonly have runs of duplicate timestamps whenever the
    underlying sample rate exceeds the timestamp field's resolution — e.g.
    several fixes logged under one HHMMSS-truncated second. Differentiating
    against a duplicate (zero-delta) timestamp divides by zero, producing
    NaN that then permanently poisons the EKF's state once fed through
    predict() (matrix math propagates NaN forever afterward). Nudging the
    duplicate apart by a tiny epsilon was tried and rejected too: dividing
    a real (non-zero) altitude change by a fabricated microsecond-scale gap
    produces a physically absurd derivative (millions of m/s^2) instead of
    a NaN — silent nonsense instead of a crash, still wrong either way. The
    correct fix is to collapse each run of duplicate timestamps to its mean
    value, differentiate over the real gaps between *distinct* timestamps,
    then broadcast that one derivative back to every row in the run.

    Grouping on exact equality alone isn't enough, though: real GPS clocks
    can jitter (a timestamp that repeats or briefly goes backward without
    being adjacent to its earlier occurrence — e.g. ...101, 102, 101, 103),
    and np.gradient's finite-difference weights can still divide by zero
    on a non-monotonic sequence even with no exact adjacent duplicate. So
    a timestamp is folded into the *current* group whenever it fails to
    strictly exceed the group's own timestamp, not just when it's equal —
    which guarantees the sequence handed to np.gradient is always strictly
    increasing, however jittery the raw log is."""
    n = len(values)
    if n < 2:
        return [0.0] * n

    unique_ts: list[float] = []
    grouped_values: list[list[float]] = []
    for t, v in zip(timestamps, values):
        if unique_ts and t <= unique_ts[-1]:
            grouped_values[-1].append(v)
        else:
            unique_ts.append(t)
            grouped_values.append([v])

    if len(unique_ts) < 2:
        return [0.0] * n

    unique_means = [sum(vs) / len(vs) for vs in grouped_values]
    unique_deriv = np.gradient(np.asarray(unique_means, dtype=float), np.asarray(unique_ts, dtype=float))

    result: list[float] = []
    for d, vs in zip(unique_deriv, grouped_values):
        result.extend([float(d)] * len(vs))
    return result


def _derive_acceleration(parsed: list[dict[str, float]]) -> list[float]:
    """Synthesize an acceleration series for logs with no accelerometer.
    Prefers differentiating velocity (one derivative, less noise
    amplification); falls back to double-differentiating altitude when no
    velocity column was mapped either. Each differentiation is bracketed by
    smoothing passes since GPS altitude/velocity noise would otherwise blow
    up into an unusable derivative."""
    timestamps = [r["timestamp"] for r in parsed]

    if parsed and "velocity_raw" in parsed[0]:
        velocity_series = [r["velocity_raw"] for r in parsed]
        velocity_smoothed = _moving_average(velocity_series, SMOOTHING_WINDOW)
        accel_series = _differentiate(velocity_smoothed, timestamps)
    else:
        altitude_series = [r["altitude"] for r in parsed]
        altitude_smoothed = _moving_average(altitude_series, SMOOTHING_WINDOW)
        velocity_est = _differentiate(altitude_smoothed, timestamps)
        velocity_est_smoothed = _moving_average(velocity_est, SMOOTHING_WINDOW)
        accel_series = _differentiate(velocity_est_smoothed, timestamps)

    return _moving_average(accel_series, SMOOTHING_WINDOW)


# Clock-time timestamp formats, as an alternative to plain elapsed
# seconds/milliseconds: "HH:MM:SS[.fff]" or compact "HHMMSS"/"HMMSS".
_CLOCK_COLON_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?$")
_CLOCK_COMPACT_RE = re.compile(r"^(\d{5,6})(?:\.(\d+))?$")


def _parse_clock_seconds(value: str) -> float | None:
    """Parse a clock-time string to seconds-since-midnight, or return None
    if it doesn't match either clock-time pattern, or the minutes/seconds
    portions aren't valid (guards against misreading a 6-digit elapsed-time
    value, e.g. "123456", as a clock time when it might not be one)."""
    value = value.strip()

    m = _CLOCK_COLON_RE.match(value)
    if not m:
        m = _CLOCK_COMPACT_RE.match(value)
        if not m:
            return None
        digits, frac = m.groups()
        ss_str, mm_str = digits[-2:], digits[-4:-2]
        h_str = digits[:-4] or "0"
    else:
        h_str, mm_str, ss_str, frac = m.groups()

    h, mm, ss = int(h_str), int(mm_str), int(ss_str)
    if mm >= 60 or ss >= 60:
        return None

    seconds = float(h * 3600 + mm * 60 + ss)
    if frac:
        seconds += float(f"0.{frac}")
    return seconds


def _is_clock_time_column(sample_values: list[str]) -> bool:
    """A column counts as clock-time only if every sampled value parses as
    one — a single value that doesn't fit falls back to plain elapsed
    seconds/milliseconds instead."""
    return bool(sample_values) and all(_parse_clock_seconds(v) is not None for v in sample_values)


_sessions: dict[str, CsvSession] = {}


def _guess_column(columns: list[str], keywords: list[str]) -> str | None:
    for keyword in keywords:
        for col in columns:
            if keyword in col.lower():
                return col
    return None


def suggest_mapping(columns: list[str]) -> dict[str, str | None]:
    return {field_name: _guess_column(columns, keywords) for field_name, keywords in FIELD_KEYWORDS.items()}


def create_session(raw_text: str) -> tuple[str, CsvSession]:
    reader = csv.DictReader(io.StringIO(raw_text))
    all_columns = [c for c in (reader.fieldnames or []) if c is not None]
    all_rows = list(reader)

    session_id = str(uuid.uuid4())
    # Non-numeric columns (GPS coordinates with a degree symbol, free-text
    # status columns, etc) are dropped right here at upload time — nothing
    # downstream (preview, mapping dropdowns, or a direct API call) ever
    # sees them, rather than filtering them out in several separate places.
    session = CsvSession(columns=all_columns, raw_rows=all_rows)
    keep = set(numeric_columns(session))
    session.columns = [c for c in all_columns if c in keep]
    session.raw_rows = [{k: v for k, v in row.items() if k in keep} for row in all_rows]

    _sessions[session_id] = session
    return session_id, session


def get_session(session_id: str) -> CsvSession:
    if session_id not in _sessions:
        raise KeyError(session_id)
    return _sessions[session_id]


def _fill_missing(values: list[float | None], label: str) -> list[float]:
    """Fill None entries (a row whose mapped value was missing/empty or
    failed to parse) by linear interpolation between the nearest valid
    neighbors, by list position — a reasonable assumption for the sparse,
    isolated gaps this is meant for (e.g. ADS-B state vectors that don't
    report every field on every message). A gap at the very start/end (no
    valid neighbor on that side) is filled flat from the nearest valid
    value instead of left missing. A column with no valid values at all is
    a real error, not something to paper over."""
    n = len(values)
    valid_idx = [i for i, v in enumerate(values) if v is not None]
    if not valid_idx:
        raise ValueError(f"Column mapped to {label!r} has no valid numeric values at all")

    result = list(values)
    first, last = valid_idx[0], valid_idx[-1]
    for i in range(first):
        result[i] = result[first]
    for i in range(last + 1, n):
        result[i] = result[last]
    for a, b in zip(valid_idx, valid_idx[1:]):
        if b - a > 1:
            va, vb = result[a], result[b]
            for i in range(a + 1, b):
                t = (i - a) / (b - a)
                result[i] = va + (vb - va) * t
    return result  # type: ignore[return-value]


def normalize_rows(
    session: CsvSession, mapping: ColumnMapping, units: UnitSettings
) -> list[dict[str, float]]:
    """Convert mapped columns to the internal schema: timestamp (s, zeroed
    at the first row), altitude (m), acceleration (m/s^2). Velocity and
    acceleration are both optional — when acceleration isn't mapped, it's
    synthesized from velocity (or double-differentiated altitude) instead
    of a real sensor reading; velocity is carried along unconverted so it's
    available for that derivation.

    Timestamp format is auto-detected: if the mapped column's values look
    like clock times (HH:MM:SS or compact HHMMSS), they're parsed as
    seconds-since-midnight; otherwise they're treated as plain elapsed
    seconds/milliseconds per `units.timestamp_unit`, as before. Either way
    the result is zeroed against the first row below.

    A row whose mapped value is missing/empty or fails to parse doesn't
    fail the whole upload — it's filled in afterward by _fill_missing. The
    upload-time numeric_columns() check only samples non-empty values, so
    a column that's numeric but sparsely populated (common in real sensor
    logs) passes that check and would otherwise crash here on the first
    empty row it actually hits."""
    sample = []
    for row in session.raw_rows:
        value = row[mapping.timestamp].strip()
        if value:
            sample.append(value)
        if len(sample) >= 10:
            break
    is_clock_time = _is_clock_time_column(sample)

    # TEMP DEBUG (step 3 investigation) — remove once real-log parsing is verified.
    print(f"[csv_sessions] timestamp column detected as clock-time: {is_clock_time}")
    print("[csv_sessions] first 10 raw timestamps -> parsed elapsed-seconds (pre-zeroing):")
    for raw_ts in sample:
        parsed_ts = _parse_clock_seconds(raw_ts) if is_clock_time else _parse_lenient_float(raw_ts)
        print(f"    {raw_ts!r} -> {parsed_ts}")

    timestamps: list[float | None] = []
    altitudes: list[float | None] = []
    velocities: list[float | None] = []
    accelerations: list[float | None] = []
    missing_found = False

    for i, row in enumerate(session.raw_rows):
        raw_ts = row[mapping.timestamp]
        raw_alt = row[mapping.altitude]
        raw_vel = row[mapping.velocity] if mapping.velocity else None
        raw_acc = row[mapping.acceleration] if mapping.acceleration else None

        try:
            if is_clock_time:
                timestamp = _parse_clock_seconds(raw_ts)
                if timestamp is None:
                    raise ValueError(f"Could not parse clock-time timestamp: {raw_ts!r}")
            else:
                timestamp = _parse_lenient_float(raw_ts)
                if units.timestamp_unit == "ms":
                    timestamp /= 1000.0
        except ValueError:
            timestamp = None

        try:
            altitude = _parse_lenient_float(raw_alt)
            if units.altitude_unit == "ft":
                altitude *= FEET_TO_METERS
        except ValueError:
            altitude = None

        try:
            velocity = _parse_lenient_float(raw_vel) if raw_vel is not None else None
        except ValueError:
            velocity = None

        try:
            acceleration = _parse_lenient_float(raw_acc) if raw_acc is not None else None
        except ValueError:
            acceleration = None

        # TEMP DEBUG (step 3 investigation) — remove once real-log parsing is verified.
        if i < 10:
            print(f"[csv_sessions] raw_altitude={raw_alt!r} -> normalized_altitude={altitude}")

        # TEMP DEBUG (real-data investigation) — Python's float() silently
        # accepts strings like "nan"/"NaN"/"inf" without raising, so a
        # sensor's own missing-fix sentinel can inject a non-finite value
        # straight into the series undetected. Scan every row (not just the
        # first 10) and name exactly which row/column/raw-string it came
        # from, since this is a different failure mode than the duplicate-
        # timestamp one already fixed — that was a bad denominator, this
        # would be a bad numerator, and one already-NaN value entering a
        # moving-average window spreads to every point in that window.
        if timestamp is not None and not math.isfinite(timestamp):
            print(f"[csv_sessions] NON-FINITE timestamp at row {i}: raw_timestamp={raw_ts!r}")
            timestamp = None
        if altitude is not None and not math.isfinite(altitude):
            print(f"[csv_sessions] NON-FINITE altitude at row {i}: raw_altitude={raw_alt!r}")
            altitude = None
        if velocity is not None and not math.isfinite(velocity):
            print(f"[csv_sessions] NON-FINITE velocity at row {i}: raw_velocity={raw_vel!r}")
            velocity = None
        if acceleration is not None and not math.isfinite(acceleration):
            print(f"[csv_sessions] NON-FINITE acceleration at row {i}: raw_acceleration={raw_acc!r}")
            acceleration = None

        if timestamp is None or altitude is None:
            missing_found = True
        if raw_vel is not None and velocity is None:
            missing_found = True
        if raw_acc is not None and acceleration is None:
            missing_found = True

        timestamps.append(timestamp)
        altitudes.append(altitude)
        velocities.append(velocity)
        accelerations.append(acceleration)

    if missing_found:
        missing_ts = sum(1 for v in timestamps if v is None)
        missing_alt = sum(1 for v in altitudes if v is None)
        missing_vel = sum(1 for v in velocities if v is None) if mapping.velocity else 0
        missing_acc = sum(1 for v in accelerations if v is None) if mapping.acceleration else 0
        print(f"[csv_sessions] filling missing/unparseable values via neighbor interpolation: "
              f"timestamp={missing_ts} altitude={missing_alt} velocity={missing_vel} acceleration={missing_acc}")

    timestamps = _fill_missing(timestamps, "timestamp")
    altitudes = _fill_missing(altitudes, "altitude")
    if mapping.velocity:
        velocities = _fill_missing(velocities, "velocity")
    if mapping.acceleration:
        accelerations = _fill_missing(accelerations, "acceleration")

    # Some exports (e.g. ADS-B/flight-tracking API dumps) list the most
    # recent row first — descending time order throughout the file, not
    # just isolated jitter. Everything downstream (EKF dt, differentiation,
    # frame-by-frame playback order) assumes ascending time, so detect an
    # overall-descending file here and reverse it back to chronological
    # order once, rather than making every consumer tolerate either
    # direction. A simple first-vs-last comparison won't misfire on the
    # isolated-jitter case (a single out-of-order row already handled by
    # _differentiate's grouping) since the overall trend there still rises.
    if len(timestamps) >= 2 and timestamps[-1] < timestamps[0]:
        print("[csv_sessions] timestamps are descending overall — reversing rows to chronological order")
        timestamps.reverse()
        altitudes.reverse()
        velocities.reverse()
        accelerations.reverse()

    parsed = []
    for i in range(len(session.raw_rows)):
        parsed_row = {
            "timestamp": timestamps[i],
            "altitude": altitudes[i],
            "acceleration": accelerations[i] if mapping.acceleration else None,
        }
        if mapping.velocity:
            parsed_row["velocity_raw"] = velocities[i]
        parsed.append(parsed_row)

    if parsed:
        t0 = parsed[0]["timestamp"]
        for r in parsed:
            r["timestamp"] -= t0

    session.acceleration_derived = mapping.acceleration is None
    if session.acceleration_derived:
        derived = _derive_acceleration(parsed)
        for r, a in zip(parsed, derived):
            r["acceleration"] = a

        # TEMP DEBUG (real-data investigation) — if this fires with clean
        # (finite) raw inputs above, the bug is in the derivation math
        # itself, not a bad raw value.
        bad_indices = [i for i, a in enumerate(derived) if not math.isfinite(a)]
        if bad_indices:
            print(f"[csv_sessions] NON-FINITE derived acceleration at rows: {bad_indices[:20]}"
                  f"{' ...' if len(bad_indices) > 20 else ''} (total {len(bad_indices)})")

    session.normalized_rows = parsed
    session.ekf = BaroInertialEKF(
        initial_altitude=parsed[0]["altitude"] if parsed else 0.0,
        accel_noise_std=EKF_ACCEL_NOISE_STD,
        baro_noise_std=EKF_BARO_NOISE_STD,
        bias_random_walk_std=EKF_BIAS_RANDOM_WALK_STD,
    )
    return parsed


def step(session: CsvSession, index: int) -> dict[str, float]:
    """Advance the session's EKF by one row, mirroring how the live
    websocket handler treats its first frame vs. subsequent frames: the
    row at index 0 just reports the EKF's initial state, since predict/update
    need a previous row to compute dt against."""
    if session.ekf is None or not session.normalized_rows:
        raise ValueError("Session has not been confirmed/normalized yet")
    if not (0 <= index < len(session.normalized_rows)):
        raise IndexError(index)

    row = session.normalized_rows[index]
    ekf = session.ekf

    # Frame 0 has no prior row to update against, so there's no innovation
    # yet — report zero/not-anomalous rather than a stale value from a
    # previous seek() reset.
    is_anomaly = False

    if index > 0:
        prev_row = session.normalized_rows[index - 1]
        dt = max(row["timestamp"] - prev_row["timestamp"], 1e-6)
        ekf.predict(a_meas=row["acceleration"], dt=dt)
        ekf.update(z_baro=row["altitude"])
        # See BaroInertialEKF.update() — anomaly flag comes from an
        # adaptive baseline over this file's own recent innovations, not
        # a fixed noise-model threshold, since real (especially
        # GPS-derived) data routinely violates the tuning constants that
        # baro_noise_std/accel_noise_std assume.
        is_anomaly = ekf.is_anomaly

    return {
        "index": index,
        "timestamp": row["timestamp"],
        "altitude": row["altitude"],
        "acceleration": row["acceleration"],
        "altitude_filtered_m": round(float(ekf.altitude), 2),
        "velocity_filtered_ms": round(float(ekf.velocity), 2),
        "accel_bias_est": round(float(ekf.bias), 4),
        "innovation_m": round(float(ekf.innovation), 3),
        "is_anomaly": is_anomaly,
    }


def seek(session: CsvSession, target_index: int) -> list[dict[str, float]]:
    """Deterministically reconstruct the EKF state at an arbitrary earlier
    (or simply not-yet-visited) frame, for stepping backward and scrubbing:
    the filter can't be "unwound" to an earlier state, so the only accurate
    way to get one is resetting to a genuinely fresh EKF (zero bias,
    matching the state right after confirm) and replaying every row from 0
    up to target_index. This is deliberately different from
    reset_playback()'s reset_flight(), which keeps the learned bias on
    purpose for Replay's touchdown/relaunch semantics — seeking needs the
    value the EKF would have actually shown the first time it passed
    through target_index, not one carried over from wherever the filter
    happened to be before.

    Returns every frame from 0 through target_index, not just the last —
    scrubbing can jump into territory the client has never seen, and
    returning only the endpoint would leave its chart with an isolated
    point instead of a continuous trace back to the start. The full list
    costs a few hundred ms and a few MB even at the far end of a 15k-row
    file (benchmarked), which is the same amount of data a full playback
    already accumulates client-side — this just delivers it in one shot
    instead of one row at a time."""
    if not session.normalized_rows:
        raise ValueError("Session has not been confirmed/normalized yet")
    if not (0 <= target_index < len(session.normalized_rows)):
        raise IndexError(target_index)

    session.ekf = BaroInertialEKF(
        initial_altitude=session.normalized_rows[0]["altitude"],
        accel_noise_std=EKF_ACCEL_NOISE_STD,
        baro_noise_std=EKF_BARO_NOISE_STD,
        bias_random_walk_std=EKF_BIAS_RANDOM_WALK_STD,
    )
    return [step(session, i) for i in range(target_index + 1)]


def reset_playback(session: CsvSession) -> dict[str, float]:
    if session.ekf is None:
        raise ValueError("Session has not been confirmed/normalized yet")
    session.ekf.reset_flight()
    return {
        "altitude_filtered_m": round(float(session.ekf.altitude), 2),
        "velocity_filtered_ms": round(float(session.ekf.velocity), 2),
        "accel_bias_est": round(float(session.ekf.bias), 4),
    }

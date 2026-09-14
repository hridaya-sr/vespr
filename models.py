from typing import Literal

from pydantic import BaseModel


class TelemetryFrame(BaseModel):
    timestamp: str
    mission_elapsed_time_s: float
    altitude_m: float
    velocity_ms: float
    accel_x_ms2: float
    accel_y_ms2: float
    accel_z_ms2: float
    gyro_x_rads: float
    gyro_y_rads: float
    gyro_z_rads: float
    phase: str


class HealthResponse(BaseModel):
    message: str
    version: str
    status: str


class FlightSummary(BaseModel):
    total_frames: int
    latest_phase: str
    max_altitude_m: float


class CsvUploadResponse(BaseModel):
    session_id: str
    # Non-numeric columns (GPS coordinates with a degree symbol, free-text
    # status columns, etc) are already dropped by the time this is built —
    # see csv_sessions.create_session — so every column here is usable.
    columns: list[str]
    preview_rows: list[dict[str, str]]
    suggested_mapping: dict[str, str | None]


class ColumnMapping(BaseModel):
    timestamp: str
    altitude: str
    # Both optional: GPS-only logs often have no onboard accelerometer, and
    # sometimes no velocity channel either. When acceleration is omitted, a
    # synthetic series is derived from velocity (or double-differentiated
    # altitude) instead of a real sensor reading.
    velocity: str | None = None
    acceleration: str | None = None


class UnitSettings(BaseModel):
    altitude_unit: Literal["m", "ft"]
    timestamp_unit: Literal["s", "ms"]


class CsvConfirmRequest(BaseModel):
    session_id: str
    mapping: ColumnMapping
    units: UnitSettings


class NormalizedRow(BaseModel):
    timestamp: float
    altitude: float
    acceleration: float


class CsvConfirmResponse(BaseModel):
    session_id: str
    row_count: int
    rows: list[NormalizedRow]
    acceleration_derived: bool


class CsvSessionRequest(BaseModel):
    session_id: str


class CsvStepRequest(BaseModel):
    session_id: str
    index: int


class CsvStepResponse(BaseModel):
    index: int
    timestamp: float
    altitude: float
    acceleration: float
    altitude_filtered_m: float
    velocity_filtered_ms: float
    accel_bias_est: float
    innovation_m: float
    is_anomaly: bool


class CsvResetResponse(BaseModel):
    altitude_filtered_m: float
    velocity_filtered_ms: float
    accel_bias_est: float

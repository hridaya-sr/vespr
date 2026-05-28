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

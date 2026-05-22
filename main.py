import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import numpy as np

app = FastAPI(title="Vespr Telemetry API", version="0.1.0")

# response model for telemetry data


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


# generate one frame

_rng = np.random.default_rng()

LAUNCH_TIME = datetime.now(timezone.utc)


def generate_frame() -> TelemetryFrame:
    now = datetime.now(timezone.utc)
    elapsed = (now - LAUNCH_TIME).total_seconds()

    # loop the simulated flight every 15 seconds
    t = elapsed % 15.0

    if t < 3.0:
        # Boost: engine burning, accelerating upwards
        phase = "boost"
        true_accel_z = 120.0  # m/s^2 upward
        true_velocity = 120.0 * t  # v = at
        true_altitude = 0.5 * 120.0 * t**2  # s = (1/2)at^2

    elif t < 8.0:
        # Coast: engine off, only gravity acting
        phase = "coast"
        coast_time = t - 3.0
        v0 = 120.0 * 3.0  # velocity at engine cutoff: 360 m/s
        s0 = 0.5 * 120.0 * 3.0**2  # altitude at engine cutoff: 540 m
        true_accel_z = -9.81  # m/s^2 downward (gravity
        true_velocity = v0 - 9.81 * coast_time  # v = v0 + at
        true_altitude = (
            s0 + v0 * coast_time - 0.5 * 9.81 * coast_time**2
        )  # s = s0 + v0t + (1/2)at^2

    else:
        # Descent: past apogee, falling back to Earth
        phase = "descent"
        descent_time = t - 8.0
        v0_coast = 120.0 * 3.0
        s0_coast = 0.5 * 120.0 * 3.0**2
        v_at_8 = v0_coast - 9.81 * 5.0
        s_at_8 = s0_coast + v0_coast * 5.0 - 0.5 * 9.81 * 5.0**2
        true_accel_z = -9.81
        true_velocity = v_at_8 - 9.81 * descent_time
        true_altitude = max(
            0, s_at_8 + v_at_8 * descent_time - 0.5 * 9.81 * descent_time**2
        )

    # Sensor Noise
    baro_noise = _rng.normal(0, 2.0)
    accel_noise = _rng.normal(0, 0.0013 * 9.81, size=3)  # [x, y, z] noise
    gyro_noise = _rng.normal(0, 0.00087, size=3)  # [x, y, z] noise

    return TelemetryFrame(
        timestamp=now.isoformat(),
        mission_elapsed_time_s=round(elapsed, 3),
        altitude_m=round(float(true_altitude + baro_noise), 2),
        velocity_ms=round(float(true_velocity), 2),
        accel_x_ms2=round(float(accel_noise[0]), 4),  # lateral - near zero
        accel_y_ms2=round(float(accel_noise[1]), 4),  # lateral - near zero
        accel_z_ms2=round(float(true_accel_z + accel_noise[2]), 4),  # vertical + noise
        gyro_x_rads=round(float(gyro_noise[0]), 4),
        gyro_y_rads=round(float(gyro_noise[1]), 4),
        gyro_z_rads=round(float(gyro_noise[2]), 4),
        phase=phase,
    )


# endpoints


@app.get("/")
def root():
    return {"message": "Vespr Telemetry API is live."}


@app.get("/telemetry", response_model=TelemetryFrame)
def get_telemetry():
    return generate_frame()


@app.get("/telemetry/stream")
async def stream_telemetry():
    async def event_generator():
        while True:
            frame = generate_frame()
            data = frame.model_dump_json()
            yield f"data: {data}\n\n"
            await asyncio.sleep(0.1)  # wait 100ms between frames

    return StreamingResponse(event_generator(), media_type="text/event-stream")

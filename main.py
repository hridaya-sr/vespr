import random
import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Vespr Telemetry API", version = "0.1.0")

# response model for telemetry data

class TelemetryFrame(BaseModel):
    timestamp: str
    altitude_m: float
    velocity_ms: float
    acceleration_ms2: float
    mission_elapsed_time_s: float


# generate one frame

LAUNCH_TIME = datetime.now(timezone.utc)

def generate_frame() -> TelemetryFrame:
    now = datetime.now(timezone.utc)
    elapsed = (now - LAUNCH_TIME).total_seconds()

    return TelemetryFrame(
        timestamp=now.isoformat(),
        altitude_m=round(random.uniform(0, 10000), 2),
        velocity_ms=round(random.uniform(0, 500), 2),
        acceleration_ms2=round(random.uniform(-15, 35), 2),
        mission_elapsed_time_s=round(elapsed, 3)
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


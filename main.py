import asyncio

from fastapi import FastAPI, Depends
from fastapi.responses import StreamingResponse
from models import TelemetryFrame
from simulation import FlightSimulator
from dependencies import get_simulator

app = FastAPI(title="Vespr Telemetry API", version="0.1.0")

# endpoints


@app.get("/")
def root():
    return {"message": "Vespr Telemetry API is live."}


@app.get("/telemetry", response_model=TelemetryFrame)
def get_telemetry(sim: FlightSimulator = Depends(get_simulator)):
    return sim.generate_frame()


@app.get("/telemetry/stream")
async def stream_telemetry(sim: FlightSimulator = Depends(get_simulator)):
    async def event_generator():
        while True:
            frame = sim.generate_frame()
            data = frame.model_dump_json()
            yield f"data: {data}\n\n"
            await asyncio.sleep(0.1)  # wait 100ms between frames

    return StreamingResponse(event_generator(), media_type="text/event-stream")

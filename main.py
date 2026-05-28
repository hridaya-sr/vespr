import asyncio

from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import StreamingResponse
from models import FlightSummary, HealthResponse, TelemetryFrame
from simulation import FlightSimulator
from dependencies import get_simulator

app = FastAPI(title="Vespr Telemetry API", version="0.1.0")

# endpoints


@app.get("/", response_model=HealthResponse)
def root():
    return HealthResponse(
        message="Vespr Telemetry API is live.", version="0.1.0", status="ok"
    )


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


@app.get("/flights/summary", response_model=FlightSummary)
def flight_summary(sim: FlightSimulator = Depends(get_simulator)):
    if not sim.flight_history:
        raise HTTPException(
            status_code=404, detail="No flight data yet — hit /telemetry first"
        )
    return FlightSummary(
        total_frames=len(sim.flight_history),
        latest_phase=sim.flight_history[-1].phase,
        max_altitude_m=max(f.altitude_m for f in sim.flight_history),
    )

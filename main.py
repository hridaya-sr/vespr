import asyncio

from fastapi import FastAPI, Depends, HTTPException, WebSocket
from fastapi.websockets import WebSocketDisconnect
from fastapi.responses import StreamingResponse
from models import FlightSummary, HealthResponse, TelemetryFrame
from simulation import FlightSimulator
from dependencies import get_simulator
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Vespr Telemetry API", version="0.1.0")

# endpoints

@app.get("/", response_model=HealthResponse)
def root():
    return HealthResponse(
        message="Vespr Telemetry API is live.",
        version="0.1.0",
        status="ok"
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
            status_code=404,
            detail="No flight data yet — hit /telemetry first"
        )
    return FlightSummary(
        total_frames=len(sim.flight_history),
        latest_phase=sim.flight_history[-1].phase,
        max_altitude_m=max(f.altitude_m for f in sim.flight_history)
    )

@app.websocket("/ws/telemetry")
async def websocket_telemetry(
    websocket: WebSocket,
    sim: FlightSimulator = Depends(get_simulator)
):
    """Stream telemetry frames over WebSocket at 10Hz."""
    await websocket.accept()
    try:
        while True:
            frame = sim.generate_frame()
            await websocket.send_text(frame.model_dump_json())
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass


# CORS configuration to allow requests from the frontend application
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://vespr-nine.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

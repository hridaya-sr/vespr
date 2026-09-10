import asyncio

from fastapi import FastAPI, Depends, HTTPException, WebSocket
from fastapi.websockets import WebSocketDisconnect
from fastapi.responses import StreamingResponse
from models import FlightSummary, HealthResponse, TelemetryFrame
from simulation import FlightSimulator
from dependencies import get_simulator
from fastapi.middleware.cors import CORSMiddleware
import json
from ekf import BaroInertialEKF


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
    """Stream raw + EKF-filtered telemetry frames over WebSocket at 10Hz."""
    await websocket.accept()

    first_frame = sim.generate_frame()
    ekf = BaroInertialEKF(
        initial_altitude=first_frame.altitude_m,
        accel_noise_std=0.0128,
        baro_noise_std=2.0,
        bias_random_walk_std=0.01,
    )
    last_elapsed = first_frame.mission_elapsed_time_s
    last_phase = first_frame.phase

    output = {
        **first_frame.model_dump(),
        "altitude_filtered_m": round(ekf.altitude, 2),
        "velocity_filtered_ms": round(ekf.velocity, 2),
        "accel_bias_est": round(ekf.bias, 4),
    }
    await websocket.send_text(json.dumps(output))
    await asyncio.sleep(0.1)

    try:
        while True:
            frame = sim.generate_frame()

            if last_phase == "descent" and frame.phase == "boost":
                ekf.reset_flight()
            last_phase = frame.phase

            elapsed = frame.mission_elapsed_time_s
            dt = max(elapsed - last_elapsed, 1e-6)
            last_elapsed = elapsed

            ekf.predict(a_meas=frame.accel_z_ms2, dt=dt)
            ekf.update(z_baro=frame.altitude_m)

            output = {
                **frame.model_dump(),
                "altitude_filtered_m": round(ekf.altitude, 2),
                "velocity_filtered_ms": round(ekf.velocity, 2),
                "accel_bias_est": round(ekf.bias, 4),
            }
            await websocket.send_text(json.dumps(output))
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

import asyncio

from fastapi import FastAPI, Depends, File, HTTPException, UploadFile, WebSocket
from fastapi.websockets import WebSocketDisconnect
from fastapi.responses import StreamingResponse
from models import (
    CsvConfirmRequest,
    CsvConfirmResponse,
    CsvResetResponse,
    CsvSessionRequest,
    CsvStepRequest,
    CsvStepResponse,
    CsvUploadResponse,
    FlightSummary,
    HealthResponse,
    NormalizedRow,
    TelemetryFrame,
)
from simulation import FlightSimulator
from dependencies import get_simulator
from fastapi.middleware.cors import CORSMiddleware
import json
import csv_sessions
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


@app.post("/csv/upload", response_model=CsvUploadResponse)
async def upload_csv(file: UploadFile = File(...)):
    """Read a CSV's header + first few rows only. No unit conversion or
    schema mapping happens here — that's confirmed explicitly in /csv/confirm."""
    raw_bytes = await file.read()
    try:
        raw_text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded")

    session_id, session = csv_sessions.create_session(raw_text)
    if not session.columns:
        raise HTTPException(
            status_code=400,
            detail="No numeric columns found in this CSV — check it has a header row "
                   "and at least one column of plain numeric values",
        )

    return CsvUploadResponse(
        session_id=session_id,
        columns=session.columns,
        preview_rows=session.raw_rows[:csv_sessions.PREVIEW_ROW_COUNT],
        suggested_mapping=csv_sessions.suggest_mapping(session.columns),
    )


@app.post("/csv/confirm", response_model=CsvConfirmResponse)
def confirm_csv(body: CsvConfirmRequest):
    """Normalize the mapped columns to the internal schema (timestamp
    zeroed in seconds, altitude in meters) and store the result in memory
    against the session for later playback."""
    try:
        session = csv_sessions.get_session(body.session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown or expired CSV session")

    mapping_dict = body.mapping.model_dump()
    for field_name, col in mapping_dict.items():
        if col is not None and col not in session.columns:
            raise HTTPException(
                status_code=400,
                detail=f"Column '{col}' selected for {field_name} was not found in the uploaded CSV",
            )

    selected_columns = [col for col in mapping_dict.values() if col is not None]
    if len(set(selected_columns)) != len(selected_columns):
        raise HTTPException(
            status_code=400,
            detail="Each mapped field must use a different column",
        )

    try:
        normalized = csv_sessions.normalize_rows(session, body.mapping, body.units)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="One or more mapped columns contain non-numeric values",
        )

    return CsvConfirmResponse(
        session_id=body.session_id,
        row_count=len(normalized),
        rows=[
            NormalizedRow(
                timestamp=row["timestamp"],
                altitude=row["altitude"],
                acceleration=row["acceleration"],
            )
            for row in normalized
        ],
        acceleration_derived=session.acceleration_derived,
    )


@app.post("/csv/step", response_model=CsvStepResponse)
def step_csv(body: CsvStepRequest):
    """Advance one normalized row through the session's EKF. Called
    repeatedly by the frontend's playback timer, one row per tick — the
    EKF instance is shared per-session, so calls must arrive in index order."""
    try:
        session = csv_sessions.get_session(body.session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown or expired CSV session")

    try:
        frame = csv_sessions.step(session, body.index)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IndexError:
        raise HTTPException(
            status_code=400,
            detail=f"Index {body.index} out of range for {len(session.normalized_rows)} rows",
        )

    return CsvStepResponse(**frame)


@app.post("/csv/seek", response_model=list[CsvStepResponse])
def seek_csv(body: CsvStepRequest):
    """Reconstruct the EKF state at an arbitrary earlier frame by resetting
    to a fresh EKF and replaying from row 0 up to the target index. Used
    for stepping backward and scrubbing — see csv_sessions.seek's docstring
    for why this can't just reuse the shared session EKF, and why it
    returns every frame up to the target rather than just that one."""
    try:
        session = csv_sessions.get_session(body.session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown or expired CSV session")

    try:
        frames = csv_sessions.seek(session, body.index)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IndexError:
        raise HTTPException(
            status_code=400,
            detail=f"Index {body.index} out of range for {len(session.normalized_rows)} rows",
        )

    return [CsvStepResponse(**f) for f in frames]


@app.post("/csv/reset", response_model=CsvResetResponse)
def reset_csv(body: CsvSessionRequest):
    """Replay: reset the session's EKF (deliberately keeps the learned
    accel bias, per BaroInertialEKF.reset_flight's own contract) so
    playback can restart from the same normalized rows in memory."""
    try:
        session = csv_sessions.get_session(body.session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown or expired CSV session")

    try:
        state = csv_sessions.reset_playback(session)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return CsvResetResponse(**state)


# CORS configuration to allow requests from the frontend application
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://vespr-nine.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

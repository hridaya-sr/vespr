
# VESPR: Full Stack Flight Telemetry Dashboard

A full-stack rocket telemetry platform, built from scratch to explore what real flight-computer software actually looks like: ingestion, state estimation, and live visualization, end to end.

**Live:** [vespr-nine.vercel.app](https://vespr-nine.vercel.app/)

## What it does

VESPR takes rocket sensor data (barometric altitude + IMU acceleration) and turns it into a clean, real-time picture of a flight: altitude, velocity, and acceleration bias. It runs two ways: streamed live over WebSocket as a simulated flight happens, or replayed frame-by-frame from an uploaded CSV flight log through that same pipeline.

The core of the system is a 3-state baro-inertial Extended Kalman Filter that fuses noisy barometer and accelerometer readings into a smooth state estimate; the same class of problem a real flight computer has to solve.

## Architecture

```
┌─────────────┐      WebSocket       ┌──────────────┐      REST/WS       ┌────────────-┐
│  Telemetry  │ ───────────────────▶ │   FastAPI    │ ────────────────▶  │   Next.js   │
│   Source    │                      │   Backend    │                    │   Frontend  │
│ (CSV / sim) │                      │  (EKF core)  │                    │  (Recharts) │
└─────────────┘                      └──────────────┘                    └─────────────┘
                                       Render deploy                       Vercel deploy
```

- **Backend** - FastAPI, deployed on Render. Runs the EKF, manages flight state, and streams updates over WebSocket.
- **Frontend** - Next.js/React, deployed on Vercel. Subscribes to the telemetry stream and renders live charts with Recharts.
- **State estimation** - 3-state EKF (altitude, velocity, accelerometer bias), validated against the EuRoC 2023 dataset.

## CSV Flight Log Replay

Live simulated telemetry is the easy case — a real flight log is messier, and there's no fixed schema, since every flight computer, altimeter, or GPS logger exports differently. So VESPR also accepts an uploaded CSV and replays it through the same EKF as the live demo, one frame at a time.

- **Guided column mapping** - after upload, a mapping screen shows the file's columns and lets you point VESPR at whichever ones are timestamp, altitude, and (optionally) velocity/acceleration, instead of assuming a specific device's export format.
- **Unit conversion** - altitude in meters or feet, and timestamps as elapsed seconds, elapsed milliseconds, or wall-clock time (auto-detected from the column's format) - all normalized to VESPR's internal schema before the data ever reaches the filter.
- **Playback controls** - play/pause, variable speed (0.25x-20x), step forward/backward one frame at a time, and scrubbing to any frame in the file.
- **Raw vs. EKF-filtered altitude overlay** - the replay chart plots the raw uploaded altitude against the EKF's fused estimate on the same axes, so the filter's job is visible rather than assumed: it's smoothing real, noisy sensor data live, not just re-plotting the input.
- **Derived acceleration, honestly** - some real-world logs (a GPS-only tracker with no onboard accelerometer, for instance) simply have no acceleration channel. Rather than pretend a signal exists that was never measured, VESPR numerically differentiates velocity (or double-differentiates altitude, if that's all there is) to synthesize one, and tracks it internally as derived rather than measured.

Both live streaming and CSV replay run against the same deployed backend - the flow above works on [vespr-nine.vercel.app](https://vespr-nine.vercel.app/), not just locally.

## State estimation

The filter fuses two noisy signals into one trustworthy state:

- **State vector:** altitude, velocity, accel_bias
- **Cold-start handling:** the filter seeds itself from the first real frame's altitude instead of assuming zero, avoiding an initial-condition transient
- **Bias persistence:** `reset_flight()` clears flight state between runs but preserves the learned accelerometer bias, so the filter doesn't have to relearn it from scratch every time


Built by working through Roger Labbe's *Kalman and Bayesian Filters in Python* and implementing the theory from scratch rather than dropping in a library filter.

## Getting started

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

Set the frontend's WebSocket/API base URL to point at your backend instance (local or deployed) via environment variables.


## Why this exists

VESPR is a portfolio project that I worked on to learn full-stack web app development and avionics concepts.

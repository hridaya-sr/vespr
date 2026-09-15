
# VESPR: Full Stack Flight Telemetry Dashboard

A full-stack rocket telemetry platform, built from scratch to explore what real flight-computer software actually looks like: ingestion, state estimation, and live visualization, end to end.

**Live:** [vespr-nine.vercel.app](https://vespr-nine.vercel.app/)

## What it does

VESPR takes rocket sensor data (barometric altitude + IMU acceleration) and turns it into a clean picture of a flight: altitude, velocity, and acceleration bias. Right now that means uploading a CSV flight log and replaying it frame-by-frame through the filter. The backend also implements a simulated live telemetry feed over WebSocket/SSE - it's the piece the project started with - but the frontend doesn't currently subscribe to it; today's UI is CSV replay only.

The core of the system is a 3-state baro-inertial Extended Kalman Filter that fuses noisy barometer and accelerometer readings into a smooth state estimate; the same class of problem a real flight computer has to solve.

## Architecture

```
┌──────────────┐         REST          ┌──────────────┐
│   Next.js    │ ────────────────────▶ │   FastAPI    │
│   Frontend   │ ◀──────────────────── │   Backend    │
│  (Recharts)  │   upload/mapping/     │  (EKF core)  │
└──────────────┘   step/seek/reset     └──────────────┘
  Vercel deploy                          Render deploy
```

- **Backend** - FastAPI, deployed on Render. Runs the EKF, holds CSV session state in memory, and also exposes a simulated live telemetry feed over WebSocket (`/ws/telemetry`) and SSE (`/telemetry/stream`) - implemented and reachable, but not currently called by the frontend.
- **Frontend** - Next.js/React, deployed on Vercel. Drives the whole CSV upload → mapping → normalize → replay flow over plain REST, and renders the raw-vs-EKF charts with Recharts. No live-telemetry view exists in the UI yet.
- **State estimation** - 3-state EKF (altitude, velocity, accelerometer bias), validated against the EuRoC 2023 dataset, shared by both the live-telemetry and CSV-replay code paths on the backend.

## CSV Flight Log Replay

A real flight log is messy, and there's no fixed schema, since every flight computer, altimeter, or GPS logger exports differently. VESPR accepts an uploaded CSV and replays it through the EKF one frame at a time - this is currently the app's only way to see data flow through the filter, live simulated telemetry being backend-only for now (see Architecture above).

- **Guided column mapping** - after upload, a mapping screen shows the file's columns and lets you point VESPR at whichever ones are timestamp, altitude, and (optionally) velocity/acceleration, instead of assuming a specific device's export format.
- **Unit conversion** - altitude in meters or feet, and timestamps as elapsed seconds, elapsed milliseconds, or wall-clock time (auto-detected from the column's format) - all normalized to VESPR's internal schema before the data ever reaches the filter.
- **Playback controls** - play/pause, variable speed (0.25x-20x), step forward/backward one frame at a time, and scrubbing to any frame in the file.
- **Raw vs. EKF-filtered altitude overlay** - the replay chart plots the raw uploaded altitude against the EKF's fused estimate on the same axes, so the filter's job is visible rather than assumed: it's smoothing real, noisy sensor data live, not just re-plotting the input.
- **Derived acceleration** - some real-world logs (a GPS-only tracker with no onboard accelerometer, for instance) simply have no acceleration channel. Rather than pretend a signal exists that was never measured, VESPR numerically differentiates velocity (or double-differentiates altitude, if that's all there is) to synthesize one, and tracks it internally as derived rather than measured.

Confirmed working against the deployed backend, not just locally - the flow above runs end to end on [vespr-nine.vercel.app](https://vespr-nine.vercel.app/).

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

Set the frontend's API base URL (`NEXT_PUBLIC_API_URL`) to point at your backend instance, local or deployed.


## Why this exists

VESPR is a portfolio project that I worked on to learn full-stack web app development and avionics concepts.

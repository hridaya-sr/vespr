
# VESPR: Full Stack Flight Telemetry Dashboard

A full-stack rocket telemetry platform, built from scratch to explore what real flight-computer software actually looks like: ingestion, state estimation, and live visualization, end to end.

**Live:** [vespr-nine.vercel.app](https://vespr-nine.vercel.app)

## What it does

VESPR takes raw rocket sensor data (barometric altitude + IMU acceleration) and turns it into a clean, real-time picture of a flight: altitude, velocity, and acceleration bias, streamed to a dashboard as the flight happens.

The core of the system is a 3-state baro-inertial Extended Kalman Filter that fuses noisy barometer and accelerometer readings into a smooth state estimate; the same class of problem a real flight computer has to solve.

## Architecture

```
┌─────────────┐      WebSocket       ┌──────────────┐      REST/WS      ┌─────────────┐
│  Telemetry  │ ───────────────────▶ │   FastAPI    │ ────────────────▶ │   Next.js    │
│   Source    │                      │   Backend    │                    │   Frontend   │
│ (CSV / sim) │                      │  (EKF core)  │                    │  (Recharts)  │
└─────────────┘                      └──────────────┘                    └─────────────┘
                                       Render deploy                       Vercel deploy
```

- **Backend** - FastAPI, deployed on Render. Runs the EKF, manages flight state, and streams updates over WebSocket.
- **Frontend** - Next.js/React, deployed on Vercel. Subscribes to the telemetry stream and renders live charts with Recharts.
- **State estimation** - 3-state EKF (altitude, velocity, accelerometer bias), validated against the EuRoC 2023 dataset.

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
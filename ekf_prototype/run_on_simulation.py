"""
Step 2 — run the EKF against VESPR's actual flight_physics(), not synthetic data.

Uses a fixed-dt loop instead of FlightSimulator's wall-clock generate_frame(),
since batch validation needs deterministic timing. Uses the SAME physics
constants and noise model as backend/simulation.py, so this is testing the
real flight profile — just sampled offline instead of over a live WebSocket.

A slowly-wandering accelerometer bias is injected here in the harness (NOT
in simulation.py) because the current simulator doesn't model one. This is
the one thing worth adding to simulation.py for realism once you're ready —
see the note at the bottom of this file.
"""

import sys
import os
import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from simulation import flight_physics, BOOST_TIME, BOOST_ACCEL, GRAVITY  # noqa: E402

from ekf import BaroInertialEKF  # noqa: E402


def build_dataset(dt_imu=0.01, dt_baro=0.05, n_cycles=1, inject_bias=True):
    v0 = BOOST_ACCEL * BOOST_TIME
    s0 = 0.5 * BOOST_ACCEL * BOOST_TIME**2
    coast_time = v0 / GRAVITY
    apogee_altitude = s0 + v0**2 / (2 * GRAVITY)
    descent_time = np.sqrt(2 * apogee_altitude / GRAVITY)
    loop_period = BOOST_TIME + coast_time + descent_time

    duration = loop_period * n_cycles
    t_imu = np.arange(0, duration, dt_imu)
    n = len(t_imu)

    true_altitude = np.zeros(n)
    true_velocity = np.zeros(n)
    true_accel = np.zeros(n)

    for i, t in enumerate(t_imu):
        t_cycle = t % loop_period
        _, a, v, alt = flight_physics(
            t_cycle, BOOST_TIME, BOOST_ACCEL, GRAVITY,
            v0, s0, coast_time, apogee_altitude,
        )
        true_accel[i] = a
        true_velocity[i] = v
        true_altitude[i] = alt

    # Same noise model as backend/simulation.py
    accel_noise_std = 0.0013 * 9.81
    baro_noise_std = 2.0

    if inject_bias:
        true_bias = 0.3 + 0.05 * np.sin(0.2 * t_imu)
    else:
        true_bias = np.zeros(n)

    a_meas = true_accel + true_bias + np.random.normal(0, accel_noise_std, n)

    baro_indices = np.arange(0, n, int(dt_baro / dt_imu))
    z_baro = true_altitude[baro_indices] + np.random.normal(
        0, baro_noise_std, len(baro_indices)
    )

    return {
        "t_imu": t_imu,
        "true_altitude": true_altitude,
        "true_velocity": true_velocity,
        "true_bias": true_bias,
        "a_meas": a_meas,
        "baro_indices": baro_indices,
        "z_baro": z_baro,
        "dt_imu": dt_imu,
        "accel_noise_std": accel_noise_std,
        "baro_noise_std": baro_noise_std,
    }


def run_filter(data):
    # Retuned to match simulation.py's actual noise levels, not Step 1's placeholders
    ekf = BaroInertialEKF(
        initial_altitude=0.0,
        accel_noise_std=data["accel_noise_std"],
        baro_noise_std=data["baro_noise_std"],
        bias_random_walk_std=0.01,
    )

    n = len(data["t_imu"])
    est_altitude = np.zeros(n)
    est_velocity = np.zeros(n)
    est_bias = np.zeros(n)

    baro_indices = data["baro_indices"]
    z_baro = data["z_baro"]
    baro_ptr = 0

    for i in range(n):
        ekf.predict(a_meas=data["a_meas"][i], dt=data["dt_imu"])
        if baro_ptr < len(baro_indices) and baro_indices[baro_ptr] == i:
            ekf.update(z_baro[baro_ptr])
            baro_ptr += 1
        est_altitude[i] = ekf.altitude
        est_velocity[i] = ekf.velocity
        est_bias[i] = ekf.bias

    return est_altitude, est_velocity, est_bias


def plot_and_report(data, est_altitude, est_velocity, est_bias):
    t = data["t_imu"]
    baro_t = t[data["baro_indices"]]

    fig, axes = plt.subplots(3, 1, figsize=(10, 10), sharex=True)

    axes[0].plot(t, data["true_altitude"], label="True altitude", linewidth=2)
    axes[0].scatter(baro_t, data["z_baro"], s=4, color="gray", alpha=0.4, label="Raw barometer")
    axes[0].plot(t, est_altitude, label="EKF filtered", linewidth=1.5)
    axes[0].set_ylabel("Altitude (m)")
    axes[0].legend()
    axes[0].set_title("VESPR flight_physics(): altitude, truth vs. raw baro vs. EKF")

    axes[1].plot(t, data["true_velocity"], label="True velocity", linewidth=2)
    axes[1].plot(t, est_velocity, label="EKF estimated velocity", linewidth=1.5)
    axes[1].set_ylabel("Velocity (m/s)")
    axes[1].legend()

    axes[2].plot(t, data["true_bias"], label="Injected true bias", linewidth=2)
    axes[2].plot(t, est_bias, label="EKF estimated bias", linewidth=1.5)
    axes[2].set_ylabel("Bias (m/s^2)")
    axes[2].set_xlabel("Time (s)")
    axes[2].legend()

    plt.tight_layout()
    plt.savefig("vespr_ekf_on_real_sim.png", dpi=150)
    print("Saved plot to vespr_ekf_on_real_sim.png")

    raw_baro_interp = np.interp(t, baro_t, data["z_baro"])
    raw_rms = np.sqrt(np.mean((raw_baro_interp - data["true_altitude"]) ** 2))
    filt_rms = np.sqrt(np.mean((est_altitude - data["true_altitude"]) ** 2))
    print(f"Raw barometer RMS error: {raw_rms:.3f} m")
    print(f"EKF filtered RMS error:  {filt_rms:.3f} m")
    print(f"Improvement factor:      {raw_rms / filt_rms:.2f}x")


if __name__ == "__main__":
    np.random.seed(42)
    data = build_dataset(n_cycles=1, inject_bias=True)
    est_altitude, est_velocity, est_bias = run_filter(data)
    plot_and_report(data, est_altitude, est_velocity, est_bias)
"""
VESPR EKF Prototype — 3-state baro-inertial filter
State: x = [altitude, velocity, accel_bias]

Run standalone: generates a synthetic rocket-ish trajectory, corrupts it with
realistic sensor noise, runs the filter, and plots results.
"""

import numpy as np
import matplotlib.pyplot as plt


class BaroInertialEKF:
    """
    3-state linear KF (EKF-compatible interface) fusing barometric altitude
    with accelerometer-derived motion.

    State vector:
        x[0] = altitude   (m)
        x[1] = velocity   (m/s)
        x[2] = accel_bias (m/s^2)
    """

    def __init__(self, initial_altitude=0.0, accel_noise_std=0.3,
                 baro_noise_std=1.5, bias_random_walk_std=0.01):
        # State estimate
        self.x = np.array([initial_altitude, 0.0, 0.0])

        # State covariance — start uncertain about velocity and bias,
        # fairly confident about initial altitude if you trust your baro zero.
        self.P = np.diag([2.0, 5.0, 1.0])

        # Sensor / process noise characteristics — TUNE THESE against your
        # actual IMU and barometer datasheets once you have hardware.
        self.accel_noise_std = accel_noise_std      # accelerometer white noise
        self.baro_noise_std = baro_noise_std         # barometer measurement noise
        self.bias_rw_std = bias_random_walk_std       # how fast bias is allowed to wander

        # Measurement model is constant: barometer observes altitude only.
        self.H = np.array([[1.0, 0.0, 0.0]])
        self.R = np.array([[baro_noise_std ** 2]])

    def predict(self, a_meas, dt):
        """
        Predict step. Call at IMU rate.
        a_meas: raw accelerometer reading (m/s^2), NOT yet bias-corrected.
        dt: time since last predict call (s)
        """
        F = np.array([
            [1.0, dt, -0.5 * dt ** 2],
            [0.0, 1.0, -dt],
            [0.0, 0.0, 1.0],
        ])
        B = np.array([0.5 * dt ** 2, dt, 0.0])

        # Propagate state using accel as a control input
        self.x = F @ self.x + B * a_meas

        # Process noise: how much uncertainty we inject by trusting the model.
        # Two independent noise sources: accelerometer white noise (drives
        # altitude/velocity uncertainty growth) and bias random walk.
        q_accel = self.accel_noise_std ** 2
        Q = np.array([
            [0.25 * dt ** 4, 0.5 * dt ** 3, 0.0],
            [0.5 * dt ** 3, dt ** 2, 0.0],
            [0.0, 0.0, 0.0],
        ]) * q_accel
        Q[2, 2] = self.bias_rw_std ** 2 * dt  # bias random walk grows with time

        self.P = F @ self.P @ F.T + Q

    def update(self, z_baro):
        """
        Update step. Call whenever a new barometer reading arrives.
        z_baro: measured altitude (m)
        """
        y = np.array([z_baro]) - self.H @ self.x           # innovation
        S = self.H @ self.P @ self.H.T + self.R            # innovation covariance
        K = self.P @ self.H.T @ np.linalg.inv(S)            # Kalman gain

        self.x = self.x + (K @ y)
        I = np.eye(3)
        self.P = (I - K @ self.H) @ self.P
    
    def reset_flight(self):
        """
        Call when a new flight cycle begins (touchdown -> relaunch in the
        demo simulator's loop). Resets altitude/velocity to ground state,
        but deliberately keeps the bias estimate.
        """
        self.x[0] = 0.0
        self.x[1] = 0.0
        self.P[0, 0] = 2.0
        self.P[1, 1] = 5.0
        self.P[0, 1] = self.P[1, 0] = 0.0
        self.P[0, 2] = self.P[2, 0] = 0.0
        self.P[1, 2] = self.P[2, 1] = 0.0

    @property
    def altitude(self):
        return self.x[0]

    @property
    def velocity(self):
        return self.x[1]

    @property
    def bias(self):
        return self.x[2]


def generate_synthetic_flight(duration=20.0, dt_imu=0.01, dt_baro=0.05):
    """
    Generates a simple rocket-like trajectory: powered ascent, coast,
    apogee, descent. Returns ground truth plus noisy sensor streams.
    """
    t_imu = np.arange(0, duration, dt_imu)
    n = len(t_imu)

    true_accel = np.zeros(n)
    burn_time = 3.0
    for i, t in enumerate(t_imu):
        if t < burn_time:
            true_accel[i] = 30.0          # powered ascent, ~3g net
        else:
            true_accel[i] = -9.81         # unpowered, gravity only

    true_velocity = np.cumsum(true_accel) * dt_imu
    true_altitude = np.cumsum(true_velocity) * dt_imu
    true_altitude = np.clip(true_altitude, 0, None)  # don't go underground

    # Inject a realistic, slowly wandering accelerometer bias
    true_bias = 0.4 + 0.05 * np.sin(0.1 * t_imu)

    accel_noise_std = 0.3
    a_meas = true_accel + true_bias + np.random.normal(0, accel_noise_std, n)

    # Barometer samples at a lower rate
    baro_indices = np.arange(0, n, int(dt_baro / dt_imu))
    baro_noise_std = 1.5
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
    }


def run_filter(data):
    ekf = BaroInertialEKF(initial_altitude=0.0)

    n = len(data["t_imu"])
    est_altitude = np.zeros(n)
    est_velocity = np.zeros(n)
    est_bias = np.zeros(n)

    baro_set = set(data["baro_indices"].tolist())
    baro_ptr = 0
    baro_indices = data["baro_indices"]
    z_baro = data["z_baro"]

    for i in range(n):
        ekf.predict(a_meas=data["a_meas"][i], dt=data["dt_imu"])

        if baro_ptr < len(baro_indices) and baro_indices[baro_ptr] == i:
            ekf.update(z_baro[baro_ptr])
            baro_ptr += 1

        est_altitude[i] = ekf.altitude
        est_velocity[i] = ekf.velocity
        est_bias[i] = ekf.bias

    return est_altitude, est_velocity, est_bias


def plot_results(data, est_altitude, est_velocity, est_bias):
    t = data["t_imu"]
    baro_t = t[data["baro_indices"]]

    fig, axes = plt.subplots(3, 1, figsize=(10, 10), sharex=True)

    axes[0].plot(t, data["true_altitude"], label="True altitude", linewidth=2)
    axes[0].scatter(baro_t, data["z_baro"], s=4, color="gray",
                     alpha=0.4, label="Raw barometer")
    axes[0].plot(t, est_altitude, label="EKF filtered", linewidth=1.5)
    axes[0].set_ylabel("Altitude (m)")
    axes[0].legend()
    axes[0].set_title("Altitude: truth vs. raw baro vs. EKF")

    axes[1].plot(t, data["true_velocity"], label="True velocity", linewidth=2)
    axes[1].plot(t, est_velocity, label="EKF estimated velocity", linewidth=1.5)
    axes[1].set_ylabel("Velocity (m/s)")
    axes[1].legend()
    axes[1].set_title("Velocity: truth vs. EKF estimate (no direct sensor)")

    axes[2].plot(t, data["true_bias"], label="True accel bias", linewidth=2)
    axes[2].plot(t, est_bias, label="EKF estimated bias", linewidth=1.5)
    axes[2].set_ylabel("Bias (m/s^2)")
    axes[2].set_xlabel("Time (s)")
    axes[2].legend()
    axes[2].set_title("Accelerometer bias: convergence")

    plt.tight_layout()
    plt.savefig("vespr_ekf_results.png", dpi=150)
    print("Saved plot to vespr_ekf_results.png")

    raw_baro_interp = np.interp(t, baro_t, data["z_baro"])
    raw_rms = np.sqrt(np.mean((raw_baro_interp - data["true_altitude"]) ** 2))
    filt_rms = np.sqrt(np.mean((est_altitude - data["true_altitude"]) ** 2))
    print(f"Raw barometer RMS error:   {raw_rms:.3f} m")
    print(f"EKF filtered RMS error:    {filt_rms:.3f} m")
    print(f"Improvement factor:        {raw_rms / filt_rms:.2f}x")


if __name__ == "__main__":
    np.random.seed(42)
    data = generate_synthetic_flight()
    est_altitude, est_velocity, est_bias = run_filter(data)
    plot_results(data, est_altitude, est_velocity, est_bias)
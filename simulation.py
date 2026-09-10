import math
import numpy as np
from datetime import datetime, timezone
from models import TelemetryFrame
from collections import deque

GRAVITY = 9.81  # m/s^2

BOOST_ACCEL = 60.0  # m/s^2 (net upward accel during burn)
BOOST_TIME = 2.0    # s


def flight_physics(t, boost_time, boost_accel, gravity, v0, s0, coast_time, apogee_altitude):
    """Pure function version of the phase/accel/velocity/altitude logic."""
    if t < boost_time:
        phase = "boost"
        true_accel_z = boost_accel
        true_velocity = boost_accel * t
        true_altitude = 0.5 * boost_accel * t**2
    elif t < boost_time + coast_time:
        phase = "coast"
        coast_t = t - boost_time
        true_accel_z = -gravity
        true_velocity = v0 - gravity * coast_t
        true_altitude = s0 + v0 * coast_t - 0.5 * gravity * coast_t**2
    else:
        phase = "descent"
        descent_t = t - boost_time - coast_time
        true_accel_z = -gravity
        true_velocity = -gravity * descent_t
        true_altitude = max(0, apogee_altitude - 0.5 * gravity * descent_t**2)
    return phase, true_accel_z, true_velocity, true_altitude


class FlightSimulator:
    def __init__(self):
        self._rng = np.random.default_rng()
        self.launch_time = datetime.now(timezone.utc)
        self.flight_history: deque[TelemetryFrame] = deque(maxlen=2000)

        self.v0 = BOOST_ACCEL * BOOST_TIME
        self.s0 = 0.5 * BOOST_ACCEL * BOOST_TIME**2
        self.coast_time = self.v0 / GRAVITY
        self.apogee_altitude = self.s0 + self.v0**2 / (2 * GRAVITY)
        self.descent_time = math.sqrt(2 * self.apogee_altitude / GRAVITY)
        self.loop_period = BOOST_TIME + self.coast_time + self.descent_time

    def generate_frame(self) -> TelemetryFrame:
        now = datetime.now(timezone.utc)
        elapsed = (now - self.launch_time).total_seconds()
        t = elapsed % self.loop_period

        phase, true_accel_z, true_velocity, true_altitude = flight_physics(
            t, BOOST_TIME, BOOST_ACCEL, GRAVITY,
            self.v0, self.s0, self.coast_time, self.apogee_altitude,
        )

        baro_noise = self._rng.normal(0, 2.0)
        accel_noise = self._rng.normal(0, 0.0013 * 9.81, size=3)
        gyro_noise = self._rng.normal(0, 0.00087, size=3)

        frame = TelemetryFrame(
            timestamp=now.isoformat(),
            mission_elapsed_time_s=round(elapsed, 3),
            altitude_m=round(float(true_altitude + baro_noise), 2),
            velocity_ms=round(float(true_velocity), 2),
            accel_x_ms2=round(float(accel_noise[0]), 4),
            accel_y_ms2=round(float(accel_noise[1]), 4),
            accel_z_ms2=round(float(true_accel_z + accel_noise[2]), 4),
            gyro_x_rads=round(float(gyro_noise[0]), 4),
            gyro_y_rads=round(float(gyro_noise[1]), 4),
            gyro_z_rads=round(float(gyro_noise[2]), 4),
            phase=phase,
        )
        self.flight_history.append(frame)
        return frame

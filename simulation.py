import math
import numpy as np
from datetime import datetime, timezone
from models import TelemetryFrame
from collections import deque

GRAVITY = 9.81  # m/s^2

# Boost phase parameters — tune these to change flight profile
BOOST_ACCEL = 60.0  # m/s^2 (net upward accel during burn)
BOOST_TIME = 2.0    # s


class FlightSimulator:
    def __init__(self):
        self._rng = np.random.default_rng()
        self.launch_time = datetime.now(timezone.utc)
        self.flight_history: deque[TelemetryFrame] = deque(maxlen=2000)

        # Derive the rest of the flight profile from boost params so
        # phases are physically consistent (velocity actually crosses
        # zero at apogee, altitude actually returns to 0 at touchdown).
        self.v0 = BOOST_ACCEL * BOOST_TIME
        self.s0 = 0.5 * BOOST_ACCEL * BOOST_TIME**2

        self.coast_time = self.v0 / GRAVITY  # time from burnout to apogee
        self.apogee_altitude = self.s0 + self.v0**2 / (2 * GRAVITY)

        # free-fall from apogee back to the ground
        self.descent_time = math.sqrt(2 * self.apogee_altitude / GRAVITY)

        self.loop_period = BOOST_TIME + self.coast_time + self.descent_time

    def generate_frame(self) -> TelemetryFrame:
        now = datetime.now(timezone.utc)
        elapsed = (now - self.launch_time).total_seconds()

        t = elapsed % self.loop_period

        if t < BOOST_TIME:
            # Boost: engine burning, accelerating upwards
            phase = "boost"
            true_accel_z = BOOST_ACCEL
            true_velocity = BOOST_ACCEL * t
            true_altitude = 0.5 * BOOST_ACCEL * t**2

        elif t < BOOST_TIME + self.coast_time:
            # Coast: engine off, only gravity acting, decelerating to apogee
            phase = "coast"
            coast_t = t - BOOST_TIME
            true_accel_z = -GRAVITY
            true_velocity = self.v0 - GRAVITY * coast_t
            true_altitude = (
                self.s0 + self.v0 * coast_t - 0.5 * GRAVITY * coast_t**2
            )

        else:
            # Descent: free-fall from apogee back to the ground
            phase = "descent"
            descent_t = t - BOOST_TIME - self.coast_time
            true_accel_z = -GRAVITY
            true_velocity = -GRAVITY * descent_t
            true_altitude = max(
                0, self.apogee_altitude - 0.5 * GRAVITY * descent_t**2
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
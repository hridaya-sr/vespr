"""
Generates test_flight.csv: a synthetic flight log for exercising the CSV
upload/replay feature. Reuses the boost/coast/descent physics from
simulation.py so the shape of the data matches the live demo simulator,
then adds the same noise model as FlightSimulator.generate_frame() so the
raw altitude/acceleration look like real sensor readings.
"""

import csv

import numpy as np

from simulation import BOOST_ACCEL, BOOST_TIME, GRAVITY, flight_physics

ROW_COUNT = 250
OUTPUT_PATH = "test_flight.csv"


def main():
    rng = np.random.default_rng(42)

    v0 = BOOST_ACCEL * BOOST_TIME
    s0 = 0.5 * BOOST_ACCEL * BOOST_TIME**2
    coast_time = v0 / GRAVITY
    apogee_altitude = s0 + v0**2 / (2 * GRAVITY)
    descent_time = (2 * apogee_altitude / GRAVITY) ** 0.5
    flight_duration = BOOST_TIME + coast_time + descent_time

    timestamps = np.linspace(0, flight_duration, ROW_COUNT)

    rows = []
    for t in timestamps:
        _, true_accel_z, _, true_altitude = flight_physics(
            t, BOOST_TIME, BOOST_ACCEL, GRAVITY, v0, s0, coast_time, apogee_altitude,
        )
        altitude = true_altitude + rng.normal(0, 2.0)
        acceleration = true_accel_z + rng.normal(0, 0.0013 * 9.81)
        rows.append((round(float(t), 3), round(float(altitude), 3), round(float(acceleration), 4)))

    with open(OUTPUT_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "altitude", "acceleration"])
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

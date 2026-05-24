from simulation import FlightSimulator

_simulator = FlightSimulator()


def get_simulator() -> FlightSimulator:
    return _simulator

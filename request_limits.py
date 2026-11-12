"""Smooth request pacing shared by workers; no burst allowance."""
import threading
import time


class RequestPacer:
    def __init__(self, units_per_second, clock=time.monotonic, sleep=time.sleep):
        if units_per_second <= 0:
            raise ValueError("Request rate must be positive")
        self.rate = units_per_second
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.next_at = 0.0
        self.requests = 0
        self.units = 0

    def wait(self, units=1):
        with self.lock:
            delay = self.next_at - self.clock()
            if delay > 0:
                self.sleep(delay)
            self.next_at = self.clock() + units / self.rate
            self.requests += 1
            self.units += units


# This is a conservative local ceiling, NOT a published Ryanair allowance.
RYANAIR_PACER = RequestPacer(2)

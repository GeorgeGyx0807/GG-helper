"""Cooperative cancellation primitives shared by runtime and providers."""

from threading import Event


class RunCancelled(RuntimeError):
    """Raised when an active Pico run is cancelled by its caller."""


class CancellationToken:
    def __init__(self):
        self._event = Event()

    @property
    def cancelled(self):
        return self._event.is_set()

    def cancel(self):
        self._event.set()

    def raise_if_cancelled(self):
        if self.cancelled:
            raise RunCancelled("run cancelled")

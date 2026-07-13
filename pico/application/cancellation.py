"""Cooperative cancellation primitives shared by runtime and providers."""

from threading import Event, Lock


class RunCancelled(RuntimeError):
    """Raised when an active Pico run is cancelled by its caller."""


class CancellationToken:
    def __init__(self):
        self._event = Event()
        self._lock = Lock()
        self._callbacks = {}
        self._next_callback_id = 0

    @property
    def cancelled(self):
        return self._event.is_set()

    def cancel(self):
        self._event.set()
        with self._lock:
            callbacks = list(self._callbacks.values())
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass

    def add_cancel_callback(self, callback):
        """Run callback on cancellation and return a safe unregister function."""
        run_now = False
        with self._lock:
            if self.cancelled:
                run_now = True
                callback_id = None
            else:
                callback_id = self._next_callback_id
                self._next_callback_id += 1
                self._callbacks[callback_id] = callback
        if run_now:
            callback()

        def unregister():
            if callback_id is not None:
                with self._lock:
                    self._callbacks.pop(callback_id, None)

        return unregister

    def raise_if_cancelled(self):
        if self.cancelled:
            raise RunCancelled("run cancelled")

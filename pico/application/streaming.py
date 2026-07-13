"""Filter model protocol tags from user-visible streaming deltas."""


FINAL_OPEN = "<final>"
FINAL_CLOSE = "</final>"


class AssistantDeltaFilter:
    """Emit final-answer text while suppressing tool/control protocol markup."""

    def __init__(self, emit):
        self.emit = emit
        self.buffer = ""
        self.mode = "unknown"
        self.closed = False

    def feed(self, delta):
        if self.closed or not delta:
            return
        self.buffer += str(delta)
        if self.mode == "unknown":
            stripped = self.buffer.lstrip()
            if FINAL_OPEN in stripped:
                self.mode = "final"
                self.buffer = stripped.split(FINAL_OPEN, 1)[1]
            elif "<tool" in stripped:
                self.mode = "tool"
                return
            elif len(stripped) >= len(FINAL_OPEN) and not (
                FINAL_OPEN.startswith(stripped) or "<tool".startswith(stripped)
            ):
                self.mode = "plain"
                self.buffer = stripped
        self._drain()

    def finish(self):
        if self.closed or self.mode == "tool":
            self.buffer = ""
            return
        if self.mode == "unknown":
            stripped = self.buffer.lstrip()
            if stripped.startswith(FINAL_OPEN):
                self.mode = "final"
                self.buffer = stripped[len(FINAL_OPEN):]
            elif stripped.startswith("<tool"):
                self.mode = "tool"
                self.buffer = ""
                return
            else:
                self.mode = "plain"
                self.buffer = stripped
        if self.mode == "final" and FINAL_CLOSE in self.buffer:
            visible, _ = self.buffer.split(FINAL_CLOSE, 1)
            self._emit(visible)
        else:
            self._emit(self.buffer)
        self.buffer = ""
        self.closed = True

    def _drain(self):
        if self.mode == "plain":
            self._emit(self.buffer)
            self.buffer = ""
            return
        if self.mode != "final":
            return
        if FINAL_CLOSE in self.buffer:
            visible, _ = self.buffer.split(FINAL_CLOSE, 1)
            self._emit(visible)
            self.buffer = ""
            self.closed = True
            return
        hold = self._closing_prefix_length(self.buffer)
        visible = self.buffer[:-hold] if hold else self.buffer
        self._emit(visible)
        self.buffer = self.buffer[-hold:] if hold else ""

    def _emit(self, text):
        if text:
            self.emit(text)

    @staticmethod
    def _closing_prefix_length(text):
        max_size = min(len(text), len(FINAL_CLOSE) - 1)
        for size in range(max_size, 0, -1):
            if text.endswith(FINAL_CLOSE[:size]):
                return size
        return 0

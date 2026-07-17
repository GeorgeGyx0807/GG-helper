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
            final_index = stripped.find(FINAL_OPEN)
            tool_index = stripped.find("<tool")
            markers = [(index, "final") for index in (final_index,) if index >= 0]
            markers.extend((index, "tool") for index in (tool_index,) if index >= 0)
            if markers:
                _, mode = min(markers, key=lambda item: item[0])
                self.mode = mode
                if mode == "final":
                    self.buffer = stripped.split(FINAL_OPEN, 1)[1]
                else:
                    self.buffer = ""
                    return
            else:
                # Do not optimistically stream untagged text. A model may begin
                # with narration and append a <tool> block a few tokens later;
                # emitting early would leak the internal tool trace to the UI.
                return
        self._drain()

    def finish(self):
        if self.closed or self.mode == "tool":
            self.buffer = ""
            return
        if self.mode == "unknown":
            stripped = self.buffer.lstrip()
            if "<tool" in stripped:
                self.mode = "tool"
                self.buffer = ""
                return
            if FINAL_OPEN in stripped:
                self.mode = "final"
                self.buffer = stripped.split(FINAL_OPEN, 1)[1]
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

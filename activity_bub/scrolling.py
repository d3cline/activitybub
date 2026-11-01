"""Short, interruptible wheel easing that preserves native touchpad gestures."""
from contextlib import contextmanager
import math
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView, QListView


class SmoothFeedView(QListView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._position = self._target = 0.0
        self._writing = False
        self._preserving = 0
        self._last_tick = 0.0
        self.motion = QTimer(self)
        self.motion.setTimerType(Qt.TimerType.PreciseTimer)
        self.motion.setInterval(16)
        self.motion.timeout.connect(self._advance)
        self.verticalScrollBar().valueChanged.connect(self._external_scroll)
        self.verticalScrollBar().sliderPressed.connect(self.stop_scrolling)

    def stop_scrolling(self):
        self.motion.stop()
        self._position = self._target = float(self.verticalScrollBar().value())

    def _external_scroll(self, _):
        if not self._writing and not self._preserving:
            self.stop_scrolling()

    def _clamp(self, value):
        bar = self.verticalScrollBar()
        return max(bar.minimum(), min(bar.maximum(), value))

    def wheelEvent(self, event):
        # High-resolution gestures already carry pixel motion and/or native
        # momentum. Do not put another easing animation on top of them.
        if event.pixelDelta().y() and not event.modifiers():
            self.stop_scrolling()
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() - event.pixelDelta().y())
            event.accept()
            return
        if (not event.pixelDelta().isNull() or event.phase() != Qt.ScrollPhase.NoScrollPhase
                or not event.angleDelta().y() or event.modifiers()):
            self.stop_scrolling()
            super().wheelEvent(event)
            return
        lines = QApplication.wheelScrollLines()
        if lines == 0:
            event.accept()
            return
        step = self.viewport().height() if lines < 0 else max(24, self.fontMetrics().lineSpacing()) * lines
        distance = -event.angleDelta().y() / 120 * step
        bar = self.verticalScrollBar()
        if not self.motion.isActive() or (self._target - bar.value()) * distance < 0:
            self._position = self._target = float(bar.value())
        # A wheel burst can move quickly without leaving a long animation tail.
        reach = max(240, self.viewport().height() * 2)
        self._target = self._clamp(max(bar.value() - reach, min(bar.value() + reach, self._target + distance)))
        if not self.motion.isActive() and self._target != bar.value():
            self._last_tick = time.monotonic()
            self.motion.start()
        event.accept()

    def _advance(self):
        now = time.monotonic()
        elapsed = max(0, now - self._last_tick)
        self._last_tick = now
        self._target = self._clamp(self._target)
        remaining = self._target - self._position
        done = abs(remaining) < .75
        self._position = self._target if done else self._position + remaining * (1 - math.exp(-elapsed / .045))
        self._writing = True
        try:
            self.verticalScrollBar().setValue(round(self._position))
        finally:
            self._writing = False
        if done:
            self.motion.stop()

    @contextmanager
    def preserve_scroll_motion(self):
        """Rebase remaining wheel travel after layout or rolling-window changes."""
        running = self.motion.isActive()
        remaining = self._target - self.verticalScrollBar().value()
        self._preserving += 1
        try:
            yield
        finally:
            self._preserving -= 1
            if running:
                self._position = float(self.verticalScrollBar().value())
                self._target = self._clamp(self._position + remaining)
                self._last_tick = time.monotonic()

    def mousePressEvent(self, event):
        self.stop_scrolling()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        self.stop_scrolling()
        super().keyPressEvent(event)

    def hideEvent(self, event):
        self.stop_scrolling()
        super().hideEvent(event)

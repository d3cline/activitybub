"""Short, interruptible wheel easing that preserves native touchpad gestures."""
from contextlib import contextmanager
import math
import time

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QAbstractItemView, QListView


class SmoothFeedView(QListView):
    autoChanged = Signal(bool)
    needsMore = Signal()

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
        self.auto_motion = QTimer(self)
        self.auto_motion.setTimerType(Qt.TimerType.PreciseTimer)
        self.auto_motion.setInterval(16)
        self.auto_motion.timeout.connect(self._auto_advance)
        self.auto_speed = 60.0
        self._auto_position = 0.0
        self._auto_tick = 0.0
        self._last_more = 0.0
        self.verticalScrollBar().valueChanged.connect(self._external_scroll)
        self.verticalScrollBar().sliderPressed.connect(self.stop_all_scrolling)

    @property
    def auto_active(self):
        return self.auto_motion.isActive()

    def set_auto_scroll(self, enabled):
        enabled = bool(enabled)
        if enabled == self.auto_active:
            return
        self.stop_scrolling()
        if enabled:
            self._auto_position = float(self.verticalScrollBar().value())
            self._auto_tick = time.monotonic()
            self._last_more = 0.0
            self.auto_motion.start()
        else:
            self.auto_motion.stop()
        self.autoChanged.emit(enabled)

    def set_auto_speed(self, pixels_per_second):
        self.auto_speed = max(15.0, min(240.0, float(pixels_per_second)))
        self._auto_tick = time.monotonic()

    def stop_all_scrolling(self):
        self.stop_scrolling()
        self.set_auto_scroll(False)

    def _auto_advance(self):
        if not self.auto_active:
            return
        now = time.monotonic()
        elapsed = min(.1, max(0.0, now-self._auto_tick))
        self._auto_tick = now
        if (not self.isVisible() or self.window().isMinimized()
                or QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None):
            return
        bar = self.verticalScrollBar()
        self._auto_position = self._clamp(self._auto_position + self.auto_speed*elapsed)
        self._writing = True
        try:
            bar.setValue(round(self._auto_position))
        finally:
            self._writing = False
        if bar.maximum()-bar.value()<=max(500,self.viewport().height()) and now-self._last_more>=.25:
            self._last_more = now
            self.needsMore.emit()

    def stop_scrolling(self):
        self.motion.stop()
        self._position = self._target = float(self.verticalScrollBar().value())

    def _external_scroll(self, _):
        if not self._writing and not self._preserving:
            self.stop_all_scrolling()

    def _clamp(self, value):
        bar = self.verticalScrollBar()
        return max(bar.minimum(), min(bar.maximum(), value))

    def wheelEvent(self, event):
        self.set_auto_scroll(False)
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
            if self.auto_active:
                self._auto_position = float(self.verticalScrollBar().value())
                self._auto_tick = time.monotonic()

    def mousePressEvent(self, event):
        self.stop_all_scrolling()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        self.stop_all_scrolling()
        super().keyPressEvent(event)

    def hideEvent(self, event):
        self.stop_all_scrolling()
        super().hideEvent(event)

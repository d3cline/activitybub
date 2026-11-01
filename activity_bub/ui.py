from __future__ import annotations

import csv
import html
import json
import re
import time
from collections import OrderedDict, deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (QAbstractListModel, QBuffer, QIODevice, QModelIndex, QObject, QPoint, QRect, QRunnable, QSize, Qt,
                            QThread, QThreadPool, QTimer, QUrl, Signal)
from PySide6.QtGui import (QAction, QActionGroup, QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QImage,
                           QImageReader, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListView, QListWidget, QMainWindow, QMenu, QMessageBox, QPushButton, QSizePolicy,
    QProgressBar, QSpinBox, QSplitter, QStackedWidget, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
    QTextBrowser, QVBoxLayout, QWidget)

from . import __version__
from .content import Post, rich_text, web_url
from .engine import Engine
from .image_io import decode_raster
from .image_policy import supported as image_isolation_supported
from .performance import PROFILES, get_profile
from .network import Network, domain
from .scrolling import SmoothFeedView

from .themes import THEMES, get_theme, stylesheet

DIM = get_theme("bub").muted
MAX_FEED_WIDTH = 720
MAX_FEED_IMAGE_HEIGHT = 520
STYLE = stylesheet(get_theme("bub"))


def label(text, name="", wrap=False):
    w = QLabel(text)
    w.setTextFormat(Qt.TextFormat.PlainText)
    w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


def button(text, callback=None, name=""):
    w = QPushButton(text)
    if callback:
        w.clicked.connect(callback)
    w.setObjectName(name)
    w.setCursor(Qt.CursorShape.PointingHandCursor)
    return w


def safe_tooltip(text):
    return "<qt>"+html.escape(text).replace("\n","<br>")+"</qt>"


def plain_warning(parent,title,text):
    dialog=QMessageBox(QMessageBox.Icon.Warning,title,"",QMessageBox.StandardButton.Ok,parent)
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.setText(text)
    dialog.exec()


class PostTextBrowser(QTextBrowser):
    """The post reader can display our markup, never load documents or resources."""
    def __init__(self):
        super().__init__()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setAcceptDrops(False)
        self.setAcceptRichText(False)

    def loadResource(self,*_):
        return None

    def doSetSource(self,*_):
        pass


def clear_icon(color=DIM):
    """Own raster icon: GNOME palette SVGs need extensions Qt cannot resolve."""
    icon = QIcon()
    for size in (16, 32, 48):
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), max(1.5, size / 10), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        a, b = round(size * .3), round(size * .7)
        painter.drawLine(a, a, b, b)
        painter.drawLine(a, b, b, a)
        painter.end()
        icon.addPixmap(pix)
    return icon


def add_clear_action(edit):
    edit.setClearButtonEnabled(False)
    action = edit.addAction(clear_icon(), QLineEdit.ActionPosition.TrailingPosition)
    action.setText("Clear search")
    action.setToolTip("Clear search")
    action.setVisible(bool(edit.text()))
    action.triggered.connect(edit.clear)
    edit.textChanged.connect(lambda text: action.setVisible(bool(text)))
    return action


def age(stamp):
    if not stamp:
        return ""
    seconds = max(0, time.time() - stamp)
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


class CrawlerThread(QThread):
    event = Signal(str, object)

    def __init__(self, store):
        super().__init__()
        self.engine = Engine(store, lambda kind, data: self.event.emit(kind, data))

    def run(self):
        try:
            self.engine.run()
        except Exception as exc:
            self.event.emit("fatal", str(exc))


class ImageSignals(QObject):
    done = Signal(str, object)


class ImageTask(QRunnable):
    def __init__(self, url, network):
        super().__init__()
        # Python owns each task until its result arrives (or it is taken out
        # of the queue). This also makes QThreadPool.tryTake safe to use.
        self.setAutoDelete(False)
        self.url, self.network = url, network
        self.signals = ImageSignals()

    def run(self):
        try:
            data = self.network.request(self.url, max_bytes=5 * 1024 * 1024)
            picture = decode_raster(data,stop=getattr(self.network,"stop",None))
        except Exception:
            picture = QImage()
        self.signals.done.emit(self.url, picture)


class Images(QObject):
    changed = Signal(str)

    def __init__(self, stop, parent=None, network=None):
        super().__init__(parent)
        self.network = network or Network(stop)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.max_pending=8
        self.cache = OrderedDict()
        self.dimensions = OrderedDict()
        self.cache_bytes = 0
        self.fitted_cache = OrderedDict()
        self.fitted_bytes = 0
        self.failed = set()
        self.tasks = {}
        self.blocked = lambda: False
        self.isolated = image_isolation_supported()
        QImageReader.setAllocationLimit(32)

    def get(self, url):
        if url in self.cache:
            self.cache.move_to_end(url)
            return self.cache[url]
        return None

    def ensure(self, url):
        if not self.isolated or self.blocked() or not url or url in self.cache or url in self.failed or url in self.tasks or len(self.tasks) >= self.max_pending:
            return
        task = ImageTask(url, self.network)
        self.tasks[url] = task
        task.signals.done.connect(self.loaded)
        self.pool.start(task)

    def prioritize(self, urls):
        # Running requests finish normally; queued requests follow the current
        # viewport instead of delaying new visible images after a fast scroll.
        for url, task in list(self.tasks.items()):
            if self.pool.tryTake(task):
                self.tasks.pop(url, None)
        for url in dict.fromkeys(urls):
            self.ensure(url)

    def image_size(self, url):
        pix = self.cache.get(url)
        return pix.size() if pix is not None else self.dimensions.get(url, QSize())

    def fitted(self, url, size, ratio=1.0):
        pix = self.get(url)
        if pix is None:
            return None
        pixels = QSize(max(1, round(size.width()*ratio)), max(1, round(size.height()*ratio)))
        key = (url, pix.cacheKey(), pixels.width(), pixels.height(), ratio)
        if key not in self.fitted_cache:
            fitted = pix.scaled(pixels, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            fitted.setDevicePixelRatio(ratio)
            self.fitted_cache[key] = fitted
            self.fitted_bytes += fitted.width()*fitted.height()*4
            while len(self.fitted_cache)>64 or self.fitted_bytes>16*1024*1024:
                _, old = self.fitted_cache.popitem(last=False)
                self.fitted_bytes -= old.width()*old.height()*4
            return fitted
        self.fitted_cache.move_to_end(key)
        return self.fitted_cache[key]

    def loaded(self, url, data):
        self.tasks.pop(url, None)
        # Production tasks decode QImage off-thread. Bytes remain accepted for
        # cached/local callers; QPixmap creation stays on the GUI thread.
        picture = data if isinstance(data, QImage) else decode_raster(data)
        if not picture.isNull() and picture.width() <= 12000 and picture.height() <= 12000:
            picture.setDevicePixelRatio(1)
            previous = self.cache.pop(url, None)
            if previous is not None:
                self.cache_bytes -= previous.width() * previous.height() * 4
            pix = QPixmap.fromImage(picture)
            self.cache[url] = pix
            self.cache_bytes += pix.width() * pix.height() * 4
            self.dimensions[url] = pix.size()
            self.dimensions.move_to_end(url)
            while len(self.dimensions) > 4096:
                self.dimensions.popitem(last=False)
            while len(self.cache) > 64 or self.cache_bytes > 64 * 1024 * 1024:
                _, old = self.cache.popitem(last=False)
                self.cache_bytes -= old.width() * old.height() * 4
        else:
            self.failed.add(url)
            if len(self.failed) > 1024:
                self.failed.clear()
        self.changed.emit(url)


def preview(post):
    if not post.media:
        return ""
    m = post.media[0]
    return m.get("preview") or (m.get("url") if str(m.get("type", "")).startswith("image") else "")


class FeedModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.posts: list[Post] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.posts)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.posts):
            return None
        post = self.posts[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return post
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{post.author}, {post.text}"
        if role == Qt.ItemDataRole.ToolTipRole:
            return "Click or Enter to read · B saves · Ctrl+O opens original · Right click for more"

    def reset(self, posts):
        self.beginResetModel()
        self.posts = posts
        self.endResetModel()

    def append(self, posts):
        if not posts:
            return
        n = len(self.posts)
        self.beginInsertRows(QModelIndex(), n, n + len(posts) - 1)
        self.posts.extend(posts)
        self.endInsertRows()

    def trim_front(self, count):
        self.beginRemoveRows(QModelIndex(), 0, count - 1)
        del self.posts[:count]
        self.endRemoveRows()


class Cards(QStyledItemDelegate):
    def __init__(self, images, revealed, parent=None):
        super().__init__(parent)
        self.images, self.revealed = images, revealed
        self.theme = get_theme("bub")
        self.font = QFont(QApplication.font())
        self.font.setPointSize(10)
        self.metric_cache = OrderedDict()

    def metrics(self, post, width):
        covered = bool(post.warning or post.sensitive) and post.uri not in self.revealed
        limit = 200 if post.media else 300
        text = ("CW · " + (post.warning or "Sensitive media") + "\nClick to reveal.") if covered else (post.text[:limit] + ("…" if len(post.text) > limit else ""))
        size = self.images.image_size(preview(post))
        viewport = self.parent().viewport() if isinstance(self.parent(), QListView) else None
        available_height = viewport.height() if viewport is not None else 700
        key = (post.uri, text, bool(post.media), covered, width, available_height,
               size.width(), size.height(), self.font.key())
        if key in self.metric_cache:
            self.metric_cache.move_to_end(key)
            return self.metric_cache[key]
        fm = QFontMetrics(self.font)
        content_width = max(1, width - 26)
        text_limit = fm.lineSpacing() * (2 if post.media else 4)
        height = min(text_limit, max(fm.lineSpacing(), fm.boundingRect(0, 0, content_width, 1000, Qt.TextFlag.TextWordWrap, text).height()))
        image_h = 0
        if post.media and not covered:
            # Reserve room for author, caption, and footer, so the entire card
            # fits the reading viewport even in a short half-screen window.
            budget = min(MAX_FEED_IMAGE_HEIGHT, max(1, available_height - 24 - 106 - height))
            natural = max(1, round(content_width * size.height() / size.width())) if size.isValid() else min(280, round(content_width * .6))
            image_h = min(budget, natural)
        result = text, height, image_h, 98 + height + (8 + image_h if image_h else 0)
        self.metric_cache[key] = result
        if len(self.metric_cache)>1024:
            self.metric_cache.popitem(last=False)
        return result

    def sizeHint(self, option, index):
        p = index.data(Qt.ItemDataRole.UserRole)
        width = option.widget.viewport().width() if option.widget else max(340, option.rect.width())
        return QSize(width, self.metrics(p, width)[3])

    def paint(self, painter, option, index):
        post = index.data(Qt.ItemDataRole.UserRole)
        if not post:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(1, 4, -1, -4)
        selected = bool(option.state & option.state.State_Selected)
        painter.setBrush(QColor(self.theme.selected if selected else self.theme.card))
        painter.setPen(QPen(QColor(self.theme.accent if selected else self.theme.line), 1))
        painter.drawRoundedRect(r, 8, 8)
        left, top = r.x() + 12, r.y() + 12
        painter.setBrush(QColor(self.theme.selected))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(left, top, 28, 28)
        f = QFont(self.font)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(self.theme.accent))
        painter.drawText(left, top, 28, 28, Qt.AlignmentFlag.AlignCenter, post.author[:1].upper())
        painter.setPen(QColor(self.theme.text))
        painter.drawText(left + 38, top - 2, r.width() - 112, 18, Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(f).elidedText(post.author, Qt.TextElideMode.ElideRight, r.width() - 120))
        f.setBold(False)
        f.setPointSize(8)
        painter.setFont(f)
        painter.setPen(QColor(self.theme.muted))
        painter.drawText(left + 38, top + 15, r.width() - 85, 16, Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(f).elidedText("@" + post.handle, Qt.TextElideMode.ElideRight, r.width() - 95))
        painter.drawText(r.right() - 66, top, 48, 20, Qt.AlignmentFlag.AlignRight, age(post.created))
        text, text_h, image_h, _ = self.metrics(post, option.rect.width())
        painter.setFont(self.font)
        covered = bool(post.warning or post.sensitive) and post.uri not in self.revealed
        painter.setPen(QColor(self.theme.warning if covered else self.theme.text))
        body_y = top + 38
        painter.setClipRect(left, body_y, r.width() - 24, text_h)
        painter.drawText(left, body_y, r.width() - 24, text_h + 30, Qt.TextFlag.TextWordWrap, text)
        painter.setClipping(False)
        if image_h:
            image_rect = QRect(left, body_y + text_h + 8, r.width() - 24, image_h)
            path = QPainterPath()
            path.addRoundedRect(image_rect, 7, 7)
            painter.setClipPath(path)
            painter.fillRect(image_rect, QColor(self.theme.canvas))
            ratio = self.parent().devicePixelRatioF() if self.parent() is not None else 1.0
            scaled = self.images.fitted(preview(post), image_rect.size(), ratio)
            if scaled is not None:
                painter.drawPixmap(round(image_rect.x() + (image_rect.width() - scaled.width()/ratio) / 2),
                                   round(image_rect.y() + (image_rect.height() - scaled.height()/ratio) / 2), scaled)
            else:
                painter.setPen(QColor(self.theme.muted))
                caption = "Image preview" if preview(post) else "Audio / video · open post to play"
                if preview(post) in self.images.failed:
                    caption = "Preview unavailable · open original"
                if not self.images.isolated:
                    caption = "Previews disabled · image isolation unavailable"
                painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, caption)
            painter.setClipping(False)
        painter.setFont(f)
        painter.setPen(QColor(self.theme.muted))
        footer = f"↩ {post.replies}    ↻ {post.boosts}    ♡ {post.likes}"
        if post.saved:
            footer += "    ★ Saved"
        painter.drawText(left, r.bottom() - 23, min(230, r.width()//2), 16, Qt.AlignmentFlag.AlignVCenter, footer)
        painter.setPen(QColor(self.theme.muted))
        server_width = max(80, r.width()//2 - 24)
        painter.drawText(r.right() - server_width - 12, r.bottom() - 23, server_width, 16, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(f).elidedText(post.server, Qt.TextElideMode.ElideLeft, server_width))
        painter.restore()


class Detail(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.post = None
        self.setMinimumWidth(270)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        self.heading = label("Take a closer look", "pageTitle", True)
        self.heading.setStyleSheet("font-size: 18px;")
        self.handle = label("Select a post to read it here.", "muted", True)
        self.meta = label("", "muted", True)
        self.warning = label("", "muted", True)
        self.reveal = button("Reveal this post", self.reveal_post)
        self.body = PostTextBrowser()
        self.body.setOpenLinks(False)
        self.body.setOpenExternalLinks(False)
        self.body.anchorClicked.connect(lambda u: self.window.open_url(u.toString()))
        self.body.setStyleSheet("QTextBrowser { font-size: 15px; line-height: 1.5; }")
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture.setMinimumHeight(0)
        self.picture.setMaximumHeight(160)
        self.picture.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.attachments = QComboBox()
        self.attachments.setToolTip("Select a media attachment")
        self.attachments.setMinimumContentsLength(10)
        self.attachments.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.attachments.currentIndexChanged.connect(self.update_image)
        self.media_open = button("↗", self.open_media)
        self.media_open.setToolTip("Open selected attachment")
        self.media_open.setFixedWidth(36)
        self.open = button("Original ↗", lambda: self.window.open_url(self.post.url) if self.post else None, "primary")
        row = QHBoxLayout()
        self.save = button("☆ Save", self.toggle_save)
        self.copy = button("Copy", lambda: QApplication.clipboard().setText(self.post.url) if self.post else None)
        self.copy.setToolTip("Copy post link")
        row.addWidget(self.open)
        row.addWidget(self.save)
        row.addWidget(self.copy)
        self.mute = button("Mute", lambda: self.window.mute_author(self.post) if self.post else None)
        self.mute.setToolTip("Mute this author")
        row.addWidget(self.mute)
        for w in (self.heading, self.handle, self.meta, self.warning, self.reveal):
            layout.addWidget(w)
        layout.addWidget(self.body, 1)
        layout.addWidget(self.picture)
        media_row = QHBoxLayout()
        media_row.addWidget(self.attachments, 1)
        media_row.addWidget(self.media_open)
        layout.addLayout(media_row)
        layout.addLayout(row)
        self.set_post(None)

    def set_post(self, post):
        self.post = post
        for w in (self.warning, self.reveal, self.picture, self.attachments, self.media_open, self.open, self.save, self.copy, self.mute):
            w.hide()
        self.picture.clear()
        if not post:
            self.heading.setText("Take a closer look")
            self.handle.setText("Select a post to read it here.")
            self.meta.clear()
            self.body.setHtml(f'<p style="color:{self.window.theme.muted}">A thousand little worlds.<br>One big rabbit hole.</p>')
            return
        self.heading.setText(post.author)
        self.handle.setText("@" + post.handle)
        date = datetime.fromtimestamp(post.created).astimezone().strftime("%b %d, %Y · %H:%M") if post.created else "Unknown post date"
        self.meta.setText(post.server + "\n" + date + (" · " + post.language if post.language else ""))
        covered = bool(post.warning or post.sensitive) and post.uri not in self.window.revealed
        self.warning.setText("CW · " + (post.warning or "Sensitive media"))
        self.warning.setVisible(bool(post.warning or post.sensitive))
        self.reveal.setVisible(covered)
        self.body.setHtml("" if covered else f'<div style="color:{self.window.theme.text}">' + rich_text(post.text) + "</div>")
        self.attachments.blockSignals(True)
        self.attachments.clear()
        if not covered:
            for i, m in enumerate(post.media):
                self.attachments.addItem(f"{i+1}/{len(post.media)} · {m.get('type','media')}" + (" · " + m["alt"][:60] if m.get("alt") else ""), m)
        self.attachments.blockSignals(False)
        self.attachments.setVisible(bool(post.media) and not covered)
        self.media_open.setVisible(bool(post.media) and not covered)
        self.update_image()
        for w in (self.open, self.save, self.copy, self.mute):
            w.show()
        self.save.setText("★ Saved" if post.saved else "☆ Save")

    def reveal_post(self):
        if self.post:
            self.window.revealed.add(self.post.uri)
            self.set_post(self.post)
            self.window.relayout_feed()
            self.window.feed.viewport().update()
            self.window.schedule_images()

    def image_url(self):
        m = self.attachments.currentData() or {}
        return m.get("preview") or (m.get("url") if str(m.get("type", "")).startswith("image") else "")

    def update_image(self, *_):
        url = self.image_url()
        pix = self.window.images.get(url)
        self.picture.setVisible(bool(url))
        if pix:
            self.picture.setPixmap(pix.scaled(max(100, self.width()-20), min(160, max(70, self.window.height()//5)), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        elif url:
            self.picture.setText("Loading preview…" if url not in self.window.images.failed else "Preview unavailable")
            self.window.images.ensure(url)
        else:
            self.picture.clear()

    def open_media(self):
        self.window.open_url((self.attachments.currentData() or {}).get("url", ""))

    def toggle_save(self):
        if self.post:
            self.window.save_post(self.post)


class Window(QMainWindow):
    def __init__(self, store, data_dir, demo=False):
        super().__init__()
        self.store, self.data_dir, self.demo = store, Path(data_dir), demo
        self.theme = get_theme(store.setting("theme", "bub"))
        self.setWindowTitle(f"Activity Bub · {__version__}")
        available = QApplication.primaryScreen().availableGeometry()
        self.resize(min(800, available.width()), min(820, max(440, available.height()-50)))
        self.setMinimumSize(460, 400)
        icon_path = Path(__file__).parent / "assets" / "activity-bub.png"
        self.setWindowIcon(QIcon(str(icon_path)))
        self.revealed = set()
        self.pending = 0
        self.latest = 0
        self.before = None
        self.exhausted = False
        self.loading = False
        self.relayouting = False
        self.mode = "feed"
        self.selected_server = ""
        self.logs = deque(maxlen=80)
        self.closing = False
        self._scroll_direction = 1
        self._last_scroll_value = 0
        self.worker = CrawlerThread(store)
        self.worker.event.connect(self.on_event)
        self.images = Images(self.worker.engine.stop, self, network=self.worker.engine.network)
        self.images.pool.setMaxThreadCount(self.worker.engine.profile.images)
        self.images.blocked = lambda: self.demo or self.closing or self.worker.engine.paused.is_set()
        self.images.changed.connect(self.image_changed)
        self.image_timer = QTimer(self)
        self.image_timer.setSingleShot(True)
        self.image_timer.setInterval(35)
        self.image_timer.timeout.connect(self.visible_images)
        self.layout_timer = QTimer(self)
        self.layout_timer.setSingleShot(True)
        self.layout_timer.setInterval(0)
        self.layout_timer.timeout.connect(self.relayout_feed)
        self.build()
        self.load_preferences()
        self.apply_theme(self.theme.key, persist=False)
        self.refresh_feed()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(2000)
        self._heartbeat_at=time.monotonic()
        self.heartbeat=QTimer(self)
        self.heartbeat.timeout.connect(self.ui_heartbeat)
        self.heartbeat.start(250)
        if not demo:
            self.worker.start()
        self.tick()

    def build(self):
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(12, 8, 12, 6)
        outer.setSpacing(0)
        self.column = QWidget()
        self.column.setMaximumWidth(MAX_FEED_WIDTH)
        self.column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        area = QVBoxLayout(self.column)
        area.setContentsMargins(0, 0, 0, 0)
        area.setSpacing(6)
        outer.addStretch(1)
        outer.addWidget(self.column, 100)
        outer.addStretch(1)

        top = QHBoxLayout()
        top.setSpacing(6)
        brand = label("activity bub")
        brand.setStyleSheet("font-size: 16px; font-weight: 700;")
        icon = label("")
        icon.setPixmap(self.windowIcon().pixmap(24,24))
        top.addWidget(icon)
        top.addWidget(brand)
        top.addStretch(1)
        self.title = button("Feed")
        navigation = QMenu(self.title)
        self.nav_feed = navigation.addAction("Feed", lambda: self.navigate("feed"))
        self.nav_saved = navigation.addAction("Saved posts", lambda: self.navigate("saved"))
        self.nav_servers = navigation.addAction("Server radar", lambda: self.navigate("servers"))
        for action in (self.nav_feed, self.nav_saved, self.nav_servers):
            action.setCheckable(True)
        self.nav_feed.setChecked(True)
        self.title.setMenu(navigation)
        top.addWidget(self.title)
        self.new_posts = button("↑", self.refresh_feed)
        self.new_posts.setFixedWidth(62)
        self.new_posts.setToolTip("Jump to newest posts (R)")
        top.addWidget(self.new_posts)
        self.pause = button("Pause", self.toggle_pause)
        self.pause.setToolTip("Pause new requests; cached posts remain readable (Space)")
        top.addWidget(self.pause)
        menu_button = button("⋯")
        menu_button.setToolTip("Servers and preferences")
        options = QMenu(menu_button)
        self.discover = options.addAction("Discover servers")
        self.discover.setCheckable(True)
        self.discover.setChecked(True)
        self.discover.triggered.connect(self.toggle_discovery)
        options.addSeparator()
        power_menu=options.addMenu("Engine power")
        self.power_group=QActionGroup(self)
        self.power_group.setExclusive(True)
        self.power_actions={}
        for profile in PROFILES:
            action=power_menu.addAction(f"{profile.label} · {profile.total} worker budget")
            action.setCheckable(True)
            action.setChecked(profile.key==self.worker.engine.profile.key)
            self.power_group.addAction(action)
            action.triggered.connect(lambda checked=False,key=profile.key:self.set_power(key))
            self.power_actions[profile.key]=action
        self.network_scope=options.addAction("Include federated public posts")
        self.network_scope.setCheckable(True)
        self.network_scope.setChecked(self.worker.engine.scope=="network")
        self.network_scope.triggered.connect(lambda enabled:self.worker.engine.set_scope("network" if enabled else "local"))
        options.addAction("Engine room…",self.engine_room)
        options.addSeparator()
        theme_menu = options.addMenu("Color theme")
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_actions = {}
        for theme in THEMES:
            action = theme_menu.addAction(theme.name)
            action.setCheckable(True)
            self.theme_group.addAction(action)
            action.triggered.connect(lambda checked=False, key=theme.key: self.apply_theme(key))
            self.theme_actions[theme.key] = action
        self.passport_action = options.addAction("Explorer passport…", self.show_passport)
        options.addAction("Surprise me · Ctrl+J", self.surprise)
        options.addSeparator()
        options.addAction("Add servers…", self.add_servers)
        options.addAction("Preferences…", self.preferences)
        menu_button.setMenu(options)
        top.addWidget(menu_button)
        area.addLayout(top)
        self.subtitle = label("OFFLINE DEMO · Synthetic sample posts", "muted", True)
        self.subtitle.setVisible(self.demo)
        area.addWidget(self.subtitle)

        self.pages = QStackedWidget()
        area.addWidget(self.pages, 1)
        feed_page = QWidget()
        feed_layout = QVBoxLayout(feed_page)
        feed_layout.setContentsMargins(0, 0, 0, 0)
        feed_layout.setSpacing(6)
        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search posts or @people…")
        self.search.setMinimumWidth(80)
        self.search_clear = add_clear_action(self.search)
        self.search.setToolTip("Search downloaded posts")
        search_row.addWidget(self.search, 1)
        self.filter_button = button("Filters", self.toggle_filters)
        self.filter_button.setCheckable(True)
        search_row.addWidget(self.filter_button)
        feed_layout.addLayout(search_row)
        self.filter_panel = QWidget()
        filters = QHBoxLayout(self.filter_panel)
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(10)
        self.language = QComboBox()
        for caption, code in (("All languages", ""), ("English", "en"), ("日本語", "ja"), ("Deutsch", "de"), ("Español", "es"), ("Français", "fr"), ("Português", "pt"), ("Unknown language", "__unknown__")):
            self.language.addItem(caption, code)
        self.media = QCheckBox("Media")
        self.hide_cw = QCheckBox("Hide CW")
        filters.addWidget(self.language, 1)
        filters.addWidget(self.media)
        filters.addWidget(self.hide_cw)
        self.filter_panel.hide()
        feed_layout.addWidget(self.filter_panel)
        self.empty = label("Looking for a signal…\nChecking servers for public posts.", "muted", True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setMinimumHeight(100)
        feed_layout.addWidget(self.empty)
        self.feed = SmoothFeedView()
        self.feed.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.feed.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.feed.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.feed.setResizeMode(QListView.ResizeMode.Adjust)
        self.feed.setLayoutMode(QListView.LayoutMode.SinglePass)
        self.feed.setWordWrap(True)
        self.model = FeedModel(self)
        self.feed.setModel(self.model)
        self.delegate = Cards(self.images, self.revealed, self.feed)
        self.feed.setItemDelegate(self.delegate)
        self.feed.clicked.connect(self.open_reader)
        self.feed.activated.connect(self.open_reader)
        self.feed.selectionModel().currentChanged.connect(lambda ix, _: self.read_post(ix))
        self.feed.verticalScrollBar().valueChanged.connect(self.scrolled)
        self.feed.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        for caption, fn in (("Read post", self.open_reader), ("Open original", self.open_selected),
                            ("Save / unsave", self.save_selected),
                            ("Mute author", lambda: self.mute_author(self.current_post())),
                            ("Mute server", self.mute_selected_server)):
            action = QAction(caption, self.feed)
            action.triggered.connect(fn)
            self.feed.addAction(action)
        feed_layout.addWidget(self.feed, 1)
        footer = QHBoxLayout()
        self.history_note = label("Scroll for more", "muted", True)
        self.history_note.setStyleSheet("font-size: 11px;")
        footer.addWidget(self.history_note, 1)
        footer.addWidget(button("More ↓", self.load_more))
        feed_layout.addLayout(footer)
        self.pages.addWidget(feed_page)

        self.reader_dialog = QDialog(self)
        self.reader_dialog.setWindowTitle("Activity Bub · Read post")
        self.reader_dialog.setMinimumSize(330, 360)
        reader_layout = QVBoxLayout(self.reader_dialog)
        reader_layout.setContentsMargins(6, 6, 6, 6)
        self.detail = Detail(self)
        reader_layout.addWidget(self.detail, 1)
        close_reader = button("Back to feed · Esc", self.reader_dialog.reject)
        reader_layout.addWidget(close_reader)
        self.build_radar()
        self.status = label("Connecting…", "muted")
        self.status.setStyleSheet("font-size: 10px;")
        area.addWidget(self.status)
        self.engine_meter=button("MAX · starting…",self.engine_room)
        self.engine_meter.setMinimumWidth(1)
        self.engine_meter.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        area.addWidget(self.engine_meter)
        self.logline = label("Starting the hunt…", "muted")
        self.logline.hide()
        self.setCentralWidget(root)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(350)
        self.debounce.timeout.connect(self.filters_changed)
        self.search.textChanged.connect(lambda: self.debounce.start())
        self.language.currentIndexChanged.connect(self.filters_changed)
        self.media.toggled.connect(self.filters_changed)
        self.hide_cw.toggled.connect(self.filters_changed)
        for key, fn in (("Ctrl+F", self.search.setFocus), ("R", self.refresh_feed),
                        ("B", self.save_selected), ("Ctrl+O", self.open_selected), ("Ctrl+J", self.surprise),
                        ("Space", self.toggle_pause)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda fn=fn, key=key: fn() if key == "Ctrl+F" or not isinstance(QApplication.focusWidget(), QLineEdit) else None)

    def toggle_filters(self):
        self.filter_panel.setVisible(self.filter_button.isChecked())
        QTimer.singleShot(0, self.relayout_feed)

    def build_radar(self):
        radar = QWidget()
        layout = QVBoxLayout(radar)
        layout.setContentsMargins(0, 8, 0, 0)
        bar = QHBoxLayout()
        self.server_search = QLineEdit()
        self.server_search.setPlaceholderText("Find a server…")
        self.server_search.textChanged.connect(self.refresh_radar)
        bar.addWidget(self.server_search, 1)
        bar.addWidget(button("Import…", self.import_servers))
        bar.addWidget(button("Export all…", self.export_servers))
        layout.addLayout(bar)
        self.server_summary = label("", "muted", True)
        layout.addWidget(self.server_summary)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Server", "Software", "Status", "Posts found", "Last result"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 95)
        self.table.itemDoubleClicked.connect(lambda _: self.browse_server())
        self.table.itemSelectionChanged.connect(self.server_selection)
        layout.addWidget(self.table, 1)
        self.server_info = label("Select a server to inspect its discovery source and last result.", "muted", True)
        self.server_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.server_info)
        actions = QHBoxLayout()
        actions.addWidget(button("Read posts", self.browse_server))
        actions.addStretch(1)
        server_actions = button("Actions")
        server_menu = QMenu(server_actions)
        for text, fn in (("Pin / unpin", self.pin_server),
                         ("Mute / unmute", self.mute_server), ("Retry", self.retry_server), ("Open website ↗", self.open_server)):
            server_menu.addAction(text, fn)
        server_actions.setMenu(server_menu)
        actions.addWidget(server_actions)
        layout.addLayout(actions)
        self.pages.addWidget(radar)

    def load_preferences(self):
        self.media.setChecked(self.store.setting("media_only", False))
        self.hide_cw.setChecked(self.store.setting("hide_cw", False))
        code = self.store.setting("language", "")
        self.language.setCurrentIndex(max(0, self.language.findData(code)))
        if not self.store.setting("discover", True):
            self.discover.setChecked(False)
            self.toggle_discovery()

    def filters(self):
        language = self.language.currentData() or ""
        return dict(query=self.search.text().strip(), language=language,
                    media_only=self.media.isChecked(), hide_sensitive=self.hide_cw.isChecked(),
                    saved_only=self.mode == "saved", server=self.selected_server,
                    muted_words=self.store.setting("muted_words", ""))

    def filters_changed(self, *_):
        self.store.set_setting("media_only", self.media.isChecked())
        self.store.set_setting("hide_cw", self.hide_cw.isChecked())
        self.store.set_setting("language", self.language.currentData() or "")
        count = int(self.media.isChecked()) + int(self.hide_cw.isChecked()) + int(bool(self.language.currentData()))
        self.filter_button.setText("Filters" + (f" · {count}" if count else ""))
        self.refresh_feed()

    def refresh_feed(self):
        self.feed.stop_scrolling()
        self._scroll_direction = 1
        self._last_scroll_value = 0
        if self.reader_dialog.isVisible():
            self.reader_dialog.reject()
        self.pending = 0
        self.worker.engine.history.clear()
        self.latest = self.store.stats()["latest"]
        self.exhausted = False
        self.detail.set_post(None)
        self.store.reset_reader()
        self.model.reset(self.store.page(**self.filters()))
        self.store.queue_reader(self.model.posts)
        self.before = (self.model.posts[-1].created, self.model.posts[-1].seq) if self.model.posts else None
        self.feed.scrollToTop()
        self.empty.setVisible(not self.model.posts)
        self.new_posts.setText("↑")
        self.new_posts.setToolTip("Jump to newest posts (R)")
        self.history_note.setText("Scroll for more")
        self.update_empty()
        self.schedule_images()

    def load_more(self):
        if self.loading or self.mode == "servers":
            return
        self.loading = True
        try:
            first_visible = self.feed.indexAt(self.feed.viewport().rect().topLeft())
            if len(self.model.posts) >= 400 and (not first_visible.isValid() or first_visible.row() < 80):
                self.history_note.setText("Scroll further down to continue loading")
                return
            posts = self.store.page(before=self.before, unseen_only=True, **self.filters()) if self.before else []
            if not posts:
                posts=self.store.page(unseen_only=True,**self.filters())
            self.store.queue_reader(posts)
            with self.feed.preserve_scroll_motion():
                self.model.append(posts)
                if posts:
                    self.worker.engine.history.clear()
                    self.before = (posts[-1].created, posts[-1].seq)
                    # Keep a rolling window while preserving the visible reading position.
                    if len(self.model.posts) > 400:
                        current = self.feed.indexAt(self.feed.viewport().rect().topLeft())
                        if current.isValid() and current.row() >= 80:
                            offset = self.feed.visualRect(current).top()
                            row = current.row() - 80
                            self.relayouting = True
                            try:
                                self.model.trim_front(80)
                                keep_revealed = {p.uri for p in self.model.posts}
                                if self.detail.post:
                                    keep_revealed.add(self.detail.post.uri)
                                self.revealed.intersection_update(keep_revealed)
                                self.feed.doItemsLayout()
                                ix = self.model.index(row)
                                self.feed.scrollTo(ix, QAbstractItemView.ScrollHint.PositionAtTop)
                                sb = self.feed.verticalScrollBar()
                                sb.setValue(sb.value() - offset)
                            finally:
                                self.relayouting = False
                                self._last_scroll_value = self.feed.verticalScrollBar().value()
            if posts:
                self.history_note.setText("Reading cached history · R jumps to the latest")
            elif self.mode != "saved" and not self.demo and not self.worker.engine.paused.is_set():
                self.worker.engine.history.set()
                self.history_note.setText("Reaching further back across readable servers…")
            else:
                self.history_note.setText("No more matching cached posts yet")
            self.schedule_images()
        finally:
            self.loading = False

    def update_empty(self):
        if self.model.posts:
            return
        s = self.store.stats()
        if self.mode == "saved":
            self.empty.setText("Your good finds live here.\nSelect a post and press Save or B.")
        elif any((self.search.text(), self.media.isChecked(), self.hide_cw.isChecked(), self.language.currentData(), self.selected_server)):
            self.empty.setText("No cached posts match these filters yet.\nTry another filter while the network keeps exploring.")
        elif s["posts"]:
            self.empty.setText("No posts to show with your current mutes and preferences.")
        elif s["queued"]:
            self.empty.setText("Looking for a signal…\nChecking servers for readable public feeds.")
        else:
            self.empty.setText("No readable posts yet.\nOpen Server radar for results, or add more server seeds.")

    def scrolled(self, value):
        if self.relayouting:
            return
        if value != self._last_scroll_value:
            self._scroll_direction = 1 if value > self._last_scroll_value else -1
        self._last_scroll_value = value
        self.schedule_images()
        sb = self.feed.verticalScrollBar()
        if value > 0 and value >= sb.maximum() - 500:
            QTimer.singleShot(0, self.load_more)
        elif value < sb.maximum() - 1000:
            self.worker.engine.history.clear()

    def schedule_images(self):
        if not self.closing and not self.image_timer.isActive():
            self.image_timer.start()

    def visible_images(self):
        if self.demo or self.closing or self.worker.engine.paused.is_set() or self.mode == "servers":
            return
        viewport = self.feed.viewport()
        height = viewport.height()
        start = self.feed.indexAt(QPoint(viewport.width()//2, 0)).row()
        start = max(0, start)
        # Geometric lookahead scales with window/card size, rather than fetching
        # a fixed six posts. Warm two viewports ahead and one behind the reader.
        ahead, behind = 2*height, height
        low, high = (-behind, height+ahead) if self._scroll_direction>0 else (-ahead, height+behind)
        candidates = []
        for row in range(start, len(self.model.posts)):
            rect = self.feed.visualRect(self.model.index(row))
            if rect.top()>high:
                break
            candidates.append((row, rect))
        for row in range(start-1, -1, -1):
            rect = self.feed.visualRect(self.model.index(row))
            if rect.bottom()<low:
                break
            candidates.append((row, rect))
        def priority(item):
            _, rect = item
            if rect.bottom()>=0 and rect.top()<height:
                return 0, rect.top()
            forward = rect.top()>=height if self._scroll_direction>0 else rect.bottom()<0
            return (1 if forward else 2), min(abs(rect.bottom()), abs(rect.top()-height))
        candidates.sort(key=priority)
        urls = []
        for row, _ in candidates:
            p = self.model.posts[row]
            if (not p.sensitive and not p.warning) or p.uri in self.revealed:
                urls.append(preview(p))
        if self.detail.post and (not self.detail.post.sensitive and not self.detail.post.warning or self.detail.post.uri in self.revealed):
            if self.reader_dialog.isVisible():
                urls.insert(0, self.detail.image_url())
        self.images.prioritize(urls)

    def image_changed(self, url):
        if any(preview(p) == url for p in self.model.posts):
            if not self.layout_timer.isActive():
                self.layout_timer.start()
        self.feed.viewport().update()
        if self.detail.image_url() == url:
            self.detail.update_image()
        self.schedule_images()

    def relayout_feed(self):
        if self.closing or self.relayouting:
            return
        self.layout_timer.stop()
        anchor = self.feed.indexAt(self.feed.viewport().rect().topLeft())
        offset = self.feed.visualRect(anchor).top() if anchor.isValid() else 0
        self.relayouting = True
        try:
            with self.feed.preserve_scroll_motion():
                self.feed.doItemsLayout()
                if anchor.isValid():
                    self.feed.scrollTo(anchor, QAbstractItemView.ScrollHint.PositionAtTop)
                    bar = self.feed.verticalScrollBar()
                    bar.setValue(bar.value() - offset)
        finally:
            self.relayouting = False
            self._last_scroll_value = self.feed.verticalScrollBar().value()

    def current_post(self):
        return self.feed.currentIndex().data(Qt.ItemDataRole.UserRole)

    def read_post(self, ix):
        if ix.isValid():
            self.detail.set_post(ix.data(Qt.ItemDataRole.UserRole))

    def open_reader(self, ix=None):
        if not isinstance(ix, QModelIndex):
            ix = self.feed.currentIndex()
        if ix.isValid():
            self.open_post(ix.data(Qt.ItemDataRole.UserRole))

    def open_post(self, post):
        if self.store.setting("rewards", True):
            self.store.stamp_world(post.server)
        self.detail.set_post(post)
        self.reader_dialog.resize(min(560, self.width()-24), min(720, self.height()-24))
        self.reader_dialog.open()
        QTimer.singleShot(0, self.detail.update_image)
        self.tick()

    def surprise(self):
        posts = self.store.page(limit=1, random_order=True, **self.filters())
        if posts:
            self.open_post(posts[0])
        else:
            self.history_note.setText("No matching cached posts to explore yet")

    def apply_theme(self, key, persist=True):
        self.theme = get_theme(key)
        QApplication.instance().setStyleSheet(stylesheet(self.theme))
        self.delegate.theme = self.theme
        self.search_clear.setIcon(clear_icon(self.theme.muted))
        self.engine_meter.setStyleSheet(f"QPushButton {{ border:0; padding:1px 0; background:transparent; color:{self.theme.muted}; font-size:10px; text-align:left; }} QPushButton:hover {{ color:{self.theme.accent}; }}")
        for name, action in self.theme_actions.items():
            action.setChecked(name == self.theme.key)
        self.detail.body.document().setDefaultStyleSheet(f"a {{ color: {self.theme.accent}; }}")
        self.detail.set_post(self.detail.post)
        if persist:
            self.store.set_setting("theme", self.theme.key)
            if self.store.setting("rewards", True):
                tried=set(self.store.setting("tried_themes", ["bub"]))
                tried.add(self.theme.key)
                self.store.set_setting("tried_themes", sorted(tried))
        self.feed.viewport().update()
        if self.mode == "servers":
            self.refresh_radar()

    def show_passport(self):
        progress = self.store.passport()
        dialog = QDialog(self)
        dialog.setWindowTitle("Activity Bub · Explorer passport")
        dialog.resize(min(500, self.width()-24), min(600, self.height()-24))
        layout = QVBoxLayout(dialog)
        title = label(f"Lv {progress['level']} · {progress['rank']}", "pageTitle", True)
        layout.addWidget(title)
        layout.addWidget(label(f"{progress['xp']:,} XP · {progress['worlds']:,} worlds visited · {progress['keeps']:,} unique saves", "muted", True))
        bar = QProgressBar()
        if progress["next"]:
            bar.setRange(0, progress["next"]-progress["start"])
            bar.setValue(progress["xp"]-progress["start"])
            bar.setFormat(f"Next rank at {progress['next']:,} XP")
        else:
            bar.setRange(0,1); bar.setValue(1); bar.setFormat("Map your own frontier")
        layout.addWidget(bar)
        badges=QListWidget()
        for badge in progress["badges"]:
            mark="★" if badge["earned"] else "☆"
            badges.addItem(f"{mark}  {badge['name']}   {min(badge['count'],badge['target'])}/{badge['target']}\n     {badge['description']}")
        layout.addWidget(badges,1)
        layout.addWidget(label("25 XP per new server you read. 10 XP per first-time save. Rewards stay on this computer.", "muted", True))
        enabled=QCheckBox("Enable exploration rewards")
        enabled.setChecked(self.store.setting("rewards", True))
        enabled.toggled.connect(lambda value:self.store.set_setting("rewards", value))
        layout.addWidget(enabled)
        layout.addWidget(button("Back to the feed", dialog.accept))
        dialog.exec()

    def open_url(self, url):
        safe = web_url(url)
        target=QUrl(safe,QUrl.ParsingMode.StrictMode)
        if safe and target.isValid() and target.scheme()=="https" and not target.userInfo():
            QDesktopServices.openUrl(target)

    def open_selected(self):
        p = self.current_post()
        if p:
            self.open_url(p.url)

    def save_post(self, post):
        post.saved = not post.saved
        self.store.save_post(post.seq, post.saved)
        for listed in self.model.posts:
            if listed.seq == post.seq:
                listed.saved = post.saved
        if post.saved and self.store.setting("rewards", True):
            self.store.stamp_keep(post.uri)
        self.feed.viewport().update()
        if self.detail.post and self.detail.post.seq == post.seq:
            self.detail.save.setText("★ Saved" if post.saved else "☆ Save")
        self.tick()

    def save_selected(self):
        p = self.current_post()
        if p:
            self.save_post(p)

    def mute_author(self, post):
        if post:
            self.store.mute_author(post.handle)
            self.refresh_feed()
            self.logline.setText("Muted @" + post.handle + " · Undo in Preferences")

    def mute_selected_server(self):
        p = self.current_post()
        if p:
            self.store.update_server(p.server, muted=1)
            self.refresh_feed()

    def navigate(self, mode):
        self.mode = mode
        self.selected_server = ""
        for w, m in ((self.nav_feed, "feed"), (self.nav_servers, "servers"), (self.nav_saved, "saved")):
            w.setChecked(mode == m)
        self.title.setText({"feed": "Feed", "servers": "Servers", "saved": "Saved"}[mode])
        self.subtitle.setVisible(self.demo)
        self.subtitle.setText("OFFLINE DEMO · Synthetic sample posts" if self.demo else "")
        self.pages.setCurrentIndex(1 if mode == "servers" else 0)
        if mode == "servers":
            self.refresh_radar()
        else:
            self.refresh_feed()

    def toggle_pause(self):
        if self.worker.engine.paused.is_set():
            self.worker.engine.paused.clear()
            self.pause.setText("Pause")
            self.schedule_images()
        else:
            self.worker.engine.paused.set()
            self.pause.setText("Resume")
        self.tick()

    def toggle_discovery(self):
        enabled = self.discover.isChecked()
        self.worker.engine.discovery.set() if enabled else self.worker.engine.discovery.clear()
        self.discover.setText("Discover servers")
        self.store.set_setting("discover", enabled)

    def on_event(self, kind, value):
        if self.closing:
            return
        if kind in ("posts", "history"):
            self.pending += value
            if not self.model.posts and self.mode != "servers":
                self.refresh_feed()
            else:
                if self.pending:
                    amount = str(self.pending) if self.pending < 1000 else f"{self.pending/1000:.1f}k"
                    self.new_posts.setText(f"+{amount} ↑")
                    self.new_posts.setToolTip(f"{self.pending:,} new finds · Refresh feed")
                sb = self.feed.verticalScrollBar()
                if (sb.value() > 0 or kind == "history" and sb.maximum() == 0) and sb.value() >= sb.maximum() - 500 and self.mode != "servers":
                    self.load_more()
        elif kind == "fatal":
            self.worker.engine.paused.set()
            self.pause.setText("Crawler stopped")
            self.pause.setEnabled(False)
            self.logs.append("Crawler stopped: " + str(value))
            self.logline.setText(self.logs[-1])
            plain_warning(self, "Crawler stopped", str(value) + "\nYour cache is still available. Restart Activity Bub to retry.")
        else:
            message = str(value)
            self.logs.append(message)
            self.logline.setText(message)
            self.logline.setToolTip(safe_tooltip("\n".join(list(self.logs)[-12:])))

    def tick(self):
        s = self.store.stats()
        self.nav_saved.setText(f"Saved posts ({s['saved']:,})")
        progress=self.store.passport()
        self.passport_action.setText(f"Passport · Lv {progress['level']} {progress['rank']}")
        state = "DEMO" if self.demo else "Paused" if self.worker.engine.paused.is_set() else "Exploring" if self.discover.isChecked() else "Reading"
        summary = f"{state} · {s['servers']:,} servers · {s['ready']:,} feeds · {s['posts']:,} posts"
        self.status.setText(QFontMetrics(self.status.font()).elidedText(summary, Qt.TextElideMode.ElideRight, max(100, self.column.width())))
        self.status.setToolTip(safe_tooltip(summary + f"\n{progress['rank']} · {progress['xp']:,} XP" + "\n" + "\n".join(list(self.logs)[-8:])))
        if self.mode == "servers":
            self.refresh_radar()
        self.update_empty()
        self.update_engine_meter()

    def ui_heartbeat(self):
        now=time.monotonic()
        self.worker.engine.note_ui_lag(max(0,(now-self._heartbeat_at-.25)*1000))
        self._heartbeat_at=now

    def set_power(self,key):
        self.worker.engine.set_profile(key)
        self.images.pool.setMaxThreadCount(get_profile(key).images)
        for name,action in self.power_actions.items():action.setChecked(name==key)
        self.update_engine_meter()

    def update_engine_meter(self):
        s=self.worker.engine.snapshot()
        jobs=s["jobs"];limits=s["limits"]
        rate=s["bps"]/(1024*1024) if s["bps"]>=1024*1024 else s["bps"]/1024
        unit="M/s" if s["bps"]>=1024*1024 else "K/s"
        text=f"{s['profile'].upper()}  D{jobs['discovery']} F{jobs['feed']} H{jobs['history']}/{sum(limits.values())}  · {s['rps']:.1f} req/s  ↓{rate:.0f}{unit}  +{s['pps']:.0f}/s"
        self.engine_meter.setText(text)
        resource=(f"CPU {s['cpu']:.0f}% · RAM {s['rss']/1024**2:.0f} MiB · {s['threads']} app threads" if s["resources_available"] else "Process CPU/RAM/thread counters unavailable on this system")
        detail=(f"Discovery {jobs['discovery']}/{limits['discovery']} · Live {jobs['feed']}/{limits['feed']} · History {jobs['history']}/{limits['history']}\\n"
                f"{s['http_active']} HTTP requests active · {s['latency_ms']:.0f} ms mean request latency\\n"
                f"{resource}\\n"
                f"{s['unread']:,} queued-cache posts · target {s['buffer_target']:,}\\n"
                f"{s['requests']:,} requests · {s['bytes']/1024**2:.1f} MiB received · {s['errors']:,} errors · {s['limited']:,} cooldown responses\\n"
                f"Adaptive: {s['adaptive']} · Click for engine controls")
        self.engine_meter.setToolTip(detail.replace("\\n","\n"))

    def engine_room(self):
        dialog=QDialog(self)
        dialog.setWindowTitle("Activity Bub · Engine room")
        dialog.resize(min(520,self.width()-24),min(560,self.height()-24))
        layout=QVBoxLayout(dialog)
        layout.addWidget(label("Engine room","pageTitle"))
        choice=QComboBox()
        for profile in PROFILES:choice.addItem(f"{profile.label} · {profile.total} crawler workers",profile.key)
        choice.setCurrentIndex(max(0,choice.findData(self.worker.engine.profile.key)))
        choice.currentIndexChanged.connect(lambda:self.set_power(choice.currentData()))
        layout.addWidget(choice)
        output=QTextBrowser()
        layout.addWidget(output,1)
        def refresh():
            self.update_engine_meter()
            output.setPlainText(self.engine_meter.toolTip()+"\n\nD = discovery · F = live feeds · H = older pages\n\nPools have separate budgets. The controller reduces concurrency under CPU/RAM pressure or UI lag. Requests to the same host stay spaced; restricted feeds and cooldowns are respected.\n\nFederated mix uses public network timelines when available, with local-public fallback. It cannot expose private posts or a complete universal firehose.")
        refresh()
        timer=QTimer(dialog);timer.timeout.connect(refresh);timer.start(1000)
        layout.addWidget(button("Back to the feed",dialog.accept))
        dialog.exec()
        timer.stop()

    def selected_host(self):
        rows = self.table.selectionModel().selectedRows()
        return self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole) if rows else ""

    def refresh_radar(self, *_):
        host = self.selected_host()
        scroll = self.table.verticalScrollBar().value()
        servers = self.store.servers(self.server_search.text().strip())
        self.table.blockSignals(True)
        self.table.setRowCount(len(servers))
        selected = -1
        names = {"queued": "Waiting", "ready": "Readable", "locked": "Login / restricted", "unsupported": "No supported feed", "error": "Retry later"}
        for i, s in enumerate(servers):
            status = "Muted" if s["muted"] else names.get(s["state"], s["state"])
            if s["cooldown"] > time.time() and not s["muted"]:
                status = "Cooling down"
            for j, value in enumerate((s["host"] + ("  ★" if s["pinned"] else ""), s["software"] or "—", status, str(s["post_count"]), s["error"] or ("Checked " + age(s["checked"]) + " ago" if s["checked"] else "Not checked yet"))):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if j == 0:
                    item.setData(Qt.ItemDataRole.UserRole, s["host"])
                if j == 2:
                    item.setForeground(QColor(self.theme.accent if s["state"] == "ready" and not s["muted"] else self.theme.muted))
                self.table.setItem(i, j, item)
            if s["host"] == host:
                selected = i
        if selected >= 0:
            self.table.selectRow(selected)
        self.table.verticalScrollBar().setValue(scroll)
        self.table.blockSignals(False)
        s = self.store.stats()
        self.server_summary.setText(f"{s['servers']:,} discovered · {s['ready']:,} readable · {s['queued']:,} waiting · {s['locked']:,} restricted · {s['muted']:,} muted\nShowing up to 1,000 matches. Search narrows the full registry; export includes every server.")

    def server_selection(self):
        s = self.store.server(self.selected_host())
        if s:
            self.server_info.setText(f"{s['host']} · Discovered via {s['source']}\n" + (s["error"] or "No recorded error"))

    def browse_server(self):
        host = self.selected_host()
        if host:
            self.navigate("feed")
            self.selected_server = host
            self.subtitle.setText("Cached public posts from " + host)
            self.subtitle.show()
            self.refresh_feed()

    def pin_server(self):
        s = self.store.server(self.selected_host())
        if s:
            self.store.update_server(s["host"], pinned=0 if s["pinned"] else 1)
            self.refresh_radar()

    def mute_server(self):
        s = self.store.server(self.selected_host())
        if s:
            self.store.update_server(s["host"], muted=0 if s["muted"] else 1)
            self.refresh_radar()

    def retry_server(self):
        s = self.store.server(self.selected_host())
        if s:
            if s["cooldown"] > time.time():
                self.server_info.setText("This server requested a cooldown. It will be eligible to retry after " + datetime.fromtimestamp(s["cooldown"]).astimezone().strftime("%H:%M %Z"))
                return
            self.store.update_server(s["host"], state="queued", next_probe=0, next_feed=0, next_peers=0, failures=0, error="")
            self.refresh_radar()

    def open_server(self):
        host = self.selected_host()
        if host:
            self.open_url("https://" + host)

    def add_servers(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Add server seeds")
        dialog.setMinimumWidth(440)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label("Enter domains separated by commas or spaces.\nActivity Bub explores their public feeds and peer lists.", "muted", True))
        edit = QLineEdit()
        edit.setPlaceholderText("opalstack.social, mastodon.social")
        layout.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            hosts, invalid = [], []
            for value in re.split(r"[\s,;]+", edit.text().strip()):
                if not value:
                    continue
                try:
                    hosts.append(domain(value))
                except (ValueError, UnicodeError):
                    invalid.append(value)
            count = self.store.add_servers(hosts)
            self.logline.setText(f"Added {count} new servers" + (f" · skipped {len(invalid)} invalid entries" if invalid else ""))
            self.tick()

    def import_servers(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import servers", "", "Server lists (*.txt *.csv);;All files (*)")
        if not path:
            return
        try:
            if Path(path).stat().st_size > 16 * 1024 * 1024:
                raise ValueError("Import files must be smaller than 16 MB")
            with open(path, encoding="utf-8-sig", newline="") as f:
                hosts = [row[0].strip() for row in csv.reader(f) if row and not row[0].lstrip().startswith("#")]
            count = self.store.add_servers(hosts, "import")
            self.logline.setText(f"Imported {count:,} new server seeds")
            self.refresh_radar()
        except (OSError, ValueError, UnicodeError) as exc:
            plain_warning(self, "Import failed", str(exc))

    def export_servers(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export all discovered servers", "activity-bub-servers.csv", "CSV (*.csv)")
        if path:
            try:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["host"])
                    writer.writerows((host,) for host in self.store.all_hosts())
                self.logline.setText("Exported the complete server registry")
            except OSError as exc:
                plain_warning(self, "Export failed", str(exc))

    def preferences(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Activity Bub preferences")
        dialog.setMinimumWidth(min(440, self.width()-24))
        layout = QVBoxLayout(dialog)
        layout.addWidget(label("YOUR FEED, YOUR RULES", "muted"))
        layout.addWidget(label("Muted words or phrases (comma separated)", wrap=True))
        words = QLineEdit(self.store.setting("muted_words", ""))
        words.setMaxLength(4000)
        layout.addWidget(words)
        layout.addWidget(label("Muted authors · select and unmute", "muted"))
        authors = QListWidget()
        authors.addItems(self.store.muted_authors())
        authors.setMaximumHeight(160)
        layout.addWidget(authors)
        def unmute():
            item = authors.currentItem()
            if item:
                self.store.unmute_author(item.text())
                authors.takeItem(authors.row(item))
        layout.addWidget(button("Unmute selected author", unmute))
        image_policy="Restricted Linux preview workers; failures stay blocked." if self.images.isolated else "Remote previews disabled: restricted decoding is unavailable on this platform."
        layout.addWidget(label("Content warnings stay covered until you reveal a post. Nearby images preload in restricted workers. Audio and video open in your browser.\n\n"+image_policy+"\n\nThe cache retains the latest 50,000 posts plus your saved posts. Server discovery has no daily cap.\n\nDisplay backend: " + QApplication.platformName() + "\nData folder:\n" + str(self.data_dir), "muted", True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            self.store.set_setting("muted_words", words.text())
        self.refresh_feed()

    def closeEvent(self, event):
        self.closing = True
        self.reader_dialog.reject()
        self.timer.stop()
        self.heartbeat.stop()
        self.image_timer.stop()
        self.layout_timer.stop()
        self.feed.stop_scrolling()
        self.worker.engine.stop.set()
        self.worker.engine.paused.set()
        self.images.pool.clear()
        if self.worker.isRunning() or self.images.pool.activeThreadCount():
            self.setWindowTitle("Activity Bub · Finishing current requests…")
            self.setEnabled(False)
            event.ignore()
            QTimer.singleShot(150, self.close)
        else:
            self.store.close()
            event.accept()

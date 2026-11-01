"""Regression coverage for whole-image feed rendering and asynchronous resizing."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QBuffer, QIODevice, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from activity_bub.content import Post
from activity_bub.store import Store
from activity_bub.ui import STYLE, Window


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    return app


def image_bytes(width, height):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#fefefe"))
    painter = QPainter(image)
    # Distinct strips at every edge expose any cover/crop behavior.
    painter.fillRect(0, 0, width, max(4, height // 10), QColor("#ff0000"))
    painter.fillRect(0, height-height//10, width, height//10, QColor("#00ff00"))
    painter.fillRect(0, height//3, max(4, width//10), height//3, QColor("#0000ff"))
    painter.fillRect(width-width//10, height//3, width//10, height//3, QColor("#ff00ff"))
    painter.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buf, "PNG")
    return bytes(buf.data())


def make_post(n, image=False):
    return Post(uri=f"https://one.example/p/{n}", remote_id=str(n), server="one.example", author="Bub",
                handle="bub@one.example", text="Whole image, correct proportions", url=f"https://one.example/p/{n}",
                created=n, media=[{"preview":f"https://one.example/{n}.png", "url":"", "type":"image"}] if image else [])


def local_pixels(width,height):
    """Trusted synthetic pixels for cross-platform GUI geometry tests."""
    return QImage.fromData(image_bytes(width,height))


@pytest.mark.parametrize("source_size", [(240, 480), (400, 400), (640, 240), (160, 1600)])
def test_painted_feed_preserves_shape_and_all_four_edges(app, tmp_path, source_size):
    s = Store(":memory:")
    p = make_post(1, image=True)
    s.insert_posts([p])
    w = Window(s, tmp_path, demo=True)
    w.resize(640, 720)
    w.show()
    app.processEvents()
    w.images.loaded(p.media[0]["preview"], local_pixels(*source_size))
    width = 600
    text, text_height, image_height, row_height = w.delegate.metrics(p, width)
    # Portraits get more vertical room than landscape images; extreme images
    # are contained, retaining every edge rather than cropped to the canvas.
    assert image_height <= 520
    assert image_height <= round((width - 26) * source_size[1] / source_size[0])
    assert row_height <= w.feed.viewport().height() - 24
    target = QImage(width, row_height, QImage.Format.Format_RGB32)
    target.fill(QColor("#000000"))
    painter = QPainter(target)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, width, row_height)
    w.delegate.paint(painter, option, w.model.index(0))
    painter.end()
    # Count each edge strip's full extent in the actual rendered card.
    colored = {"red":[], "green":[], "blue":[], "magenta":[]}
    for y in range(54 + text_height + 8, 54 + text_height + 8 + image_height):
        for x in range(13, width - 13):
            c = target.pixelColor(x, y)
            if c.red() > 240 and c.green() < 15 and c.blue() < 15: colored["red"].append((x,y))
            elif c.green() > 240 and c.red() < 15 and c.blue() < 15: colored["green"].append((x,y))
            elif c.blue() > 240 and c.red() < 15 and c.green() < 15: colored["blue"].append((x,y))
            elif c.red() > 240 and c.blue() > 240 and c.green() < 15: colored["magenta"].append((x,y))
    assert all(colored.values()), "A source image edge was cropped out of the feed"
    bounds = [xy for points in colored.values() for xy in points]
    actual_width = max(x for x,y in bounds) - min(x for x,y in bounds) + 1
    actual_height = max(y for x,y in bounds) - min(y for x,y in bounds) + 1
    assert actual_width / actual_height == pytest.approx(source_size[0] / source_size[1], rel=.025)
    w.close()
    app.processEvents()


def test_image_loading_above_reader_keeps_scroll_anchor(app, tmp_path):
    s = Store(":memory:")
    s.insert_posts([make_post(i, image=True) for i in range(40)])
    w = Window(s, tmp_path, demo=True)
    w.show()
    app.processEvents()
    ix = w.model.index(8)
    w.feed.scrollTo(ix, w.feed.ScrollHint.PositionAtTop)
    bar = w.feed.verticalScrollBar()
    bar.setValue(bar.value() + 27)
    anchor = w.feed.indexAt(w.feed.viewport().rect().topLeft())
    before_uri = anchor.data(Qt.ItemDataRole.UserRole).uri
    before_top = w.feed.visualRect(anchor).top()
    w.images.loaded(w.model.posts[1].media[0]["preview"], local_pixels(200, 500))
    app.processEvents()
    after = w.feed.indexAt(w.feed.viewport().rect().topLeft())
    assert after.data(Qt.ItemDataRole.UserRole).uri == before_uri
    assert w.feed.visualRect(after).top() == before_top
    w.close()
    app.processEvents()


def test_decoding_preserves_portrait_resolution_without_upscaling(app, tmp_path):
    w = Window(Store(":memory:"), tmp_path, demo=True)
    w.images.loaded("https://one.example/tall.png", local_pixels(800, 1200))
    tall = w.images.get("https://one.example/tall.png")
    assert tall.size().width() == 800 and tall.size().height() == 1200
    w.images.loaded("https://one.example/small.png", local_pixels(80, 120))
    small = w.images.get("https://one.example/small.png")
    assert small.width() == 80 and small.height() == 120
    w.close()
    app.processEvents()

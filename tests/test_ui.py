import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from activity_bub.demo import artwork, populate
from activity_bub.store import Store
from activity_bub.ui import STYLE, Window


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    return app


def test_desktop_read_save_filter_radar_history_and_shutdown(app, tmp_path):
    store = Store(":memory:")
    populate(store)
    window = Window(store, tmp_path, demo=True)
    artwork(window.images)
    window.show()
    app.processEvents()
    assert window.model.rowCount() == 40
    assert not window.images.tasks  # offline demo never fetches remote media
    first = window.model.index(0)
    window.feed.setCurrentIndex(first)
    window.read_post(first)
    assert window.detail.post is not None
    window.detail.toggle_save()
    assert store.stats()["saved"] == 1
    window.navigate("saved")
    assert window.model.rowCount() == 1
    window.navigate("feed")
    window.load_more()
    assert window.model.rowCount() == 80
    window.media.setChecked(True)
    assert all(p.media for p in window.model.posts)
    window.navigate("servers")
    assert window.table.rowCount() == 3
    window.table.selectRow(0)
    window.pin_server()
    assert store.server(window.selected_host())["pinned"]
    window.browse_server()
    assert window.selected_server
    assert 460 <= window.grab().width() <= 960
    window.close()
    app.processEvents()
    assert not window.worker.isRunning()


def test_cw_is_covered_until_reveal(app, tmp_path):
    store = Store(":memory:")
    populate(store)
    window = Window(store, tmp_path, demo=True)
    p = next(p for p in window.model.posts if p.warning)
    window.detail.set_post(p)
    assert not window.detail.body.toPlainText()
    window.detail.reveal_post()
    assert p.text in window.detail.body.toPlainText()
    window.close()
    app.processEvents()


def test_rolling_window_does_not_grow_when_more_clicked_at_top(app, tmp_path):
    from activity_bub.content import Post
    store = Store(":memory:")
    store.add_servers(["one.example"])
    store.insert_posts([Post(uri=f"https://one.example/{i}", remote_id=str(i), server="one.example", author="Bub",
                             handle="bub@one.example", text="Window budget test", url=f"https://one.example/{i}", created=i) for i in range(700)])
    window = Window(store, tmp_path, demo=True)
    window.show()
    app.processEvents()
    for _ in range(25):
        window.load_more()
        app.processEvents()
    assert window.model.rowCount() <= 400
    window.images.ensure("https://remote.example/no-demo-network.jpg")
    assert not window.images.tasks
    window.close()
    app.processEvents()


def test_image_previews_accept_raster_and_reject_remote_svg(app, tmp_path):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage
    store = Store(":memory:")
    window = Window(store, tmp_path, demo=True)
    image = QImage(20, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("#b7ef6a"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    window.images.loaded("https://one.example/1.png", bytes(buffer.data()))
    assert window.images.get("https://one.example/1.png") is not None
    window.images.loaded("https://one.example/1.svg", b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20"/></svg>')
    assert "https://one.example/1.svg" in window.images.failed
    window.close()
    app.processEvents()


def test_bottom_accepts_newer_arrivals_once_without_jumping_existing_rows(app,tmp_path):
    from tests.test_feed_images import make_post
    store=Store(":memory:");store.insert_posts([make_post(1),make_post(2)])
    window=Window(store,tmp_path,demo=True)
    before=[p.uri for p in window.model.posts]
    store.insert_posts([make_post(100)])
    window.worker.engine.history.set()
    window.load_more()
    assert [p.uri for p in window.model.posts]==before+[make_post(100).uri]
    assert not window.worker.engine.history.is_set()
    window.load_more()
    assert window.model.rowCount()==3
    window.close();app.processEvents()


def test_power_controls_share_media_network_and_keep_half_screen_layout(app,tmp_path):
    store=Store(":memory:");populate(store)
    window=Window(store,tmp_path,demo=True)
    window.resize(480,540);window.show();app.processEvents()
    assert window.images.network is window.worker.engine.network
    assert "MAX" in window.engine_meter.text()
    assert "Discovery 0/10" in window.engine_meter.toolTip()
    window.set_power("quiet");app.processEvents()
    assert store.setting("power_profile")=="quiet"
    assert sum(window.worker.engine.snapshot()["limits"].values())==8
    assert window.images.pool.maxThreadCount()==2
    assert window.width()==480
    assert "QUIET" in window.engine_meter.text()
    window.close();app.processEvents()

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from activity_bub.demo import artwork,populate
from activity_bub.store import Store
from activity_bub.ui import MAX_FEED_WIDTH,STYLE,Window
from tests.test_feed_images import local_pixels,make_post


@pytest.fixture(scope="module")
def app():
    app=QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    return app


def settle(app):
    for _ in range(6):app.processEvents()


@pytest.mark.parametrize("window_size",[(480,540),(640,720),(960,540),(960,900),(1280,800)])
def test_full_portrait_card_fits_centered_reading_area(app,tmp_path,window_size):
    store=Store(":memory:")
    post=make_post(1,True)
    post.text="A portrait with a caption that wraps over more than one line. "*8
    store.insert_posts([post,make_post(0)])
    window=Window(store,tmp_path,demo=True)
    window.resize(*window_size)
    window.show();settle(app)
    window.images.loaded(post.media[0]["preview"],local_pixels(200,1400))
    settle(app)
    assert window.size().width()==window_size[0]
    assert window.column.width()<=MAX_FEED_WIDTH
    left=window.column.mapTo(window,QPoint(0,0)).x()
    assert abs(left+window.column.width()/2-window.width()/2)<=2
    rect=window.feed.visualRect(window.model.index(0))
    assert rect.height()<=window.feed.viewport().height()-24
    assert not window.detail.isVisible() and not window.reader_dialog.isVisible()
    assert not window.filter_panel.isVisible()
    window.close();settle(app)


def test_shrinking_window_recalculates_image_height(app,tmp_path):
    store=Store(":memory:");post=make_post(1,True);store.insert_posts([post,make_post(0)])
    window=Window(store,tmp_path,demo=True)
    window.resize(640,900);window.show();settle(app)
    window.images.loaded(post.media[0]["preview"],local_pixels(240,1400));settle(app)
    tall=window.feed.visualRect(window.model.index(0)).height()
    window.resize(640,480);settle(app)
    shorter=window.feed.visualRect(window.model.index(0)).height()
    assert shorter<tall
    assert shorter<=window.feed.viewport().height()-24
    window.close();settle(app)


def test_reader_and_filters_open_on_demand_without_a_second_column(app,tmp_path):
    store=Store(":memory:");populate(store)
    window=Window(store,tmp_path,demo=True);artwork(window.images)
    window.resize(480,540);window.show();settle(app)
    before=window.feed.width()
    window.filter_button.click();settle(app)
    assert window.filter_panel.isVisible()
    window.media.setChecked(True);settle(app)
    assert all(p.media for p in window.model.posts)
    window.filter_button.click();settle(app)
    assert not window.filter_panel.isVisible()
    window.open_reader(window.model.index(0));settle(app)
    assert window.reader_dialog.isVisible()
    assert window.reader_dialog.height()<=window.height()
    assert window.detail.body.height()>=60
    assert window.detail.picture.isVisible() and window.detail.picture.height()>30
    assert window.feed.width()==before
    window.reader_dialog.reject();settle(app)
    assert not window.reader_dialog.isVisible()
    window.navigate("servers");settle(app)
    assert window.width()==480 and window.table.rowCount()==3
    window.navigate("saved");settle(app)
    assert window.width()==480
    window.close();settle(app)

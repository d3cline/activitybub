"""Auto scrolling must stay controllable, preserve anchors, and honor CW choices."""
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from activity_bub.store import Store
from activity_bub.ui import Window, preview
from tests.test_feed_images import make_post, local_pixels
from tests.test_scrolling import settle, wheel


@pytest.fixture(scope="module")
def app():
    app=QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app


def make_window(app,tmp_path,count=100,media=False,path=":memory:"):
    store=Store(path)
    store.insert_posts([make_post(n,media) for n in range(count)])
    w=Window(store,tmp_path,demo=True)
    w.resize(480,540);w.show();settle(app)
    return w


def clock_for(monkeypatch):
    from activity_bub import scrolling
    clock=[100.0]
    monkeypatch.setattr(scrolling.time,"monotonic",lambda:clock[0])
    return clock


def test_autodoom_moves_at_selected_speed_without_catching_up_after_stalls(app,tmp_path,monkeypatch):
    w=make_window(app,tmp_path);clock=clock_for(monkeypatch)
    try:
        assert not w.autodoom_button.isChecked() and not w.auto_cw.isChecked()
        w.autodoom_button.click()
        assert w.feed.auto_active
        clock[0]+=.1;w.feed._auto_advance()
        assert w.feed.verticalScrollBar().value()==6
        w.doom_speed.setValue(400)
        clock[0]+=.1;w.feed._auto_advance()
        assert w.feed.verticalScrollBar().value()==30
        assert w.speed_label.text()=="4×"
        clock[0]+=20;w.feed._auto_advance()
        assert w.feed.verticalScrollBar().value()==54
        w.autodoom_button.click()
        assert not w.feed.auto_active
    finally:
        w.close();settle(app)


@pytest.mark.parametrize("action",["wheel","key","scrollbar","reader","refresh","pause","radar"])
def test_manual_control_and_reader_pause_autodoom(app,tmp_path,action):
    w=make_window(app,tmp_path)
    try:
        w.autodoom_button.click();assert w.feed.auto_active
        if action=="wheel":wheel(w.feed)
        elif action=="key":QTest.keyClick(w.feed,Qt.Key.Key_Down)
        elif action=="scrollbar":w.feed.verticalScrollBar().setValue(100)
        elif action=="reader":w.open_reader(w.model.index(0))
        elif action=="refresh":w.refresh_feed()
        elif action=="pause":w.toggle_pause()
        else:w.navigate("servers")
        assert not w.feed.auto_active and not w.autodoom_button.isChecked()
    finally:
        w.close();settle(app)


def test_speed_and_auto_cw_persist_but_scrolling_does_not_restart_on_launch(app,tmp_path):
    path=tmp_path/"preferences.sqlite3"
    w=make_window(app,tmp_path,path=path)
    w.doom_speed.setValue(225);w.auto_cw.click();w.autodoom_button.click()
    w.close();settle(app)
    w=make_window(app,tmp_path,path=path)
    try:
        assert w.doom_speed.value()==225 and w.speed_label.text()=="2.25×"
        assert w.auto_cw.isChecked() and w.auto_cw_enabled
        assert not w.autodoom_button.isChecked() and not w.feed.auto_active
    finally:
        w.close();settle(app)


def test_auto_cw_controls_reader_prefetch_and_recovers_without_losing_manual_reveals(app,tmp_path,monkeypatch):
    w=make_window(app,tmp_path,media=True)
    requested=[]
    monkeypatch.setattr(w.images,"ensure",lambda url:requested.append(url))
    w.demo=False  # No crawler was started; requests are intercepted above.
    try:
        p=w.model.posts[0];p.warning="A warning";p.sensitive=True
        manual=w.model.posts[1];manual.warning="Manual reveal"
        w.revealed.add(manual.uri)
        w.detail.set_post(p);w.relayout_feed();w.visible_images()
        assert preview(p) not in requested and not w.detail.body.toPlainText()
        w.auto_cw.click();w.visible_images()
        assert preview(p) in requested and p.text in w.detail.body.toPlainText()
        assert not w.covered(p) and not w.covered(manual)
        assert p.uri not in w.revealed
        requested.clear();w.auto_cw.click();w.visible_images()
        assert w.covered(p) and not w.covered(manual)
        assert preview(p) not in requested and not w.detail.body.toPlainText()
        # The hide filter still wins; Auto CW never unhides filtered-out posts.
        flagged=make_post(999,True);flagged.warning="Persisted warning";flagged.sensitive=True
        w.store.insert_posts([flagged])
        w.auto_cw.click();w.refresh_feed()
        assert any(p.uri==flagged.uri for p in w.model.posts)
        w.hide_cw.setChecked(True)
        assert not any(p.uri==flagged.uri for p in w.model.posts)
    finally:
        w.close();settle(app)


def test_autodoom_waits_at_end_and_continues_when_new_posts_arrive(app,tmp_path,monkeypatch):
    w=make_window(app,tmp_path,count=2);clock=clock_for(monkeypatch)
    w.demo=False
    try:
        bar=w.feed.verticalScrollBar();bar.setValue(bar.maximum())
        w.autodoom_button.click()
        clock[0]+=.1;w.feed._auto_advance();w.auto_load_more()
        assert w.feed.auto_active and w.worker.engine.history.is_set()
        assert bar.value()==bar.maximum()
        w.store.insert_posts([make_post(n) for n in range(100,110)])
        w.on_event("posts",10);w.relayout_feed()
        before=bar.value();clock[0]+=.1;w.feed._auto_advance()
        assert len(w.model.posts)==12 and bar.value()>before
        assert w.feed.auto_active
    finally:
        w.close();settle(app)


def test_first_live_posts_do_not_turn_off_autodoom(app,tmp_path):
    w=make_window(app,tmp_path,count=0)
    w.demo=False
    try:
        w.autodoom_button.click()
        w.store.insert_posts([make_post(n) for n in range(50)])
        w.on_event("posts",50);settle(app)
        assert len(w.model.posts)==40
        assert not w.empty.isVisible()
        assert w.feed.auto_active and w.autodoom_button.isChecked()
    finally:
        w.close();settle(app)


def test_rolling_trim_and_image_resize_preserve_auto_position(app,tmp_path,monkeypatch):
    w=make_window(app,tmp_path,count=700,media=True);clock=clock_for(monkeypatch)
    try:
        for _ in range(9):w.load_more()
        settle(app);assert len(w.model.posts)==400
        view=w.feed;bar=view.verticalScrollBar()
        view.scrollTo(w.model.index(320),view.ScrollHint.PositionAtTop)
        anchor=view.indexAt(view.viewport().rect().topLeft())
        uri=anchor.data(Qt.ItemDataRole.UserRole).uri;offset=view.visualRect(anchor).top()
        w.autodoom_button.click();w.load_more()
        after=view.indexAt(view.viewport().rect().topLeft())
        assert len(w.model.posts)==360 and after.data(Qt.ItemDataRole.UserRole).uri==uri
        assert view.visualRect(after).top()==offset and view.auto_active
        w.images.loaded(preview(w.model.posts[1]),local_pixels(1000,100));w.relayout_feed()
        after=view.indexAt(view.viewport().rect().topLeft())
        assert after.data(Qt.ItemDataRole.UserRole).uri==uri and view.visualRect(after).top()==offset
        before=bar.value();clock[0]+=.1;view._auto_advance()
        assert 0<bar.value()-before<=6 and view.auto_active
    finally:
        w.close();settle(app)


def test_modal_dialog_holds_position_without_accumulating_scroll_distance(app,tmp_path,monkeypatch):
    w=make_window(app,tmp_path);clock=clock_for(monkeypatch)
    dialog=QDialog(w);dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    try:
        w.autodoom_button.click();dialog.show();settle(app)
        assert QApplication.activeModalWidget() is dialog
        clock[0]+=30;w.feed._auto_advance()
        assert w.feed.verticalScrollBar().value()==0 and w.feed.auto_active
        dialog.close();settle(app)
        clock[0]+=.1;w.feed._auto_advance()
        assert 0<w.feed.verticalScrollBar().value()<=6
    finally:
        dialog.close();w.close();settle(app)

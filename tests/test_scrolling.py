"""Exercise wheel motion, real view geometry, image queue order, and anchoring."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading

import pytest
from PySide6.QtCore import QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from activity_bub.store import Store
from activity_bub.image_policy import supported
from activity_bub.ui import Images, Window, preview
from tests.test_feed_images import image_bytes, local_pixels, make_post


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def settle(app):
    for _ in range(6):
        app.processEvents()


def window(app, tmp_path, count=100, media=True):
    store = Store(":memory:")
    store.insert_posts([make_post(i, media) for i in range(count)])
    w = Window(store, tmp_path, demo=True)
    w.resize(640, 720)
    w.show()
    settle(app)
    return w


def wheel(view, angle=-120, pixels=0, phase=Qt.ScrollPhase.NoScrollPhase):
    position = view.viewport().rect().center()
    event = QWheelEvent(QPointF(position), QPointF(view.viewport().mapToGlobal(position)),
                        QPoint(0, pixels), QPoint(0, angle), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, phase, False)
    QApplication.sendEvent(view.viewport(), event)


def test_wheel_moves_in_intermediate_steps_and_reverses_immediately(app,tmp_path,monkeypatch):
    from activity_bub import scrolling
    w = window(app,tmp_path)
    clock = [100.0]
    monkeypatch.setattr(scrolling.time,"monotonic",lambda:clock[0])
    try:
        view = w.feed;bar = view.verticalScrollBar()
        bar.setValue(1000)
        wheel(view)
        assert bar.value()==1000 and view.motion.isActive()
        clock[0]+=.016;view._advance()
        assert 1000<bar.value()<1072
        forward = bar.value()
        wheel(view,120)
        clock[0]+=.016;view._advance()
        assert bar.value()<forward
        samples=[]
        for _ in range(24):
            clock[0]+=.016;view._advance();samples.append(bar.value())
        assert samples==sorted(samples,reverse=True)
        assert not view.motion.isActive()
        # A deliberate scrollbar jump cancels outstanding wheel travel.
        wheel(view);bar.setValue(200)
        assert not view.motion.isActive()
        view._advance();assert bar.value()==200
    finally:
        w.close();settle(app)


def test_native_pixel_and_momentum_gestures_are_not_animated_twice(app,tmp_path):
    w = window(app,tmp_path)
    try:
        bar=w.feed.verticalScrollBar();bar.setValue(1000)
        wheel(w.feed,angle=0,pixels=-37,phase=Qt.ScrollPhase.ScrollUpdate)
        assert bar.value()==1037
        assert not w.feed.motion.isActive()
        wheel(w.feed,angle=0,pixels=19,phase=Qt.ScrollPhase.ScrollMomentum)
        assert bar.value()==1018
        assert not w.feed.motion.isActive()
    finally:
        w.close();settle(app)


def test_prefetch_warms_nearby_offscreen_images_in_both_directions(app,tmp_path,monkeypatch):
    w = window(app,tmp_path)
    requested=[]
    monkeypatch.setattr(w.images,"prioritize",lambda urls:requested.__setitem__(slice(None),urls))
    w.demo=False  # Only the image selector is exercised; no crawler is started.
    try:
        w.feed.scrollTo(w.model.index(12),w.feed.ScrollHint.PositionAtTop)
        settle(app)
        top=w.feed.indexAt(QPoint(w.feed.viewport().width()//2,0)).row()
        sensitive=w.model.posts[top+1];sensitive.warning="Covered"
        w.visible_images()
        height=w.feed.viewport().height()
        requested_rows=[i for i,p in enumerate(w.model.posts) if preview(p) in requested]
        assert preview(w.model.posts[top])==requested[0]
        assert preview(sensitive) not in requested
        assert any(w.feed.visualRect(w.model.index(i)).top()>=height for i in requested_rows)
        assert any(w.feed.visualRect(w.model.index(i)).bottom()<0 for i in requested_rows)
        assert preview(w.model.posts[-1]) not in requested
        for i in requested_rows:
            rect=w.feed.visualRect(w.model.index(i))
            assert rect.top()<=3*height and rect.bottom()>=-height
        w._scroll_direction=-1;w.visible_images()
        above=[requested.index(preview(p)) for i,p in enumerate(w.model.posts) if preview(p) in requested
               and w.feed.visualRect(w.model.index(i)).bottom()<0]
        below=[requested.index(preview(p)) for i,p in enumerate(w.model.posts) if preview(p) in requested
               and w.feed.visualRect(w.model.index(i)).top()>=height]
        assert above and below and max(above)<min(below)
        w.revealed.add(sensitive.uri);w.visible_images()
        assert preview(sensitive) in requested
    finally:
        w.close();settle(app)


def test_loading_image_above_view_preserves_anchor_and_remaining_wheel_travel(app,tmp_path):
    w = window(app,tmp_path)
    try:
        view=w.feed;bar=view.verticalScrollBar()
        view.scrollTo(w.model.index(8),view.ScrollHint.PositionAtTop)
        bar.setValue(bar.value()+27)
        anchor=view.indexAt(view.viewport().rect().topLeft())
        uri=anchor.data(Qt.ItemDataRole.UserRole).uri
        offset=view.visualRect(anchor).top()
        wheel(view)
        remaining=view._target-bar.value()
        w.images.loaded(preview(w.model.posts[1]),local_pixels(1000,100))
        w.relayout_feed()
        after=view.indexAt(view.viewport().rect().topLeft())
        assert after.data(Qt.ItemDataRole.UserRole).uri==uri
        assert view.visualRect(after).top()==offset
        assert view.motion.isActive()
        assert view._target-bar.value()==pytest.approx(remaining,abs=1)
    finally:
        w.close();settle(app)


def test_rolling_feed_trim_preserves_wheel_motion_and_position(app,tmp_path):
    w = window(app,tmp_path,count=700,media=False)
    try:
        for _ in range(9):w.load_more()
        settle(app)
        assert w.model.rowCount()==400
        view=w.feed;bar=view.verticalScrollBar()
        view.scrollTo(w.model.index(320),view.ScrollHint.PositionAtTop)
        anchor=view.indexAt(view.viewport().rect().topLeft())
        uri=anchor.data(Qt.ItemDataRole.UserRole).uri
        offset=view.visualRect(anchor).top()
        wheel(view);remaining=view._target-bar.value()
        w.load_more()
        after=view.indexAt(view.viewport().rect().topLeft())
        assert w.model.rowCount()==360
        assert after.data(Qt.ItemDataRole.UserRole).uri==uri
        assert view.visualRect(after).top()==offset
        assert view.motion.isActive()
        assert view._target-bar.value()==pytest.approx(remaining,abs=1)
    finally:
        w.close();settle(app)


@pytest.mark.skipif(not supported(),reason="Restricted remote-image decoding is Linux-only")
def test_fast_scroll_discards_stale_queued_images_before_requesting_new_ones(app):
    entered=threading.Event();release=threading.Event();calls=[]
    payload=image_bytes(80,120)
    class Network:
        def request(self,url,**_):
            calls.append(url)
            if len(calls)==1:entered.set();release.wait(2)
            return payload
    images=Images(threading.Event(),network=Network())
    images.pool.setMaxThreadCount(1)
    first="https://one.example/first.png"
    old="https://one.example/stale.png"
    new="https://one.example/visible.png"
    nearby="https://one.example/nearby.png"
    try:
        images.ensure(first);assert entered.wait(1)
        images.ensure(old)
        images.prioritize([new,nearby,new])
        assert old not in images.tasks
        assert set(images.tasks)=={first,new,nearby}
    finally:
        release.set();assert images.pool.waitForDone(3000)
        settle(app)
    assert calls==[first,new,nearby]
    assert not images.tasks


def test_resized_previews_are_reused_and_bounded_without_changing_source(app):
    images=Images(threading.Event())
    url="https://one.example/portrait.png"
    images.loaded(url,local_pixels(800,1200))
    first=images.fitted(url,QSize(300,400),2)
    assert images.fitted(url,QSize(300,400),2).cacheKey()==first.cacheKey()
    assert first.devicePixelRatio()==2
    assert first.deviceIndependentSize().width()<=300
    assert first.deviceIndependentSize().height()<=400
    assert first.width()/first.height()==pytest.approx(800/1200,rel=.01)
    for i in range(80):images.fitted(url,QSize(600+i,800),2)
    assert len(images.fitted_cache)<=64 and images.fitted_bytes<=16*1024*1024
    assert images.get(url).size()==QSize(800,1200)

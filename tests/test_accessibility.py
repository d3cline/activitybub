"""Accessible content must follow the same CW, media, and motion choices as sighted UI."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialogButtonBox, QPushButton

from activity_bub.accessibility import Accessibility, VISION_OPTIONS, adapt_theme
from activity_bub.store import Store
from activity_bub.themes import get_theme
from activity_bub.ui import Window
from tests.test_feed_images import make_post, local_pixels
from tests.test_scrolling import settle, wheel
from tests.test_themes_passport import luminance


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app


def window(app, tmp_path, prefs=Accessibility(), path=":memory:"):
    store = Store(path)
    store.insert_posts([make_post(n, True) for n in range(40)])
    store.set_setting("accessibility", prefs.saved())
    w = Window(store, tmp_path, demo=True)
    w.resize(480, 540)
    w.show()
    settle(app)
    return w


@pytest.mark.parametrize("value", [None, [], "bad", {"scale": True, "bold": "yes", "vision": [], "font": "bogus"}])
def test_invalid_preferences_recover_to_defaults(value):
    assert Accessibility.load(value) == Accessibility()


@pytest.mark.parametrize("vision", [key for key, _ in VISION_OPTIONS])
@pytest.mark.parametrize("contrast", ["dark", "light"])
def test_high_contrast_text_is_at_least_seven_to_one(vision, contrast):
    theme = adapt_theme(get_theme("daylight"), Accessibility(vision=vision, contrast=contrast))
    for ink in (theme.text, theme.muted, theme.accent, theme.warning):
        for background in (theme.bg, theme.card, theme.panel, theme.selected):
            a, b = sorted((luminance(ink), luminance(background)))
            assert (b+.05)/(a+.05) >= 7
    a, b = sorted((luminance(theme.accent_text), luminance(theme.accent)))
    assert (b+.05)/(a+.05) >= 7


def test_screen_reader_tree_never_exposes_covered_body_or_alt(app, tmp_path):
    w = window(app, tmp_path, Accessibility(screen_reader=True))
    try:
        post = w.model.posts[0]
        post.warning = "A content warning"
        post.text = "SECRET BODY"
        post.media[0]["alt"] = "SECRET ALT"
        w.model.descriptions_changed()
        w.relayout_feed(); settle(app)
        index = w.model.index(0)
        interface = QAccessible.queryAccessibleInterface(w.feed)
        assert interface.text(QAccessible.Text.Name) == "Public post feed"
        item = interface.child(0)
        hidden = item.text(QAccessible.Text.Name)
        assert "content warning" in hidden and "Content hidden" in hidden
        for role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            assert "SECRET" not in index.data(role)
        assert "SECRET" not in hidden
        w.open_post(post); settle(app)
        assert w.detail.reveal.hasFocus()
        assert not w.detail.body.toPlainText() and not w.detail.alt.text()
        w.detail.reveal.click(); settle(app)
        assert "SECRET BODY" in item.text(QAccessible.Text.Name)
        assert "SECRET ALT" in index.data(Qt.ItemDataRole.AccessibleTextRole)
        assert "SECRET ALT" in w.detail.alt.text()
        w.revealed.clear(); w.set_auto_cw(True); settle(app)
        assert "SECRET BODY" in item.text(QAccessible.Text.Name)
        w.set_auto_cw(False); settle(app)
        assert "SECRET" not in item.text(QAccessible.Text.Name)
    finally:
        w.close(); settle(app)


@pytest.mark.parametrize("option", ["reduce_motion", "screen_reader"])
def test_motion_preference_stops_and_prevents_animated_scroll(app, tmp_path, option):
    w = window(app, tmp_path)
    try:
        w.autodoom_button.click(); assert w.feed.auto_active
        w.set_accessibility(replace(w.accessibility, **{option: True}))
        assert not w.feed.auto_active and not w.feed.motion.isActive()
        assert not w.autodoom_button.isEnabled()
        w.feed.set_auto_scroll(True)
        w.feed.setFocus(); QTest.keyClick(w.feed, Qt.Key.Key_D)
        wheel(w.feed)
        assert not w.feed.auto_active and not w.feed.motion.isActive()
        assert not w.autodoom_button.isChecked()
        w.set_accessibility(Accessibility())
        w.autodoom_button.click(); assert w.feed.auto_active
    finally:
        w.close(); settle(app)


def test_hide_previews_removes_geometry_and_requests_but_keeps_alt(app, tmp_path, monkeypatch):
    w = window(app, tmp_path)
    requests = []
    monkeypatch.setattr(w.images, "ensure", requests.append)
    try:
        post = w.model.posts[0]
        post.media[0]["alt"] = "A tall green tree"
        w.images.loaded(post.media[0]["preview"], local_pixels(200,1000))
        assert w.delegate.metrics(post,440)[2] > 0
        w.set_accessibility(Accessibility(hide_images=True))
        requests.clear(); w.demo = False
        w.visible_images(); w.open_post(post); settle(app)
        assert not requests and w.images.blocked()
        assert w.delegate.metrics(post,440)[2] == 0
        assert w.detail.picture.isHidden()
        assert "A tall green tree" in w.detail.alt.text()
        assert "A tall green tree" in w.model.index(0).data(Qt.ItemDataRole.AccessibleTextRole)
    finally:
        w.close(); settle(app)


def test_preferences_persist_and_zoom_does_not_accumulate(app, tmp_path):
    path = tmp_path/"prefs.sqlite3"
    prefs = Accessibility(vision="tritan", contrast="dark", scale=200, font="mono", bold=True,
                          large_controls=True, hide_images=True, screen_reader=True)
    w = window(app, tmp_path, prefs, path)
    font_key = w.delegate.font.key()
    for _ in range(3): w.apply_theme("ocean", persist=False)
    assert w.delegate.font.key() == font_key
    w.close(); settle(app)
    store = Store(path)
    w = Window(store,tmp_path,demo=True)
    try:
        assert w.accessibility == prefs
        assert w.feed.itemDelegate() is w.text_delegate
        w.zoom(-1); assert w.accessibility.scale == 175
        w.zoom(1); w.zoom(1); assert w.accessibility.scale == 200
    finally:
        w.close(); settle(app)


@pytest.mark.parametrize("scale", [100, 150, 200])
def test_zoomed_narrow_window_and_reader_remain_reachable(app, tmp_path, scale):
    w = window(app,tmp_path,Accessibility(scale=scale))
    try:
        assert w.width() == 480
        assert w.feed.viewport().height() >= 80
        for control in (w.title,w.pause,w.menu_button,w.motion_group,w.feed_actions):
            rect = control.geometry()
            assert rect.right() <= control.parentWidget().width()
        w.feed.setCurrentIndex(w.model.index(0))
        QTest.keyClick(w.feed, Qt.Key.Key_Return); settle(app)
        assert w.reader_dialog.isVisible() and w.detail.body.hasFocus()
        assert w.detail.body.minimumHeight() >= 100
        assert w.reader_scroll.widgetResizable()
        # The full post and controls scroll vertically at 200% in a small window.
        assert w.reader_scroll.horizontalScrollBar().maximum() == 0
        for _ in range(12):
            if w.detail.mute.hasFocus():
                break
            QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab); settle(app)
        assert w.detail.mute.hasFocus() and not w.detail.mute.visibleRegion().isEmpty()
        QTest.keyClick(w.detail.mute, Qt.Key.Key_Escape); settle(app)
        assert not w.reader_dialog.isVisible()
    finally:
        w.close(); settle(app)


def test_accessibility_dialog_save_cancel_and_reset(app, tmp_path):
    w = window(app,tmp_path)
    try:
        def choose(save):
            dialog = app.activeModalWidget()
            combo = next(c for c in dialog.findChildren(QComboBox) if c.accessibleName() == "Color visibility")
            combo.setCurrentIndex(combo.findData("tritan"))
            buttons = dialog.findChild(QDialogButtonBox)
            buttons.button(QDialogButtonBox.StandardButton.Save if save else QDialogButtonBox.StandardButton.Cancel).click()
        QTimer.singleShot(0,lambda:choose(False)); w.accessibility_dialog()
        assert w.accessibility.vision == "standard"
        QTimer.singleShot(0,lambda:choose(True)); w.accessibility_dialog()
        assert w.accessibility.vision == "tritan"
        assert w.store.setting("accessibility")["vision"] == "tritan"
        def reset():
            dialog = app.activeModalWidget()
            next(c for c in dialog.findChildren(QPushButton) if c.text() == "Reset accessibility defaults").click()
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        QTimer.singleShot(0, reset); w.accessibility_dialog()
        assert w.accessibility == Accessibility()
    finally:
        w.close(); settle(app)

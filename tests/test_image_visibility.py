"""Image visibility must affect both displays without destroying original pixels."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
import pytest
from PySide6.QtCore import QSize, QRect
from PySide6.QtGui import QImage, QColor, QPainter
from PySide6.QtWidgets import QApplication, QCheckBox, QDialogButtonBox, QStyleOptionViewItem
from PySide6.QtCore import QTimer

from activity_bub.accessibility import Accessibility
from activity_bub.image_visibility import filter_image
from activity_bub.store import Store
from activity_bub.ui import Window
from tests.test_feed_images import make_post
from tests.test_scrolling import settle


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pixels(width=120, height=80):
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(40,110,220,128))
    return image


@pytest.mark.parametrize("vision", ["tritan", "protan", "deutan", "mono", "warm"])
def test_every_color_filter_changes_rgb_preserves_alpha_and_source(vision):
    image = pixels()
    before = bytes(image.constBits())
    out = filter_image(image, Accessibility(vision=vision))
    assert out.size() == image.size()
    assert out.pixelColor(0,0) != image.pixelColor(0,0)
    assert out.pixelColor(0,0).alpha() == 128
    assert bytes(image.constBits()) == before
    if vision == "mono":
        c = out.pixelColor(0,0)
        assert c.red() == c.green() == c.blue()
    elif vision == "warm":
        assert out.pixelColor(0,0).blue() < image.pixelColor(0,0).blue()


@pytest.mark.parametrize("vision", ["tritan", "protan", "deutan"])
def test_recoloring_keeps_neutral_pixels_neutral_and_preserves_transparency(vision):
    image = QImage(4,1,QImage.Format.Format_RGBA8888)
    for x, v in enumerate((0,80,160,255)):
        image.setPixelColor(x,0,QColor(v,v,v,x*85))
    out = filter_image(image,Accessibility(vision=vision))
    for x,v in enumerate((0,80,160,255)):
        c=out.pixelColor(x,0)
        assert all(abs(channel-v)<=1 for channel in (c.red(),c.green(),c.blue()))
        assert c.alpha() == x*85


def test_high_contrast_boosts_tones_without_inverting_photos():
    image = QImage(2,1,QImage.Format.Format_RGBA8888)
    image.setPixelColor(0,0,QColor(64,64,64,40))
    image.setPixelColor(1,0,QColor(192,192,192,200))
    for mode in ("dark","light"):
        out=filter_image(image,Accessibility(contrast=mode))
        assert out.pixelColor(0,0).red() < 64
        assert out.pixelColor(1,0).red() > 192
        assert out.pixelColor(0,0).alpha() == 40
        assert out.pixelColor(1,0).alpha() == 200


def test_filters_only_use_bounded_raw_pixels_and_toggle_bypasses_processing(monkeypatch):
    from PIL import Image
    def reject(*_, **__):
        raise AssertionError("An image file decoder was called")
    monkeypatch.setattr(Image, "open", reject)
    monkeypatch.setattr(QImage, "fromData", reject)
    image = pixels()
    assert not filter_image(image,Accessibility(vision="tritan")).isNull()
    monkeypatch.setattr(Image,"frombytes",reject)
    assert filter_image(image,Accessibility()) is image
    assert filter_image(image,Accessibility(vision="tritan",contrast="dark",image_filters=False)) is image
    with pytest.raises(TypeError):
        filter_image(b"not an image",Accessibility(vision="tritan"))
    with pytest.raises(ValueError):
        filter_image(QImage(1601,1200,QImage.Format.Format_RGBA8888),Accessibility(vision="tritan"))
    assert filter_image(QImage(),Accessibility(vision="tritan")).isNull()


def test_feed_and_reader_share_filters_cache_invalidation_and_original_restore(app,tmp_path,monkeypatch):
    store=Store(":memory:");post=make_post(1,True);store.insert_posts([post])
    w=Window(store,tmp_path,demo=True);w.resize(640,720);w.show();settle(app)
    url=post.media[0]["preview"]
    w.images.loaded(url,pixels(240,480));settle(app)
    source_key=w.images.get(url).cacheKey()
    original=w.images.fitted(url,QSize(200,200),2)
    requests=[]
    monkeypatch.setattr(w.images,"ensure",requests.append)
    try:
        # Existing 0.5 preferences get image filters without needing a new opt-in.
        prefs=Accessibility.load({"vision":"mono"})
        assert prefs.image_filters
        w.set_accessibility(prefs);w.detail.set_post(w.model.posts[0]);settle(app)
        feed=w.images.fitted(url,QSize(200,200),2)
        reader=w.detail.picture.pixmap()
        for pix in (feed,reader):
            c=pix.toImage().pixelColor(0,0)
            assert c.red() == c.green() == c.blue()
            assert pix.width()/pix.height() == pytest.approx(.5)
        assert feed.devicePixelRatioF() == 2
        assert w.images.fitted(url,QSize(200,200),2).cacheKey() == feed.cacheKey()
        assert w.images.get(url).cacheKey() == source_key
        w.set_accessibility(replace(prefs,image_filters=False));settle(app)
        restored=w.images.fitted(url,QSize(200,200),2)
        assert restored.toImage() == original.toImage()
        c=w.detail.picture.pixmap().toImage().pixelColor(0,0)
        assert c.blue()>c.red()
        assert not requests
        assert w.images.fitted_bytes == sum(p.width()*p.height()*4 for p in w.images.fitted_cache.values())
        assert w.images.fitted_bytes <= 16*1024*1024
    finally:
        w.close();settle(app)


def test_image_filter_preference_saves_and_is_present_in_settings(app,tmp_path):
    path=tmp_path/"prefs.sqlite3"
    store=Store(path);w=Window(store,tmp_path,demo=True);w.show();settle(app)
    try:
        def choose():
            dialog=app.activeModalWidget()
            check=next(c for c in dialog.findChildren(QCheckBox) if c.accessibleName()=="Apply color and contrast settings to images")
            assert check.isChecked()
            check.setChecked(False)
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        QTimer.singleShot(0,choose);w.accessibility_dialog()
        assert not w.accessibility.image_filters
    finally:
        w.close();settle(app)
    store=Store(path);w=Window(store,tmp_path,demo=True)
    try:
        assert not w.accessibility.image_filters
    finally:
        w.close();settle(app)


@pytest.mark.parametrize("vision", ["tritan", "protan", "deutan", "mono", "warm"])
def test_actual_feed_paint_uses_filter_for_images_arriving_after_setting_change(app,tmp_path,vision):
    store=Store(":memory:");post=make_post(1,True);store.insert_posts([post])
    w=Window(store,tmp_path,demo=True);w.show();settle(app)
    try:
        prefs=Accessibility(vision=vision)
        w.set_accessibility(prefs)
        image=pixels(240,480);image.fill(QColor(40,110,220))
        w.images.loaded(post.media[0]["preview"],image);settle(app)
        width=440
        _,text_h,image_h,row_h=w.delegate.metrics(post,width)
        target=QImage(width,row_h,QImage.Format.Format_RGBA8888)
        target.fill(QColor("black"))
        option=QStyleOptionViewItem();option.rect=QRect(0,0,width,row_h)
        painter=QPainter(target)
        w.delegate.paint(painter,option,w.model.index(0));painter.end()
        image_y=16+w.delegate.geometry()[0]+text_h+8
        actual=target.pixelColor(width//2,image_y+image_h//2)
        expected=filter_image(image,prefs).pixelColor(0,0)
        assert actual == expected
    finally:
        w.close();settle(app)

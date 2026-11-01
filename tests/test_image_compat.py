import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import struct
import threading
import time
import zlib

import pytest
from PySide6.QtCore import QBuffer, QIODevice, qInstallMessageHandler
from PySide6.QtGui import QColor, QIcon, QImage
from PySide6.QtWidgets import QApplication, QLineEdit

from activity_bub.image_io import decode_raster, remove_duplicate_exif
from activity_bub.ui import Images, add_clear_action


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app


def png():
    image = QImage(80,120,QImage.Format.Format_RGB32)
    image.fill(QColor("#aabbcc"))
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buf,"PNG")
    return bytes(buf.data())


def test_clear_search_avoids_reproduced_gnome_palette_warnings(app,tmp_path):
    actions = tmp_path/"Buggy"/"scalable"/"actions"
    actions.mkdir(parents=True)
    (tmp_path/"Buggy"/"index.theme").write_text('[Icon Theme]\nName=Buggy\nDirectories=scalable/actions\n\n[scalable/actions]\nSize=16\nType=Scalable\nContext=Actions\n')
    (actions/"edit-clear.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><path fill="url(#gpa:foreground)" d="M1 1h14v14H1z"/></svg>')
    old_paths,old_name = QIcon.themeSearchPaths(),QIcon.themeName()
    QIcon.setThemeSearchPaths([str(tmp_path)])
    QIcon.setThemeName("Buggy")
    messages=[]
    previous = qInstallMessageHandler(lambda typ,context,message:messages.append(message))
    try:
        assert QIcon.hasThemeIcon("edit-clear")
        old = QLineEdit()
        old.setClearButtonEnabled(True)
        old.setText("before")
        old.show();app.processEvents();old.grab();old.close();app.processEvents()
        assert any("gpa:foreground" in m for m in messages), "Baseline must reproduce the user's warning"
        messages.clear()
        fixed = QLineEdit()
        clear = add_clear_action(fixed)
        assert not clear.isVisible()
        fixed.setText("after")
        fixed.show();app.processEvents();fixed.grab()
        assert clear.isVisible()
        clear.trigger()
        assert not fixed.text() and not clear.isVisible()
        fixed.close();app.processEvents()
        assert not any("gpa:foreground" in m for m in messages)
    finally:
        qInstallMessageHandler(previous)
        QIcon.setThemeSearchPaths(old_paths)
        QIcon.setThemeName(old_name)


def test_png_duplicate_exif_is_cleaned_without_changing_pixels(app):
    source = png()
    payload = b'II*\x00\x08\x00\x00\x00\x01\x00\x12\x01\x03\x00\x01\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00'
    kind = b"eXIf"
    chunk = struct.pack(">I",len(payload)) + kind + payload + struct.pack(">I",zlib.crc32(kind+payload)&0xffffffff)
    # Insert both metadata chunks after IHDR, before image data.
    duplicate = source[:33] + chunk + chunk + source[33:]
    cleaned = remove_duplicate_exif(duplicate)
    assert cleaned.count(b"eXIf") == 1
    assert len(duplicate)-len(cleaned) == len(chunk)
    messages=[]
    previous = qInstallMessageHandler(lambda typ,context,message:messages.append(message))
    try:
        decoded = decode_raster(duplicate)
        assert decoded == decode_raster(source)
        assert not any("duplicate" in m.lower() for m in messages)
    finally:
        qInstallMessageHandler(previous)


def test_remote_svg_never_enters_qt_svg_plugin(app,monkeypatch):
    from activity_bub import image_io
    def reject(*args,**kwargs): raise AssertionError("SVG was probed by a Qt image plugin")
    monkeypatch.setattr(image_io,"QImageReader",reject)
    assert decode_raster(b'<svg xmlns="http://www.w3.org/2000/svg"><path fill="url(#gpa:foreground)"/></svg>').isNull()


def test_invalid_image_and_truncated_metadata_fail_without_exception(app):
    assert decode_raster(b"<html>not an image</html>").isNull()
    truncated = b"\x89PNG\r\n\x1a\n\xff\xff\xff\xffeXIf"
    assert remove_duplicate_exif(truncated) == truncated
    assert decode_raster(truncated).isNull()


def test_real_image_job_decodes_off_gui_thread(app,monkeypatch):
    from activity_bub import ui
    image = png()
    decoder_threads=[]
    original = ui.decode_raster
    def tracked(data):
        decoder_threads.append(threading.get_ident())
        return original(data)
    monkeypatch.setattr(ui,"decode_raster",tracked)
    images = Images(threading.Event())
    class Network:
        def request(self,*args,**kwargs): return image
    images.network = Network()
    url = "https://one.example/image.png"
    images.ensure(url)
    assert images.pool.waitForDone(2000)
    deadline = time.monotonic()+2
    while images.get(url) is None and time.monotonic()<deadline:
        app.processEvents()
    assert images.get(url) is not None
    assert decoder_threads and all(t!=threading.get_ident() for t in decoder_threads)

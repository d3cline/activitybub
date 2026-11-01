"""Bounded raster-only decoding without probing Qt's SVG renderer."""
from __future__ import annotations

import struct

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QImageReader


PNG = b"\x89PNG\r\n\x1a\n"


def remove_duplicate_exif(data):
    """Keep the first PNG eXIf chunk and retain all image/other metadata bytes."""
    if not data.startswith(PNG):
        return data
    chunks = [PNG]
    offset, seen_exif, changed = 8, False, False
    for _ in range(4096):
        if offset + 12 > len(data):
            return data  # Let the decoder reject malformed/truncated input.
        length = struct.unpack_from(">I", data, offset)[0]
        end = offset + length + 12
        if end > len(data):
            return data
        kind = data[offset + 4:offset + 8]
        if kind == b"eXIf" and seen_exif:
            changed = True
        else:
            chunks.append(data[offset:end])
        seen_exif = seen_exif or kind == b"eXIf"
        offset = end
        if kind == b"IEND":
            return b"".join(chunks) + data[end:] if changed else data
    return data


def raster_format(data):
    if data.startswith(PNG):
        return b"png"
    if data.startswith(b"\xff\xd8\xff"):
        return b"jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return b"gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return b"webp"
    if data[:2] == b"BM":
        return b"bmp"
    if data[4:8] == b"ftyp":
        brands = {data[i:i+4] for i in range(8, min(len(data), 64), 4)}
        if brands & {b"avif", b"avis"}:
            return b"avif"
        if brands & {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}:
            return b"heif"
    return b""


def decode_raster(data):
    fmt = raster_format(data)
    if not fmt or len(data) > 5 * 1024 * 1024:
        return QImage()
    data = remove_duplicate_exif(data) if fmt == b"png" else data
    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, fmt)
    reader.setAutoDetectImageFormat(False)
    size = reader.size()
    if not size.isValid() or not 0 < size.width() <= 12000 or not 0 < size.height() <= 12000:
        return QImage()
    reader.setAutoTransform(True)
    if size.width() > 1200 or size.height() > 1600:
        reader.setScaledSize(size.scaled(1200, 1600, Qt.AspectRatioMode.KeepAspectRatio))
    picture = reader.read()
    picture.setDevicePixelRatio(1)
    return picture

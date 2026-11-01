"""Recognize raster containers, then accept only raw pixels from a restricted worker."""
from __future__ import annotations

import os
from pathlib import Path
import selectors
import struct
import subprocess
import sys
import threading
import time

from .image_policy import MAX_EDGE, MAX_INPUT, MAX_OUTPUT, MAX_OUTPUT_PIXELS, WALL_SECONDS, supported


PNG = b"\x89PNG\r\n\x1a\n"
_decoders = threading.BoundedSemaphore(2)


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
    return b""


def _exchange(command, data, *, stop=None, seconds=WALL_SECONDS, max_output=MAX_OUTPUT, env=None, cwd=None):
    """Bound both pipes and wall time; communicate() would buffer unlimited output."""
    process = subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL,close_fds=True,bufsize=0,env=env,cwd=cwd)
    deadline = time.monotonic()+seconds
    result = bytearray()
    pending = memoryview(data)
    try:
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdin.fileno(),False)
            os.set_blocking(process.stdout.fileno(),False)
            selector.register(process.stdin,selectors.EVENT_WRITE)
            selector.register(process.stdout,selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic()>=deadline or (stop is not None and stop.is_set()):
                    return None
                for key, _ in selector.select(min(.1,max(0,deadline-time.monotonic()))):
                    if key.fileobj is process.stdin:
                        try:
                            count = os.write(key.fd,pending[:65536]) if pending else 0
                            pending = pending[count:]
                        except BrokenPipeError:
                            pending = memoryview(b"")
                        if not pending:
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        chunk = os.read(key.fd,min(65536,max_output+1-len(result)))
                        if not chunk:
                            selector.unregister(process.stdout)
                        else:
                            result.extend(chunk)
                            if len(result)>max_output:
                                return None
            if process.wait(timeout=max(.01,deadline-time.monotonic()))!=0:
                return None
        return bytes(result)
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)
        process.stdin.close()
        process.stdout.close()


def worker_command():
    if getattr(sys,"frozen",False):
        return [sys.executable,"--_decode-image"],str(Path(sys.executable).resolve().parent)
    return [sys.executable,"-m","activity_bub.image_worker"],str(Path(__file__).resolve().parents[1])


def worker_environment():
    # Do not give the image parser API keys, cloud credentials, browser session
    # variables, injected Python paths, or unrelated application environment.
    keys = {"LANG","LC_ALL","LC_CTYPE"}
    if getattr(sys,"frozen",False):
        keys |= {"LD_LIBRARY_PATH","_PYI_APPLICATION_HOME_DIR","_PYI_ARCHIVE_FILE","_PYI_PARENT_PROCESS_LEVEL"}
    return {key:value for key,value in os.environ.items() if key in keys}


def packet_image(packet):
    from PySide6.QtGui import QImage
    if packet is None or len(packet)<12:
        return QImage()
    magic,width,height = struct.unpack("!4sII",packet[:12])
    if (magic!=b"BUB1" or not 0<width<=MAX_EDGE or not 0<height<=MAX_EDGE
            or width*height>MAX_OUTPUT_PIXELS or len(packet)!=12+width*height*4):
        return QImage()
    # Fixed raw pixels, never QImage.fromData(), a remote image reader, pickle,
    # HTML, paths, or any other executable/interpreted worker output.
    return QImage(packet[12:],width,height,width*4,QImage.Format.Format_RGBA8888).copy()


def decode_raster(data, *, stop=None):
    from PySide6.QtGui import QImage
    if (not isinstance(data,(bytes,bytearray)) or len(data)>MAX_INPUT or not raster_format(data)
            or not supported() or (stop is not None and stop.is_set())):
        return QImage()
    command,cwd = worker_command()
    while not _decoders.acquire(timeout=.1):
        if stop is not None and stop.is_set():
            return QImage()
    try:
        return packet_image(_exchange(command,data,stop=stop,env=worker_environment(),cwd=cwd))
    except (OSError,ValueError):
        return QImage()  # Never fall back to decoding remote files in the GUI.
    finally:
        _decoders.release()

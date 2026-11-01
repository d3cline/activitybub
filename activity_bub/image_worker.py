"""Decode one image after OS restrictions; return a fixed raw RGBA packet."""
import io
import copy
import os
import struct
import warnings

from .image_policy import MAX_EDGE, MAX_INPUT, MAX_OUTPUT_PIXELS, MAX_SOURCE_PIXELS, restrict_worker
from .image_io import raster_format, remove_duplicate_exif


def main():
    # Import decoder code before filesystem opens are prohibited. No remote
    # bytes are read until every restriction has been installed successfully.
    from PIL import Image, ImageOps, ImageMode, ImagePalette, ImageColor, ExifTags, PngImagePlugin, JpegImagePlugin, GifImagePlugin, WebPImagePlugin, BmpImagePlugin, TiffImagePlugin
    # Image.open always runs Pillow's standard preinit (including its PPM
    # module), even with an explicit format list. Complete that trusted import
    # phase before a frozen importer would need to reopen the PYZ archive.
    Image.preinit()
    Image.MAX_IMAGE_PIXELS = MAX_SOURCE_PIXELS
    PngImagePlugin.MAX_TEXT_CHUNK = 1024*1024
    PngImagePlugin.MAX_TEXT_MEMORY = 4*1024*1024
    warnings.simplefilter("error",Image.DecompressionBombWarning)
    try:
        restrict_worker()
        data = bytearray()
        while True:
            chunk = os.read(0,min(65536,MAX_INPUT+1-len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data)>MAX_INPUT:
                return 2
        # One explicit format at a time; no document, TIFF, SVG, EPS, or
        # external-converter fallback, regardless of extension or MIME claims.
        fmt = raster_format(data)
        selected = {b"png":"PNG",b"jpeg":"JPEG",b"gif":"GIF",b"webp":"WEBP",b"bmp":"BMP"}.get(fmt)
        if selected is None:
            return 2
        payload = remove_duplicate_exif(bytes(data)) if fmt==b"png" else bytes(data)
        with Image.open(io.BytesIO(payload),formats=(selected,)) as image:
            if not 0<image.width<=12000 or not 0<image.height<=12000 or image.width*image.height>MAX_SOURCE_PIXELS:
                return 2
            image.seek(0)
            image.thumbnail((1200,1600),Image.Resampling.LANCZOS)
            image = ImageOps.exif_transpose(image)
            if max(image.size)>MAX_EDGE or image.width*image.height>MAX_OUTPUT_PIXELS:
                return 2
            rgba = image.convert("RGBA")
            # No metadata, ICC profile, extra frames, or original container
            # bytes cross this boundary. The GUI does not parse an image file.
            packet = struct.pack("!4sII",b"BUB1",rgba.width,rgba.height)+rgba.tobytes()
        view = memoryview(packet)
        while view:
            view = view[os.write(1,view):]
        return 0
    except Exception as exc:
        # The GUI discards stderr. A manual synthetic worker probe can still
        # identify a packaging/policy failure without logging image contents.
        try:
            trace=exc.__traceback__
            frames=[]
            while trace is not None:
                frames.append(f"{trace.tb_frame.f_code.co_filename}:{trace.tb_lineno}")
                trace=trace.tb_next
            os.write(2,(type(exc).__name__+" "+" > ".join(frames[:6])+"\n").encode("ascii","backslashreplace")[:1024])
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    os._exit(main())

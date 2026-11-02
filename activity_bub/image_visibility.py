"""Display-only color filters over bounded, decoded RGBA pixels, never files.

The simulation matrices are Machado et al.'s severity-1 reference values:
https://www.inf.ufrgs.br/~oliveira/pubs_files/CVD_Simulation/CVD_Simulation.html
We use their error to *remap* color into other channels, not to display a CVD
simulation. This is an approximate, encoded-RGB convenience filter, not a
clinical correction or an exact reproduction of the linear-light model.
"""
from functools import lru_cache

from PySide6.QtGui import QImage

from .image_policy import MAX_OUTPUT_PIXELS


SIMULATIONS = {
    "protan": ((.152286, 1.052583, -.204868),
               (.114503, .786281, .099216),
               (-.003882, -.048116, 1.051998)),
    "deutan": ((.367322, .860646, -.227968),
               (.280085, .672501, .047413),
               (-.011820, .042940, .968881)),
    "tritan": ((1.255528, -.076749, -.178779),
               (-.078411, .930809, .147602),
               (.004733, .691367, .303900)),
}
# Redistribute the estimated missing-channel signal into the other channels.
SHIFTS = {
    "protan": ((0., 0., 0.), (.7, 1., 0.), (.7, 0., 1.)),
    "deutan": ((1., .7, 0.), (0., 0., 0.), (0., .7, 1.)),
    "tritan": ((1., 0., .7), (0., 1., .7), (0., 0., 0.)),
}


@lru_cache(maxsize=3)
def correction_matrix(vision):
    simulation, shift = SIMULATIONS[vision], SHIFTS[vision]
    # I + shift * (I - simulation); preserve signed error until the final
    # native conversion clamps RGB. Clamping the intermediate loses detail.
    matrix = []
    for row in range(3):
        for col in range(3):
            error = sum(shift[row][k] * (float(k == col)-simulation[k][col]) for k in range(3))
            matrix.append(float(row == col) + error)
        matrix.append(0.)
    return tuple(matrix)


def filter_image(image, prefs):
    """Return filtered pixels without changing the input, alpha or dimensions.

    Callers downscale before filtering and retain the unmodified source cache.
    Only the fixed raw-pixel decoder is used here; containers, metadata and ICC
    profiles stay on the other side of the existing restricted-worker boundary.
    """
    if not isinstance(image, QImage):
        raise TypeError("Visibility filters require decoded QImage pixels")
    if image.isNull():
        return QImage()
    if image.width()*image.height() > MAX_OUTPUT_PIXELS:
        raise ValueError("Visibility filter pixel limit exceeded")
    vision, contrast = prefs.image_signature
    if (vision, contrast) == ("standard", "theme"):
        return image
    from PIL import Image
    source = image.convertToFormat(QImage.Format.Format_RGBA8888)
    raw = Image.frombytes("RGBA", (source.width(), source.height()), bytes(source.constBits()),
                          "raw", "RGBA", source.bytesPerLine(), 1)
    rgb = raw.convert("RGB")
    if vision in SIMULATIONS:
        rgb = rgb.convert("RGB", correction_matrix(vision))
    elif vision == "mono":
        rgb = rgb.convert("L").convert("RGB")
    elif vision == "warm":
        rgb = rgb.point([round(i*factor) for factor in (1., .83, .55) for i in range(256)])
    if contrast in ("dark", "light"):
        # The same contrast curve for both themes; never invert a photograph.
        curve = [max(0, min(255, round((i-128)*1.25+128))) for i in range(256)]
        rgb = rgb.point(curve*3)
    rgb.putalpha(raw.getchannel("A"))
    data = rgb.tobytes("raw", "RGBA")
    # Own the buffer before local Python/Pillow objects are released.
    result = QImage(data, source.width(), source.height(), source.width()*4,
                    QImage.Format.Format_RGBA8888).copy()
    result.setDevicePixelRatio(image.devicePixelRatio())
    return result

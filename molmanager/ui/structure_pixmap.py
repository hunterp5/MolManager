"""Convert structure render PNG bytes to table-sized QPixmaps."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap


def pixmap_from_structure_render_png(
    png_bytes: bytes,
    display_w: int,
    display_h: int,
) -> QPixmap:
    """Decode a render PNG and scale down to the display size when larger than target."""
    pm = QPixmap.fromImage(QImage.fromData(png_bytes))
    if pm.isNull():
        return pm
    dw, dh = int(display_w), int(display_h)
    if pm.width() > dw or pm.height() > dh:
        pm = pm.scaled(dw, dh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return pm

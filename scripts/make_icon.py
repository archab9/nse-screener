"""Generate the app icon as a multi-size .ico.

    python scripts/make_icon.py

Drawn in code so there is no binary asset to keep in sync with the in-app tray icon, and
so it can be regenerated on any machine. Ascending bars read as a screener at 16px, where
lettering turns to mush.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "nse-screener.ico"

BACKGROUND = "#1b5e20"
BARS = ["#a5d6a7", "#66bb6a", "#ffffff"]


def draw(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    radius = size * 0.18
    painter.setBrush(QColor(BACKGROUND))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    # Three ascending bars, inset with even gutters.
    margin = size * 0.20
    usable = size - margin * 2
    bar_width = usable / 4.2
    gap = (usable - bar_width * 3) / 2
    heights = [0.34, 0.60, 0.86]

    for i, (colour, factor) in enumerate(zip(BARS, heights)):
        bar_height = usable * factor
        x = margin + i * (bar_width + gap)
        y = size - margin - bar_height
        painter.setBrush(QColor(colour))
        painter.drawRoundedRect(
            QRectF(x, y, bar_width, bar_height), bar_width * 0.22, bar_width * 0.22
        )

    painter.end()
    return pixmap


def main() -> int:
    # The QApplication must stay alive for as long as any QPixmap exists. Dropping the
    # reference here crashed the interpreter outright (0xC0000409) rather than raising.
    app = QApplication.instance() or QApplication(sys.argv)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pixmap = draw(256)
    if not pixmap.save(str(OUT), "ICO"):
        print(f"Failed to write {OUT}", file=sys.stderr)
        return 1

    print(f"Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size} bytes)")
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

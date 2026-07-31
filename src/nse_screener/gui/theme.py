"""Dark theme.

Applied explicitly rather than inherited from Windows, so the app looks the same either
way. That matters here because several cells set a background colour directly: against a
light default those looked fine, but under a dark system theme the light-coloured
highlights sat behind light text and became unreadable.

Rule for anything added later: whenever a cell sets a background, set its foreground too.
Relying on the theme's default text colour is what broke.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette

# Surfaces
WINDOW = "#1e1f22"
BASE = "#252629"
ALT_BASE = "#2b2c30"
BORDER = "#3a3b40"

# Text
TEXT = "#e6e6e6"
MUTED = "#a8a8a8"
BRIGHT = "#ffffff"

# Accents, all chosen to carry light text
GREEN = "#1b5e20"
GREEN_SOFT = "#20402a"
GREEN_TEXT = "#8bc98f"
AMBER = "#4a3b1a"
AMBER_TEXT = "#ffcc66"
RED = "#5c1f1f"
RED_TEXT = "#ff8a80"
BLUE = "#1b3a5c"
PURPLE = "#3a2a4d"
PURPLE_TEXT = "#ce93d8"

# Tier colours, lightened so they read on a dark row.
TIER_TEXT = {
    "ELITE COMPOUNDER": "#81c784",
    "QUALITY GROWER": "#a5d6a7",
    "WATCHLIST": "#ffb74d",
    "EXCLUDED": "#9e9e9e",
}

# Row highlights: dark fills with an explicit light foreground.
LEADER_BG = QColor(GREEN_SOFT)
LEADER_FG = QColor(GREEN_TEXT)
UNRESOLVED_BG = QColor(PURPLE)
UNRESOLVED_FG = QColor(PURPLE_TEXT)
THIN_FG = QColor(MUTED)
MEDAL_BG = (QColor("#4a3f1a"), QColor("#3a3a3f"), QColor("#43301f"))

BANNER = {
    "error": f"background:{RED}; color:{BRIGHT}; padding:7px; border-radius:3px;",
    "warning": f"background:{AMBER}; color:{AMBER_TEXT}; padding:7px; border-radius:3px;",
    "info": f"background:{BLUE}; color:{BRIGHT}; padding:7px; border-radius:3px;",
    "good": f"background:{GREEN}; color:{BRIGHT}; padding:7px; border-radius:3px;",
}

MUTED_LABEL = f"color:{MUTED}; padding:2px;"
HINT_LABEL = f"color:{GREEN_TEXT}; font-size:11px; padding:2px;"

STYLESHEET = f"""
QWidget {{ background-color: {WINDOW}; color: {TEXT}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; }}
QTabBar::tab {{
    background: {BASE}; color: {MUTED};
    padding: 7px 14px; border: 1px solid {BORDER}; border-bottom: none;
}}
QTabBar::tab:selected {{ background: {GREEN}; color: {BRIGHT}; font-weight: bold; }}
QTableWidget {{
    background-color: {BASE}; alternate-background-color: {ALT_BASE};
    color: {TEXT}; gridline-color: {BORDER}; selection-background-color: #2f5d8a;
    selection-color: {BRIGHT};
}}
QHeaderView::section {{
    background-color: {ALT_BASE}; color: {TEXT};
    padding: 5px; border: 1px solid {BORDER}; font-weight: bold;
}}
QTextEdit, QPlainTextEdit, QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
    background-color: {BASE}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 3px; padding: 3px;
}}
QComboBox QAbstractItemView {{
    background-color: {BASE}; color: {TEXT}; selection-background-color: {GREEN};
}}
QPushButton {{
    background-color: {ALT_BASE}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 3px; padding: 5px 11px;
}}
QPushButton:hover {{ background-color: #35363b; }}
QPushButton:disabled {{ background-color: #2a2a2d; color: #6b6b6b; }}
QGroupBox {{
    border: 1px solid {BORDER}; border-radius: 4px; margin-top: 9px; padding-top: 7px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 9px; color: {GREEN_TEXT}; }}
QCheckBox {{ color: {TEXT}; }}
QProgressBar {{
    border: 1px solid {BORDER}; border-radius: 3px; text-align: center;
    background-color: {BASE}; color: {TEXT};
}}
QProgressBar::chunk {{ background-color: {GREEN}; }}
QSplitter::handle {{ background-color: {BORDER}; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: {BASE}; }}
QScrollBar::handle {{ background: #4a4b52; border-radius: 4px; }}
QToolTip {{
    background-color: {ALT_BASE}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px;
}}
"""


def apply(app) -> None:
    """Install the dark palette and stylesheet on a QApplication."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(WINDOW))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(BASE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(ALT_BASE))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(ALT_BASE))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(ALT_BASE))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2f5d8a"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(BRIGHT))
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#6b6b6b")
    )
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)

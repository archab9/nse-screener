"""Sector Ranks tab - every sector and subsector, ranked on market returns.

One row per group with its median share price return over 6 months, 1, 3 and 5 years,
each showing the group's rank on that horizon, and an overall strength score.

Sorted strongest first. Strength is the mean of the group's percentile position across the
horizons it reports, 0-100 where 100 is the best group in the market - a position rather
than a raw number, because +40% means very different things over six months and five
years. Horizons are equally weighted; any other weighting would be an unbacked judgement.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nse_screener.leaderboard import HORIZONS, Leaderboards, build_leaderboards

COLUMNS = ["Rank", "Name", "Level", "Companies"] + [
    f"{label} return" for _key, label in HORIZONS
] + ["Strength"]

ALL, SECTORS_ONLY, SUBSECTORS_ONLY = "all", "sector", "subsector"

MEDAL_BG = (QColor("#fff8e1"), QColor("#f5f5f5"), QColor("#fbe9e7"))
STRONG_FG = QColor("#1b5e20")
WEAK_FG = QColor("#b71c1c")


class SectorRanksTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._board = Leaderboards()
        self._rows = []

        outer = QVBoxLayout(self)

        blurb = QLabel(
            "Every sector and subsector ranked on the MEDIAN share price return of its "
            "constituents. Median, not mean, so one multi-bagger cannot carry a flat "
            "group. Strength is the average percentile position across the four horizons "
            "(100 = best in market); the table sorts on it, strongest first."
        )
        blurb.setWordWrap(True)
        blurb.setStyleSheet("background:#1565c0; color:white; padding:8px; border-radius:3px;")
        outer.addWidget(blurb)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Show:"))
        self.level_combo = QComboBox()
        self.level_combo.addItem("Sectors and subsectors", ALL)
        self.level_combo.addItem("Sectors only", SECTORS_ONLY)
        self.level_combo.addItem("Subsectors only", SUBSECTORS_ONLY)
        self.level_combo.currentIndexChanged.connect(self._render)
        controls.addWidget(self.level_combo)

        self.run_button = QPushButton("Run sector / subsector tests")
        self.run_button.setMinimumHeight(30)
        self.run_button.setStyleSheet(
            "QPushButton { background:#1b5e20; color:white; font-weight:bold;"
            " border-radius:4px; padding:4px 12px; }"
            "QPushButton:hover { background:#2e7d32; }"
        )
        self.run_button.clicked.connect(self.run_tests)
        controls.addWidget(self.run_button)
        controls.addStretch()
        outer.addLayout(controls)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#555; padding:2px;")
        outer.addWidget(self.status)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 55)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 85)
        outer.addWidget(self.table, stretch=1)

        self._render()

    # ------------------------------------------------------------------- data

    def board(self) -> Leaderboards:
        return self._board

    def set_reference(self, reference) -> None:
        self._board = build_leaderboards(reference)
        self._render()

    def run_tests(self, reference=None) -> None:
        window = self.window()
        if reference is None and hasattr(window, "leadership_tab"):
            reference = window.leadership_tab.reference()
        self.set_reference(reference)
        # Medals feed the trophy on the other two tabs, so refresh them together.
        for name in ("history_tab", "watchlist_tab"):
            tab = getattr(window, name, None)
            if tab is not None:
                tab.set_board(self._board)

    def _visible(self):
        level = self.level_combo.currentData()
        if level == SECTORS_ONLY:
            return list(self._board.sectors)
        if level == SUBSECTORS_ONLY:
            return list(self._board.subsectors)
        # Interleaved would be meaningless; rank each level then sort the union on strength
        # so the single list still reads strongest-first.
        return sorted(
            self._board.all_groups(),
            key=lambda g: (g.strength is None, -(g.strength or 0), g.name),
        )

    # -------------------------------------------------------------- rendering

    def _render(self) -> None:
        self._rows = self._visible()
        self.table.setRowCount(len(self._rows))

        for row, group in enumerate(self._rows):
            values = [
                str(group.rank),
                f"{group.medal} {group.name}".strip(),
                group.level.capitalize(),
                str(group.constituents),
            ]
            values += [group.horizon(key).display for key, _label in HORIZONS]
            values.append(group.strength_display)

            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col == len(values) - 1 and group.strength is not None:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setForeground(STRONG_FG if group.strength >= 50 else WEAK_FG)
                if group.medal and col < 2:
                    item.setBackground(MEDAL_BG[min(row, len(MEDAL_BG) - 1)])
                self.table.setItem(row, col, item)

        if not self._board.available:
            self.status.setText(self._board.summary())
            self.status.setStyleSheet("color:#b71c1c; padding:2px;")
            return

        best = self._rows[0] if self._rows else None
        self.status.setText(
            f"{len(self._rows)} group(s) shown, strongest first"
            + (f" - leading: {best.name} (strength {best.strength_display})" if best else "")
            + f". {self._board.skipped_thin} group(s) skipped for too few constituents."
        )
        self.status.setStyleSheet("color:#555; padding:2px;")

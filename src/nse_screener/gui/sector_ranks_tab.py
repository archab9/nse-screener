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

from nse_screener.analysis_state import AnalysisState
from nse_screener.gui import theme
from nse_screener.leaderboard import HORIZONS, Leaderboards, build_leaderboards

COLUMNS = ["Rank in level", "Name", "Level", "Parent sector", "Companies"] + [
    f"{label} return" for _key, label in HORIZONS
] + ["Strength"]

ALL, SECTORS_ONLY, SUBSECTORS_ONLY = "all", "sector", "subsector"

MEDAL_BG = theme.MEDAL_BG
STRONG_FG = QColor(theme.GREEN_TEXT)
WEAK_FG = QColor(theme.RED_TEXT)
THIN_FG = theme.THIN_FG


class SectorRanksTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._board = Leaderboards()
        self._state = AnalysisState.load()
        self._universe = None
        self._parents: dict[str, str] = {}
        self._rows = []

        outer = QVBoxLayout(self)

        blurb = QLabel(
            "Every sector and subsector ranked on the MEDIAN share price return of its "
            "constituents. Median, not mean, so one multi-bagger cannot carry a flat "
            "group. Strength is the average percentile position across the four horizons "
            "(100 = best in market); the table sorts on it, strongest first. EVERY group "
            "is listed - those with too few companies are greyed and barred from medals, "
            "never hidden. 'Rank in level' is the group's place among sectors, or among "
            "subsectors, so the two sequences are independent."
        )
        blurb.setWordWrap(True)
        blurb.setStyleSheet(theme.BANNER["info"])
        outer.addWidget(blurb)

        self.refresh_banner = QLabel()
        self.refresh_banner.setWordWrap(True)
        outer.addWidget(self.refresh_banner)

        controls = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh ranking analysis")
        self.refresh_button.setMinimumHeight(32)
        self.refresh_button.setToolTip(
            "Re-read the bulk export and rebuild every sector and subsector ranking. "
            f"Prompted every {AnalysisState.interval_days()} days - the numbers only move "
            "when you refresh the export."
        )
        self.refresh_button.clicked.connect(self.refresh_analysis)
        controls.addWidget(self.refresh_button)

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
        self.status.setStyleSheet(theme.MUTED_LABEL)
        outer.addWidget(self.status)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 170)
        self.table.setColumnWidth(4, 95)
        outer.addWidget(self.table, stretch=1)

        self._render()
        self._render_refresh_banner()

    # ------------------------------------------------------------------- data

    def board(self) -> Leaderboards:
        return self._board

    def set_universe(self, universe) -> None:
        self._universe = universe

    def set_reference(self, reference) -> None:
        self._board = build_leaderboards(reference, self._universe)
        # Subsector -> its sector, so the table can show the hierarchy.
        self._render_refresh_banner()
        self._parents = {}
        if self._universe is not None:
            for c in self._universe.constituents:
                if c.subsector and c.sector:
                    self._parents.setdefault(c.subsector, c.sector)
        for row in getattr(reference, "rows", []) or []:
            if row.subsector and row.sector:
                self._parents.setdefault(row.subsector, row.sector)
        self._render()

    def refresh_analysis(self) -> None:
        """The Refresh button. Rebuilds the ranking and stamps the run."""
        self.run_tests()
        self._state.record(self._board)
        self._state.save()
        self._render_refresh_banner()

    def _render_refresh_banner(self) -> None:
        due = self._state.is_due()
        missing = sum(1 for g in self._board.all_groups() if not g.has_returns)
        text = self._state.status()

        if missing and self._board.available:
            text += (
                f"\n\n{missing} group(s) have no return figures. Returns come from the bulk "
                "Screener.in export, not from the classification: run one screen with a broad "
                "condition (e.g. Market Capitalization > 100), add the columns "
                "'Return over 6months', 'Return over 1year', 'Return over 3years', "
                "'Return over 5years', Sector, Industry, Basic Industry and the NSE code, "
                "export to CSV and drop it in data/sector_reference/."
            )
        self.refresh_banner.setText(text)
        self.refresh_banner.setStyleSheet(
            theme.BANNER["warning"] if (due or missing) else theme.BANNER["good"]
        )

    def run_tests(self, reference=None) -> None:
        window = self.window()
        if getattr(window, "_universe", None) is not None:
            self.set_universe(window._universe)
        if reference is None and hasattr(window, "leadership_tab"):
            reference = window.leadership_tab.reference()
        self.set_reference(reference)
        # Medals feed the trophy on the other two tabs, so refresh them together.
        for name in ("history_tab", "watchlist_tab"):
            tab = getattr(window, name, None)
            if tab is not None:
                tab.set_board(self._board)

    def _parent_of(self, group) -> str:
        """Which sector a subsector belongs to, from the loaded export."""
        return self._parents.get(group.name, "") if group.level == "subsector" else ""

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
                self._parent_of(group) or "-",
                f"{group.constituents}"
                + (f" ({group.with_returns} priced)" if group.with_returns < group.constituents else "")
                + (" thin" if group.thin else ""),
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
                if not group.has_returns:
                    item.setForeground(THIN_FG)
                elif group.thin:
                    item.setForeground(THIN_FG)
                    font = item.font()
                    font.setItalic(True)
                    item.setFont(font)
                elif group.medal and col < 2:
                    index = "🥇🥈🥉".find(group.medal[0]) if group.medal else -1
                    item.setBackground(MEDAL_BG[max(index, 0)])
                    item.setForeground(QColor(theme.BRIGHT))
                self.table.setItem(row, col, item)

        if not self._board.available:
            self.status.setText(self._board.summary())
            self.status.setStyleSheet(theme.BANNER["error"])
            return

        best = self._rows[0] if self._rows else None
        self.status.setText(
            f"{len(self._rows)} group(s) shown, strongest first"
            + (f" - leading: {best.name} (strength {best.strength_display})" if best else "")
            + f". {sum(1 for g in self._rows if not g.has_returns)} awaiting return data, "
            f"{self._board.skipped_thin} thin."
        )
        self.status.setStyleSheet(theme.MUTED_LABEL)

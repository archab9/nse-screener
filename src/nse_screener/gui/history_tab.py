"""Run history - the last 30 days of screening runs.

Stocks are listed most-parameters-hit first. "Hit" means a YES verdict on an active
parameter, which is deliberately not the same as the core score: four YES and three NO
(8 points) means more individual tests passed than seven PARTIAL (7 points), and this
list answers "how many did it actually pass".

Clicking a stock shows its full parameter breakdown with the underlying numbers, every
flag raised, and each earlier run it appeared in.
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from nse_screener.models import P8_ID, PARAM_IDS, PARAM_NAMES
from nse_screener.run_history import RunHistory, StockSnapshot, sort_key

COLUMNS = ["Ticker", "Params hit", "Score", "Tier", "Industry", "PEGY", "Runs", "Last seen"]

VERDICT_COLOUR = {
    "YES": "#1b5e20",
    "PARTIAL": "#e65100",
    "NO": "#b71c1c",
    "N/A": "#757575",
}
ALL_RUNS = "__all__"


class HistoryTab(QWidget):
    def __init__(self, history: RunHistory, parent=None) -> None:
        super().__init__(parent)
        self._history = history
        self._rows: list[StockSnapshot] = []

        outer = QVBoxLayout(self)

        header = QHBoxLayout()
        header.addWidget(QLabel("Show:"))
        self.run_combo = QComboBox()
        self.run_combo.currentIndexChanged.connect(self._render_table)
        header.addWidget(self.run_combo, stretch=1)

        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        outer.addLayout(header)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color:#555; padding:2px;")
        outer.addWidget(self.summary)

        note = QLabel(
            "Sorted by parameters hit (YES verdicts on active parameters), most first. "
            "Click a stock for its full breakdown."
        )
        note.setStyleSheet("color:#1b5e20; font-size:11px; padding:2px;")
        outer.addWidget(note)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._show_detail)
        splitter.addWidget(self.table)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(QFont("Consolas", 9))
        self.detail.setPlainText("Select a stock to see its detailed results.")
        splitter.addWidget(self.detail)
        splitter.setSizes([360, 460])
        outer.addWidget(splitter, stretch=1)

        self.refresh()

    # -------------------------------------------------------------------- data

    def refresh(self) -> None:
        current = self.run_combo.currentData()
        self.run_combo.blockSignals(True)
        self.run_combo.clear()
        self.run_combo.addItem(
            f"All runs in the last {self._history.retention_days()} days", ALL_RUNS
        )
        for run in self._history.recent_runs():
            self.run_combo.addItem(run.label, run.run_id)

        index = self.run_combo.findData(current)
        self.run_combo.setCurrentIndex(max(index, 0))
        self.run_combo.blockSignals(False)
        self._render_table()

    def _current_rows(self) -> list[StockSnapshot]:
        selected = self.run_combo.currentData()
        if selected in (None, ALL_RUNS):
            return self._history.aggregate()
        run = self._history.run_by_id(selected)
        return sorted(run.stocks, key=sort_key) if run else []

    # ---------------------------------------------------------------- rendering

    def _render_table(self) -> None:
        self._rows = self._current_rows()
        self.table.setRowCount(len(self._rows))

        for row, snap in enumerate(self._rows):
            appearances = len(self._history.appearances(snap.symbol))
            last_seen = ""
            history = self._history.appearances(snap.symbol)
            if history:
                last_seen = history[0][0].run_date.strftime("%d %b")

            values = [
                snap.symbol,
                snap.hit_display,
                snap.score_display,
                snap.tier,
                snap.industry or "-",
                f"{snap.pegy:.2f}" if snap.pegy is not None else "-",
                str(appearances),
                last_seen,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col == 1:
                    item.setForeground(QColor("#1b5e20"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(row, col, item)

        runs = len(self._history.runs)
        if runs == 0:
            self.summary.setText(
                "No runs recorded yet. Press Generate Results on the Screener tab and the "
                "run is saved here automatically."
            )
        else:
            oldest = min(r.run_date for r in self._history.runs)
            newest = max(r.run_date for r in self._history.runs)
            self.summary.setText(
                f"{runs} run(s) recorded, {oldest:%d %b %Y} to {newest:%d %b %Y}. "
                f"{len(self._rows)} stock(s) in view."
            )

        if self._rows:
            self.table.selectRow(0)
        else:
            self.detail.setPlainText("Nothing recorded for this selection.")

    def _show_detail(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if len(rows) != 1:
            return
        row = rows.pop()
        if 0 <= row < len(self._rows):
            self.detail.setPlainText(self._format_detail(self._rows[row]))

    def _format_detail(self, snap: StockSnapshot) -> str:
        lines = [
            "=" * 84,
            f"{snap.symbol} - {snap.name}",
            f"{snap.industry or 'industry unknown'}",
            f"{snap.tier}  |  {snap.score_display} ({snap.pct_of_max:.0f}% of active max)"
            f"  |  parameters hit: {snap.yes_count}"
            f"  (partial {snap.partial_count}, no {snap.no_count}, n/a {snap.unknown_count})",
            f"Sector tailwind: {'Yes' if snap.sector_tailwind else 'No'}"
            + (f" ({snap.tailwind_sector})" if snap.tailwind_sector else "")
            + (f"   |   PEGY {snap.pegy:.2f}" if snap.pegy is not None else "")
            + (f"   |   PB {snap.pb:.2f}" if snap.pb is not None else ""),
            "=" * 84,
        ]

        for pid in list(PARAM_IDS) + [P8_ID]:
            param = snap.params.get(pid)
            if param is None:
                continue
            suffix = "  (reported separately, never scored)" if pid == P8_ID else ""
            lines.append(f"\n{pid} {PARAM_NAMES.get(pid, pid)}: {param.verdict}{suffix}")
            if param.detail:
                lines.append(f"    {param.detail}")
            for key, value in param.evidence.items():
                lines.append(f"      - {key}: {_evidence(value)}")

        lines.append("\nFlags raised:")
        lines.extend(f"    {flag}" for flag in snap.flags) if snap.flags else lines.append("    none")

        appearances = self._history.appearances(snap.symbol)
        lines.append(f"\nAppeared in {len(appearances)} run(s) in the last "
                     f"{self._history.retention_days()} days:")
        for run, seen in appearances[:15]:
            lines.append(
                f"    {run.run_date:%d %b %Y}  {seen.score_display:>7}  "
                f"{seen.yes_count} hit  {seen.tier}"
            )
        return "\n".join(lines)


def _evidence(value) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k} {v}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return "n/a" if value is None else str(value)

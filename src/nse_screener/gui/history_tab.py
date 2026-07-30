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

from nse_screener.display import BADGE_LEGEND, format_snapshot_detail, parameter_badges
from nse_screener.run_history import RunHistory, StockSnapshot, sort_key

COLUMNS = [
    "Ticker", "Cap", "Params hit", "Parameters", "Score", "Tier",
    "Peer rank", "Biggest positive", "Runs", "Last seen",
]

ALL_RUNS = "__all__"
LEADER_BG = QColor("#c8e6c9")


def _top_quartile(snap) -> bool:
    """Highlight when the stock sits in the best quartile of its subsector on the primary
    metric. The old rule was top-3 by market cap, which left almost every row unmarked."""
    data = (snap.peer_ranks or {}).get("Price return 1y")
    if not data or not data.get("sub_rank") or (data.get("sub_total") or 0) < 2:
        return False
    return (data["sub_rank"] - 1) / (data["sub_total"] - 1) * 100.0 <= 25.0


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
            "Click a stock for its full breakdown.   " + BADGE_LEGEND
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
            history = self._history.appearances(snap.symbol)
            last_seen = history[0][0].run_date.strftime("%d %b") if history else ""
            values = [
                snap.symbol,
                snap.cap_category or "-",
                snap.hit_display,
                parameter_badges(snap.verdicts, snap.active_toggles),
                snap.score_display,
                snap.tier,
                snap.peer_summary or "-",
                snap.headline_positive or "-",
                str(len(history)),
                last_seen,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col == 2:
                    item.setForeground(QColor("#1b5e20"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if _top_quartile(snap):
                    item.setBackground(LEADER_BG)
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
            # Refresh the pane explicitly. selectRow emits nothing when row 0 is already
            # selected, which left the previous stock's detail on screen beside a
            # freshly rendered table - stale numbers next to a buy decision.
            self._show_detail(force_row=0)
        else:
            self.detail.setPlainText("Nothing recorded for this selection.")

    def _show_detail(self, force_row: int | None = None) -> None:
        if force_row is not None:
            row = force_row
        else:
            rows = {i.row() for i in self.table.selectedIndexes()}
            if len(rows) != 1:
                return
            row = rows.pop()
        if 0 <= row < len(self._rows):
            self.detail.setPlainText(self._format_detail(self._rows[row]))

    def _format_detail(self, snap: StockSnapshot) -> str:
        return format_snapshot_detail(
            snap, self._history.appearances(snap.symbol), self._history.retention_days()
        )

"""Watchlist tab.

Sorted the same way History is - most parameters hit first - using each stock's latest
recorded run. A watchlist entry that has never been through a run shows no scores rather
than a misleading zero.

Clicking a stock shows the same full detail History does: pass/fail per parameter, the
biggest positive, the latest concall split into positives and negatives, and every
parameter's underlying numbers.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
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
from nse_screener.watchlist import Watchlist, WatchState

COLUMNS = [
    "Symbol", "Cap", "Params hit", "Parameters", "Score", "Tier",
    "Peer rank", "Biggest positive", "Note", "Added",
]
COL_NOTE = 8
LEADER_BG = QColor("#c8e6c9")


def _top_quartile(snap) -> bool:
    """Highlight when the stock sits in the best quartile of its subsector on the primary
    metric. The old rule was top-3 by market cap, which left almost every row unmarked."""
    data = (snap.peer_ranks or {}).get("Price return 1y")
    if not data or not data.get("sub_rank") or (data.get("sub_total") or 0) < 2:
        return False
    return (data["sub_rank"] - 1) / (data["sub_total"] - 1) * 100.0 <= 25.0


class WatchlistTab(QWidget):
    changed = pyqtSignal()
    run_requested = pyqtSignal(list)   # symbols to screen

    def __init__(self, watchlist: Watchlist, runs: RunHistory, parent=None) -> None:
        super().__init__(parent)
        self._watchlist = watchlist
        self._runs = runs
        self._rows: list[tuple[str, StockSnapshot | None]] = []
        self._loading = False

        outer = QVBoxLayout(self)

        header = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        header.addWidget(self.summary, stretch=1)

        self.run_button = QPushButton("Run filter on entire watchlist")
        self.run_button.setMinimumHeight(32)
        self.run_button.setStyleSheet(
            "QPushButton { background:#1b5e20; color:white; font-weight:bold; border-radius:4px;"
            " padding:4px 12px; }"
            "QPushButton:hover { background:#2e7d32; }"
            "QPushButton:disabled { background:#9e9e9e; }"
        )
        self.run_button.clicked.connect(self._run_watchlist)
        header.addWidget(self.run_button)

        remove = QPushButton("Remove selected")
        remove.clicked.connect(self._remove_selected)
        header.addWidget(remove)
        outer.addLayout(header)

        note = QLabel(
            "Sorted by parameters hit in the latest recorded run, most first.   " + BADGE_LEGEND
        )
        note.setStyleSheet("color:#1b5e20; font-size:11px; padding:2px;")
        outer.addWidget(note)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(2, 190)
        self.table.setColumnWidth(5, 210)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._show_detail)
        splitter.addWidget(self.table)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(QFont("Consolas", 9))
        splitter.addWidget(self.detail)
        splitter.setSizes([340, 470])
        outer.addWidget(splitter, stretch=1)

        self.removed_label = QLabel()
        self.removed_label.setWordWrap(True)
        self.removed_label.setStyleSheet("color:#666;")
        outer.addWidget(self.removed_label)

        restore = QPushButton("Clear all 'removed' marks")
        restore.clicked.connect(self._clear_removed)
        outer.addWidget(restore)

        self.refresh()

    # --------------------------------------------------------------------- data

    def symbols(self) -> list[str]:
        return [entry.symbol for entry in self._watchlist.watching()]

    def _latest_snapshot(self, symbol: str) -> StockSnapshot | None:
        appearances = self._runs.appearances(symbol)
        return appearances[0][1] if appearances else None

    def _build_rows(self) -> list[tuple[str, StockSnapshot | None]]:
        rows = [(s, self._latest_snapshot(s)) for s in self.symbols()]
        # Never-screened entries sort last; the rest use the History sort key so the two
        # tabs order identically.
        return sorted(
            rows,
            key=lambda r: (r[1] is None, sort_key(r[1]) if r[1] else (0, 0, 0, r[0])),
        )

    # ---------------------------------------------------------------- rendering

    def refresh(self) -> None:
        self._loading = True
        self._rows = self._build_rows()
        self.table.setRowCount(len(self._rows))

        for row, (symbol, snap) in enumerate(self._rows):
            entry = self._watchlist.entries.get(symbol)
            values = [
                symbol,
                (snap.cap_category if snap else "") or "-",
                snap.hit_display if snap else "not screened",
                parameter_badges(snap.verdicts, snap.active_toggles) if snap else "-",
                snap.score_display if snap else "-",
                snap.tier if snap else "-",
                (snap.peer_summary if snap else "") or "-",
                (snap.headline_positive if snap else "") or "-",
                entry.note if entry else "",
                entry.updated if entry else "",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col != COL_NOTE:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col == 2 and snap:
                    item.setForeground(QColor("#1b5e20"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if snap and _top_quartile(snap):
                    item.setBackground(LEADER_BG)
                self.table.setItem(row, col, item)

        self.table.resizeRowsToContents()

        screened = sum(1 for _s, snap in self._rows if snap)
        self.summary.setText(
            f"{len(self._rows)} stock(s) in the watchlist, {screened} with a recorded run."
            if self._rows
            else "Watchlist is empty. Add stocks from the Screener tab."
        )
        self.run_button.setEnabled(bool(self._rows))

        removed = sorted(self._watchlist.removed_symbols())
        self.removed_label.setText(
            f"Removed ({len(removed)}), skipped on future runs: {', '.join(removed)}"
            if removed
            else "No stocks marked removed."
        )
        self._loading = False

        if self._rows:
            self.table.selectRow(0)
            # selectRow emits nothing when row 0 is already selected, which would leave
            # the previous stock's detail on screen next to a refreshed table.
            self._show_detail(force_row=0)
        else:
            self.detail.setPlainText("Select a stock to see its detailed results.")

    def _show_detail(self, force_row: int | None = None) -> None:
        if force_row is not None:
            row = force_row
        else:
            rows = {i.row() for i in self.table.selectedIndexes()}
            if len(rows) != 1:
                return
            row = rows.pop()
        if not (0 <= row < len(self._rows)):
            return
        symbol, snap = self._rows[row]
        if snap is None:
            self.detail.setPlainText(
                f"{symbol} has not been through a screening run yet.\n\n"
                f"Press 'Run filter on entire watchlist' above, or add it to a run on the "
                f"Screener tab, and its full results will appear here."
            )
            return
        self.detail.setPlainText(
            format_snapshot_detail(
                snap, self._runs.appearances(symbol), self._runs.retention_days()
            )
        )

    # ------------------------------------------------------------------ actions

    def _run_watchlist(self) -> None:
        symbols = self.symbols()
        if symbols:
            self.run_requested.emit(symbols)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != COL_NOTE:
            return
        symbol_item = self.table.item(item.row(), 0)
        if symbol_item:
            self._watchlist.set_note(symbol_item.text(), item.text())
            self._watchlist.save()
            self.changed.emit()

    def _remove_selected(self) -> None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        for row in rows:
            symbol_item = self.table.item(row, 0)
            if symbol_item:
                self._watchlist.set_state(symbol_item.text(), WatchState.REMOVED)
        if rows:
            self._watchlist.save()
            self.refresh()
            self.changed.emit()

    def _clear_removed(self) -> None:
        for symbol in list(self._watchlist.removed_symbols()):
            self._watchlist.set_state(symbol, WatchState.NONE)
        self._watchlist.save()
        self.refresh()
        self.changed.emit()

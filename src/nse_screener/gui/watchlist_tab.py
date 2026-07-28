"""Watchlist tab.

Shows everything explicitly added, with the tier and score captured at the time it was
added, plus a free-text note. Removed symbols are listed separately so a past "no" can be
undone without hunting for the stock in a new run.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nse_screener.watchlist import Watchlist, WatchState

COLUMNS = ["Symbol", "Tier when added", "Score", "Updated", "Note (double-click to edit)"]


class WatchlistTab(QWidget):
    changed = pyqtSignal()

    def __init__(self, watchlist: Watchlist, parent=None) -> None:
        super().__init__(parent)
        self._watchlist = watchlist
        self._loading = False

        outer = QVBoxLayout(self)

        header = QHBoxLayout()
        self.summary = QLabel()
        header.addWidget(self.summary, stretch=1)

        remove = QPushButton("Remove selected from watchlist")
        remove.clicked.connect(self._remove_selected)
        header.addWidget(remove)
        outer.addLayout(header)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(
            len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch
        )
        self.table.itemChanged.connect(self._on_item_changed)
        outer.addWidget(self.table, stretch=1)

        self.removed_label = QLabel()
        self.removed_label.setWordWrap(True)
        self.removed_label.setStyleSheet("color:#666;")
        outer.addWidget(self.removed_label)

        restore = QPushButton("Clear all 'removed' marks")
        restore.clicked.connect(self._clear_removed)
        outer.addWidget(restore)

        self.refresh()

    def refresh(self) -> None:
        self._loading = True
        entries = self._watchlist.watching()
        self.table.setRowCount(len(entries))

        for row, entry in enumerate(entries):
            values = [
                entry.symbol,
                entry.tier_when_added or "-",
                entry.score_when_added or "-",
                entry.updated or "-",
                entry.note,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col < len(COLUMNS) - 1:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, item)

        self.summary.setText(
            f"{len(entries)} stock(s) in the watchlist."
            if entries
            else "Watchlist is empty. Add stocks from the Screener tab."
        )

        removed = sorted(self._watchlist.removed_symbols())
        self.removed_label.setText(
            f"Removed ({len(removed)}), skipped on future runs: {', '.join(removed)}"
            if removed
            else "No stocks marked removed."
        )
        self._loading = False

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != len(COLUMNS) - 1:
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

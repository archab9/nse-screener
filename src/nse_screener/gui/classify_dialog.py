"""Manually classify a stock whose symbol did not match the sector reference export.

Reached by double-clicking a stock's Industry cell. Needed most for typed and text-file
entries, which carry a symbol and nothing else - but the Chartink CSV carries no
classification either, so any stock can land here.

Industry choices are drawn from the loaded reference data so a hand-set Industry matches
one that breadth is actually computed for. A free-typed Industry that exists nowhere in
the reference would never be leadership-aligned, which would look like a bug.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from nse_screener.classification import ClassificationStore
from nse_screener.sector_reference import SectorReference


class ClassifyDialog(QDialog):
    def __init__(
        self,
        symbol: str,
        store: ClassificationStore,
        reference: SectorReference | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._symbol = symbol
        self._store = store
        self._reference = reference

        self.setWindowTitle(f"Classify {symbol}")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        current = store.get(symbol)
        blurb = QLabel(
            f"{symbol} is currently: {current.source.label}"
            + (f" ({current.industry})" if current.industry else "")
            + ".\n\nPick the Industry it belongs to. Choices come from the loaded sector "
            "reference data, so breadth is actually computed for whatever you select."
        )
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        form = QFormLayout()
        self.industry = QComboBox()
        self.industry.setEditable(False)

        pairs = sorted(
            {(row.sector, row.industry) for row in (reference.rows if reference else []) if row.industry}
        )
        if not pairs:
            self.industry.addItem("(no reference data loaded)", None)
            self.industry.setEnabled(False)
        else:
            for sector, industry in pairs:
                self.industry.addItem(f"{industry}   -   {sector}" if sector else industry,
                                      (sector, industry))
            if current.industry:
                index = next(
                    (i for i in range(self.industry.count())
                     if (self.industry.itemData(i) or ("", ""))[1] == current.industry),
                    -1,
                )
                if index >= 0:
                    self.industry.setCurrentIndex(index)
        form.addRow("Industry", self.industry)
        layout.addLayout(form)

        clear = QPushButton("Clear manual classification")
        clear.clicked.connect(self._clear)
        layout.addWidget(clear)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply(self) -> None:
        data = self.industry.currentData()
        if not data:
            self.reject()
            return
        sector, industry = data
        basic = ""
        if self._reference:
            row = next(
                (r for r in self._reference.rows if r.industry == industry and r.basic_industry),
                None,
            )
            basic = row.basic_industry if row else ""
        self._store.set_override(self._symbol, sector, industry, basic)
        self._store.save()
        self.accept()

    def _clear(self) -> None:
        self._store.clear_override(self._symbol)
        self._store.save()
        self.accept()

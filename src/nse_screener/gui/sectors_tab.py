"""Sector tailwind tab.

Shows the five configured tailwind sectors, their rationale, how stale the review is, and
which of this run's stocks fall into each - separating plain members from genuine sector
leaders (top-3 by market cap in Screener.in's own industry table).

The banner at the top is deliberate: this list is a human judgement stored in config, not
a forecast the app produced. Presenting it without that caveat would imply the screener
knows something it does not.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QGroupBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from nse_screener.sectors import build_sector_views, excluded_sectors, review_status

LEADER_GREEN = "#1b5e20"


class SectorsTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)

        self.caveat = QLabel(
            "These sectors are a human 3-year macro judgement recorded in "
            "config/sector_map.json - the app does not derive or forecast them. What it "
            "does verify is membership plus a top-3-by-market-cap rank from Screener.in's "
            "own industry tables. Only stocks meeting BOTH are marked as leaders."
        )
        self.caveat.setWordWrap(True)
        self.caveat.setStyleSheet(
            "background:#1565c0; color:white; padding:8px; border-radius:3px;"
        )
        outer.addWidget(self.caveat)

        self.review = QLabel()
        self.review.setWordWrap(True)
        outer.addWidget(self.review)

        area = QScrollArea()
        area.setWidgetResizable(True)
        self._inner = QWidget()
        self._layout = QVBoxLayout(self._inner)
        area.setWidget(self._inner)
        outer.addWidget(area, stretch=1)

        self.refresh([])

    def refresh(self, stocks) -> None:
        message, stale = review_status()
        self.review.setText(message)
        self.review.setStyleSheet(
            "background:#ef6c00; color:white; padding:7px; border-radius:3px;"
            if stale
            else "color:#555; padding:4px;"
        )

        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for view in build_sector_views(stocks):
            self._layout.addWidget(self._build_card(view))

        for name, reason in excluded_sectors().items():
            box = QGroupBox(f"{name} - deliberately excluded")
            layout = QVBoxLayout(box)
            label = QLabel(reason)
            label.setWordWrap(True)
            label.setStyleSheet("color:#666;")
            layout.addWidget(label)
            self._layout.addWidget(box)

        self._layout.addStretch()

    def _build_card(self, view) -> QGroupBox:
        box = QGroupBox(view.name)
        layout = QVBoxLayout(box)

        if view.rationale:
            rationale = QLabel(view.rationale)
            rationale.setWordWrap(True)
            rationale.setStyleSheet("color:#444;")
            layout.addWidget(rationale)

        if view.leaders:
            leaders = QLabel("Sector leaders in this run: " + ", ".join(view.leaders))
            leaders.setWordWrap(True)
            leaders.setStyleSheet(f"color:{LEADER_GREEN}; font-weight:bold;")
            layout.addWidget(leaders)

        others = [s for s in view.matched if s not in view.leaders]
        if others:
            rest = QLabel("Also in this sector (not top-3): " + ", ".join(others))
            rest.setWordWrap(True)
            rest.setStyleSheet("color:#555;")
            layout.addWidget(rest)

        if not view.matched:
            empty = QLabel("No stocks from this run.")
            empty.setStyleSheet("color:#999; font-style:italic;")
            layout.addWidget(empty)

        keywords = QLabel("Matched on: " + ", ".join(view.keywords))
        keywords.setWordWrap(True)
        keywords.setStyleSheet("color:#999; font-size:11px;")
        layout.addWidget(keywords)
        return box

"""Sector Leadership panel.

Breadth of fundamental improvement per Industry, with the four conditions toggleable and
a manual Auto / Force Yes / Force No per Industry.

Wording is load-bearing here. Every status string comes from breadth.STATUS_LABELS, which
says "Leadership-aligned (early signal)" - a measurement of what is already visible in the
financials, never a claim about what will happen next.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nse_screener.breadth import (
    CONDITION_KEYS,
    CONDITION_LABELS,
    STATUS_LABELS,
    IndustryBreadth,
    Override,
    Status,
    apply_trend,
    compute_breadth,
    enabled_conditions,
)
from nse_screener.config import resolve_path, save_overrides, settings
from nse_screener.sector_history import SectorHistory, quarter_label
from nse_screener.sector_reference import SectorReferenceError, load_sector_reference

COLUMNS = ["Sector", "Industry", "Breadth %", "Trend", "Status", "Companies", "Override"]

ALIGNED_BG = QColor("#c8e6c9")
WATCH_BG = QColor("#fff3e0")
THIN_FG = QColor("#9e9e9e")


class SectorLeadershipTab(QWidget):
    changed = pyqtSignal()

    def __init__(self, history: SectorHistory, parent=None) -> None:
        super().__init__(parent)
        self._history = history
        self._reference = None
        self._entries: list[IndustryBreadth] = []
        self._loading = False

        outer = QVBoxLayout(self)

        caveat = QLabel(
            "Breadth measures how many companies in an Industry ALREADY show simultaneous "
            "fundamental improvement - the earliest confirmable sign of a re-rating "
            "under way. It is not a prediction, and it never affects any stock's P1-P7 "
            "score or tier."
        )
        caveat.setWordWrap(True)
        caveat.setStyleSheet("background:#1565c0; color:white; padding:8px; border-radius:3px;")
        outer.addWidget(caveat)

        outer.addWidget(self._build_source_row())
        outer.addWidget(self._build_conditions())

        self.status_label = QLabel("No sector reference data loaded.")
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        outer.addWidget(self.table, stretch=1)

        self.try_load(silent=True)

    # ------------------------------------------------------------------- controls

    def _build_source_row(self) -> QWidget:
        box = QGroupBox("Sector reference data (bulk Screener.in CSV export)")
        layout = QVBoxLayout(box)

        hint = QLabel(
            "One market-wide Screener.in query with a broad condition (e.g. Market "
            "Capitalization > 100), with Sector / Industry / Basic Industry / growth / "
            "OPM / ROCE / NSE code columns added. Refresh quarterly. Split into several "
            "CSVs by market-cap band if the export truncates - all files in the folder "
            "are merged."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#555; font-size:11px;")
        layout.addWidget(hint)

        row = QHBoxLayout()
        self.path_label = QLabel()
        self.path_label.setStyleSheet("color:#555;")
        row.addWidget(self.path_label, stretch=1)

        choose = QPushButton("Choose folder...")
        choose.clicked.connect(self._choose_folder)
        row.addWidget(choose)

        reload_button = QPushButton("Reload")
        reload_button.clicked.connect(lambda: self.try_load(silent=False))
        row.addWidget(reload_button)

        snapshot = QPushButton("Save this quarter's snapshot")
        snapshot.clicked.connect(self._save_snapshot)
        row.addWidget(snapshot)
        layout.addLayout(row)
        return box

    def _build_conditions(self) -> QWidget:
        box = QGroupBox("Breadth conditions - a company counts only if every enabled one passes")
        layout = QVBoxLayout(box)
        self._condition_checks: dict[str, QCheckBox] = {}
        self._condition_spins: dict[str, QDoubleSpinBox] = {}
        cfg = settings()["sector_leadership"]["conditions"]

        for key in CONDITION_KEYS:
            row = QHBoxLayout()
            check = QCheckBox(CONDITION_LABELS[key])
            check.setChecked(cfg.get(key, {}).get("enabled", True))
            check.stateChanged.connect(self._on_conditions_changed)
            self._condition_checks[key] = check
            row.addWidget(check)

            if "min" in cfg.get(key, {}):
                spin = QDoubleSpinBox()
                spin.setRange(-100, 200)
                spin.setDecimals(1)
                spin.setSingleStep(0.5)
                spin.setValue(float(cfg[key]["min"]))
                spin.setSuffix(" %")
                spin.valueChanged.connect(self._on_conditions_changed)
                self._condition_spins[key] = spin
                row.addWidget(spin)

            row.addStretch()
            layout.addLayout(row)

        gate = QHBoxLayout()
        gate.addWidget(QLabel("Suppress industries with fewer than"))
        self.min_companies = QDoubleSpinBox()
        self.min_companies.setRange(1, 100)
        self.min_companies.setDecimals(0)
        self.min_companies.setValue(float(settings()["sector_leadership"]["min_companies"]))
        self.min_companies.valueChanged.connect(self._on_conditions_changed)
        gate.addWidget(self.min_companies)
        gate.addWidget(QLabel("companies"))
        gate.addStretch()
        layout.addLayout(gate)
        return box

    # -------------------------------------------------------------------- loading

    def _choose_folder(self) -> None:
        start = str(self._reference.files[0].parent) if self._reference and self._reference.files else ""
        folder = QFileDialog.getExistingDirectory(self, "Select the sector reference folder", start)
        if folder:
            overrides = {"paths": {"sector_reference_dir": folder}}
            save_overrides({**self._current_overrides(), **overrides})
            self.try_load(silent=False)

    def _current_overrides(self) -> dict:
        from nse_screener.config import load_overrides

        return load_overrides()

    def try_load(self, silent: bool = True) -> None:
        try:
            self._reference = load_sector_reference()
        except SectorReferenceError as exc:
            self._reference = None
            self.path_label.setText("no folder selected")
            self.status_label.setText(str(exc))
            self.status_label.setStyleSheet("color:#b71c1c;" if not silent else "color:#666;")
            self.table.setRowCount(0)
            self.changed.emit()
            return

        self.path_label.setText(
            f"{len(self._reference.files)} file(s), {len(self._reference.rows)} companies"
        )
        self.recompute()

    def _on_conditions_changed(self) -> None:
        if self._loading:
            return
        conditions = {}
        for key, check in self._condition_checks.items():
            entry = {"enabled": check.isChecked()}
            if key in self._condition_spins:
                entry["min"] = self._condition_spins[key].value()
            conditions[key] = entry

        overrides = self._current_overrides()
        section = overrides.setdefault("sector_leadership", {})
        section["conditions"] = conditions
        section["min_companies"] = int(self.min_companies.value())
        save_overrides(overrides)
        self.recompute()

    # ---------------------------------------------------------------- computation

    def recompute(self) -> None:
        if self._reference is None:
            return

        label = quarter_label()
        self._entries = compute_breadth(self._reference, overrides=self._history.overrides)
        apply_trend(self._entries, self._history.previous_breadth(label))
        self._render()
        self.changed.emit()

    def leadership_industries(self) -> set[str]:
        return {e.industry for e in self._entries if e.is_leadership_aligned}

    def entry_for(self, industry: str) -> IndustryBreadth | None:
        return next((e for e in self._entries if e.industry == industry), None)

    def has_reference(self) -> bool:
        return self._reference is not None

    def reference(self):
        return self._reference

    def _save_snapshot(self) -> None:
        if not self._entries:
            self.status_label.setText("Nothing to snapshot - load the reference CSV first.")
            return
        label = self._history.record_snapshot(self._entries, enabled_conditions())
        self._history.save()
        self.recompute()
        self.status_label.setText(f"Snapshot saved for {label}.")
        self.status_label.setStyleSheet("color:#2e7d32;")

    # ------------------------------------------------------------------ rendering

    def _render(self) -> None:
        self._loading = True
        self.table.setRowCount(len(self._entries))
        label = quarter_label()
        has_history = self._history.has_history(label)

        for row, entry in enumerate(self._entries):
            thin = entry.status is Status.INSUFFICIENT_SAMPLE
            trend_text = "-" if not has_history else entry.trend.label
            if entry.rising_into_leadership:
                trend_text += " (rising)"

            values = [
                entry.sector or "-",
                entry.industry,
                entry.breadth_display,
                trend_text,
                entry.status_label,
                str(entry.denominator),
                "",
            ]
            for col, value in enumerate(values[:-1]):
                item = QTableWidgetItem(value)
                if thin:
                    item.setForeground(THIN_FG)
                    font = item.font()
                    font.setItalic(True)
                    item.setFont(font)
                elif entry.status is Status.LEADERSHIP_ALIGNED:
                    item.setBackground(ALIGNED_BG)
                elif entry.status is Status.WATCH:
                    item.setBackground(WATCH_BG)
                if col == 2 and entry.previous_pct is not None:
                    item.setToolTip(f"Previous quarter: {entry.previous_pct:.0f}%")
                if col == 5 and thin:
                    item.setToolTip(
                        f"Only {entry.denominator} companies - below the "
                        f"{int(self.min_companies.value())} needed for a meaningful percentage."
                    )
                self.table.setItem(row, col, item)

            self.table.setCellWidget(row, len(COLUMNS) - 1, self._override_widget(entry))

        aligned = sum(1 for e in self._entries if e.is_leadership_aligned)
        thin_count = sum(1 for e in self._entries if e.status is Status.INSUFFICIENT_SAMPLE)
        history_note = "" if has_history else " Trend needs a second quarter - snapshot saved this run will provide it."
        self.status_label.setText(
            f"{len(self._entries)} industries | {aligned} {STATUS_LABELS[Status.LEADERSHIP_ALIGNED]} "
            f"| {thin_count} insufficient sample.{history_note}"
        )
        self.status_label.setStyleSheet("color:#555;")
        self._loading = False

    def _override_widget(self, entry: IndustryBreadth) -> QWidget:
        combo = QComboBox()
        for option in (Override.AUTO, Override.FORCE_YES, Override.FORCE_NO):
            combo.addItem(option.label, option)
        combo.setCurrentIndex(combo.findData(entry.override))
        combo.currentIndexChanged.connect(
            lambda _i, ind=entry.industry, c=combo: self._on_override(ind, c.currentData())
        )
        return combo

    def _on_override(self, industry: str, override: Override) -> None:
        if self._loading:
            return
        self._history.set_override(industry, override)
        self._history.save()
        self.recompute()

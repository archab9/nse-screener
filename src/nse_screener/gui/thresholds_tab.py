"""Threshold editor tab.

Every editable number from thresholds.SCHEMA, grouped by parameter. Changing one writes
to config/user_thresholds.json and re-evaluates the current results in place - no
re-fetch, because the underlying company data has not changed, only the rules applied
to it.

Modified values are marked so it is always obvious what has been moved off the default.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from nse_screener.config import reset_overrides, save_overrides, settings
from nse_screener.thresholds import (
    SCHEMA,
    ThresholdField,
    build_overrides,
    current_value,
    default_value,
    validate,
)

MODIFIED_STYLE = "QDoubleSpinBox { background:#fff8e1; border:1px solid #ff9800; }"


class ThresholdsTab(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._spins: dict[tuple[str, str], QDoubleSpinBox] = {}
        self._loading = False

        outer = QVBoxLayout(self)

        header = QHBoxLayout()
        intro = QLabel(
            "Edit any threshold below. Changes re-score the current results immediately - "
            "no re-fetch. Highlighted fields differ from the shipped default."
        )
        intro.setWordWrap(True)
        header.addWidget(intro, stretch=1)

        reset = QPushButton("Reset all to defaults")
        reset.clicked.connect(self._reset_all)
        header.addWidget(reset)
        outer.addLayout(header)

        self.status = QLabel()
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget()
        layout = QVBoxLayout(inner)

        for group in SCHEMA:
            layout.addWidget(self._build_group(group))
        layout.addStretch()
        area.setWidget(inner)
        outer.addWidget(area, stretch=1)

        self._refresh_marks()

    def _build_group(self, group) -> QGroupBox:
        box = QGroupBox(f"{group.param_id} - {group.title}"
                        if group.param_id != "TIERS" else group.title)
        grid = QGridLayout(box)
        row = 0
        for f in group.fields:
            grid.addWidget(QLabel(f.label), row, 0)
            spin = self._build_spin(group.section, f)
            grid.addWidget(spin, row, 1)
            grid.addWidget(QLabel(f.unit), row, 2)
            if f.help:
                note = QLabel(f.help)
                note.setStyleSheet("color:#666; font-size:11px;")
                note.setWordWrap(True)
                grid.addWidget(note, row, 3)
            grid.setColumnStretch(3, 1)
            row += 1
        return box

    def _build_spin(self, section: str, f: ThresholdField) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(f.minimum, f.maximum)
        spin.setSingleStep(f.step)
        spin.setDecimals(f.decimals)
        try:
            spin.setValue(current_value(section, f.key))
        except KeyError:
            spin.setValue(f.minimum)
            spin.setEnabled(False)
        spin.setToolTip(f"Default: {default_value(section, f.key)} {f.unit}" if f.key else "")
        spin.valueChanged.connect(self._on_change)
        self._spins[(section, f.key)] = spin
        return spin

    def _values(self) -> dict[tuple[str, str], float]:
        return {key: spin.value() for key, spin in self._spins.items() if spin.isEnabled()}

    def _on_change(self) -> None:
        if self._loading:
            return
        values = self._values()

        problems = validate(values)
        if problems:
            self.status.setText("Not saved - " + " ".join(problems))
            self.status.setStyleSheet("color:#b71c1c;")
            return

        save_overrides(build_overrides(values))
        self._refresh_marks()
        modified = sum(
            1 for (s, k), v in values.items() if v != default_value(s, k)
        )
        self.status.setText(
            f"Saved. {modified} threshold(s) differ from default." if modified
            else "Saved. All thresholds at their defaults."
        )
        self.status.setStyleSheet("color:#2e7d32;")
        self.changed.emit()

    def _refresh_marks(self) -> None:
        for (section, key), spin in self._spins.items():
            if not spin.isEnabled():
                continue
            spin.setStyleSheet(
                MODIFIED_STYLE if spin.value() != default_value(section, key) else ""
            )

    def _reset_all(self) -> None:
        reset_overrides()
        self._loading = True
        for (section, key), spin in self._spins.items():
            if spin.isEnabled():
                spin.setValue(default_value(section, key))
        self._loading = False
        self._refresh_marks()
        self.status.setText("All thresholds reset to defaults.")
        self.status.setStyleSheet("color:#2e7d32;")
        self.changed.emit()

    def reload_from_config(self) -> None:
        self._loading = True
        for (section, key), spin in self._spins.items():
            if spin.isEnabled():
                try:
                    spin.setValue(current_value(section, key))
                except KeyError:
                    pass
        self._loading = False
        self._refresh_marks()

    def summary(self) -> str:
        active = settings()
        del active  # touch settings so a stale cache is obvious in testing
        modified = [
            f"{section}.{key}"
            for (section, key), spin in self._spins.items()
            if spin.isEnabled() and spin.value() != default_value(section, key)
        ]
        return f"{len(modified)} threshold(s) customised" if modified else "default thresholds"

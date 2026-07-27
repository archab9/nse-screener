"""Windows desktop app (spec section 10).

Design constraints taken directly from the spec:
  - A single "Generate Results" button is the ONLY trigger. No scheduler, no timer, no
    background runner.
  - The pipeline runs synchronously on that press, with a progress indicator.
  - Each of P1-P7 has its own checkbox, all ON by default. P8 gets a separate toggle,
    since it was never part of the score.
  - Toggling recomputes and re-renders the table AND every detail card immediately,
    without re-running the pipeline.
  - Banners are never suppressed: stale fundamentals, missing Kite token, non-trading day.
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

if __package__ in (None, ""):  # allow `python src/nse_screener/gui/app.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nse_screener.config import resolve_path
from nse_screener.gui.login_dialog import ScreenerLoginDialog
from nse_screener.market.kite import TokenState, build_login_url, check_token, complete_login
from nse_screener.models import P8_ID, PARAM_IDS, PARAM_NAMES, RunContext, ScoredStock, Tier
from nse_screener.pipeline import LIVE, LOCAL, PipelineResult, run_pipeline
from nse_screener.report import format_detail_card
from nse_screener.scoring import score_all, summary_line
from nse_screener.stage2.screener_client import has_credentials

TIER_COLOURS = {
    Tier.ELITE_COMPOUNDER: "#1b5e20",
    Tier.QUALITY_GROWER: "#2e7d32",
    Tier.WATCHLIST: "#e65100",
    Tier.EXCLUDED: "#757575",
}

SEVERITY_STYLE = {
    "error": "background:#b71c1c; color:white; padding:7px; border-radius:3px;",
    "warning": "background:#ef6c00; color:white; padding:7px; border-radius:3px;",
    "info": "background:#1565c0; color:white; padding:7px; border-radius:3px;",
}

COLUMNS = ["Ticker", "Sector", "Score", "Tier", "Tailwind", "PEGY", "PB", "Key flags"]


def _make_icon() -> QIcon:
    """Tray/window icon drawn in code so the .exe needs no external asset."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#1b5e20"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("white"))
    font = QFont("Segoe UI", 30, QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "S")
    painter.end()
    return QIcon(pixmap)


class ScreenerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("NSE Two-Stage Screener")
        self.resize(1350, 900)

        self._result: PipelineResult | None = None
        self._ranked: list[ScoredStock] = []
        self._checkboxes: dict[str, QCheckBox] = {}
        self._csv_path: Path | None = None

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        layout.addWidget(self._build_sources())
        layout.addWidget(self._build_controls())

        self.banner_box = QVBoxLayout()
        self.banner_box.setSpacing(4)
        layout.addLayout(self.banner_box)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status = QLabel("Press Generate Results to run the pipeline.")
        self.status.setStyleSheet("color:#555; padding:2px;")
        layout.addWidget(self.status)

        self.summary = QLabel(summary_line(self._toggles()))
        self.summary.setStyleSheet("font-weight:bold; padding:2px;")
        layout.addWidget(self.summary)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSortingEnabled(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(1, 210)
        self.table.setColumnWidth(3, 150)
        splitter.addWidget(self.table)

        self.cards = QTextEdit()
        self.cards.setReadOnly(True)
        self.cards.setFont(QFont("Consolas", 9))
        card_area = QScrollArea()
        card_area.setWidgetResizable(True)
        card_area.setWidget(self.cards)
        splitter.addWidget(card_area)
        splitter.setSizes([420, 480])
        layout.addWidget(splitter, stretch=1)

        self._build_tray()

    # ------------------------------------------------------------------ layout

    def _build_sources(self) -> QWidget:
        box = QGroupBox("Data sources")
        outer = QVBoxLayout(box)

        stage1 = QHBoxLayout()
        stage1.addWidget(QLabel("Stage 1 - Chartink CSV:"))
        self.csv_label = QLabel()
        self.csv_label.setStyleSheet("color:#555;")
        upload = QPushButton("Upload CSV...")
        upload.clicked.connect(self._choose_csv)
        stage1.addWidget(self.csv_label, stretch=1)
        stage1.addWidget(upload)
        outer.addLayout(stage1)

        stage2 = QHBoxLayout()
        stage2.addWidget(QLabel("Stage 2 - fundamentals:"))
        self.source_combo = QComboBox()
        self.source_combo.addItem("Live from Screener.in (Premium login)", LIVE)
        self.source_combo.addItem("Saved local export", LOCAL)
        self.source_combo.currentIndexChanged.connect(self._refresh_source_label)
        stage2.addWidget(self.source_combo)

        self.login_button = QPushButton("Screener.in login...")
        self.login_button.clicked.connect(self._screener_login)
        stage2.addWidget(self.login_button)

        self.login_label = QLabel()
        self.login_label.setStyleSheet("color:#555;")
        stage2.addWidget(self.login_label, stretch=1)
        outer.addLayout(stage2)

        # Default to the configured CSV if it already exists, so the app is usable
        # immediately without an upload step.
        try:
            default_csv = resolve_path("chartink_csv")
            if default_csv.exists():
                self._csv_path = default_csv
        except (KeyError, OSError):
            pass

        self._refresh_source_label()
        return box

    def _choose_csv(self) -> None:
        start = str(self._csv_path.parent) if self._csv_path else ""
        path, _filter = QFileDialog.getOpenFileName(
            self, "Select the Chartink scan export", start, "CSV files (*.csv);;All files (*)"
        )
        if path:
            self._csv_path = Path(path)
            self._refresh_source_label()

    def _screener_login(self) -> None:
        dialog = ScreenerLoginDialog(self)
        if dialog.exec():
            self._refresh_source_label()

    def _refresh_source_label(self) -> None:
        self.csv_label.setText(self._csv_path.name if self._csv_path else "none selected")
        live = self.source_combo.currentData() == LIVE
        self.login_button.setEnabled(live)
        if not live:
            self.login_label.setText("reading a saved export from disk")
        elif has_credentials():
            self.login_label.setText("credentials stored")
        else:
            self.login_label.setText("no credentials stored")

    def _build_controls(self) -> QWidget:
        box = QGroupBox("Parameters - toggling removes a parameter from the score AND the denominator")
        outer = QVBoxLayout(box)

        checks = QHBoxLayout()
        for pid in PARAM_IDS:
            cb = QCheckBox(f"{pid}  {PARAM_NAMES[pid]}")
            cb.setChecked(True)
            cb.stateChanged.connect(self._on_toggle)
            self._checkboxes[pid] = cb
            checks.addWidget(cb)
        outer.addLayout(checks)

        row = QHBoxLayout()
        p8 = QCheckBox(f"{P8_ID}  {PARAM_NAMES[P8_ID]} (reported separately, never scored)")
        p8.setChecked(True)
        p8.stateChanged.connect(self._on_toggle)
        self._checkboxes[P8_ID] = p8
        row.addWidget(p8)
        row.addStretch()

        self.run_button = QPushButton("Generate Results")
        self.run_button.setMinimumHeight(38)
        self.run_button.setMinimumWidth(190)
        self.run_button.setStyleSheet(
            "QPushButton { background:#1b5e20; color:white; font-weight:bold; border-radius:4px; }"
            "QPushButton:hover { background:#2e7d32; }"
            "QPushButton:disabled { background:#9e9e9e; }"
        )
        self.run_button.clicked.connect(self.generate)
        row.addWidget(self.run_button)
        outer.addLayout(row)
        return box

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_make_icon(), self)
        self.tray.setToolTip("NSE Two-Stage Screener")
        menu = QMenu()
        show = QAction("Show window", self)
        show.triggered.connect(self.showNormal)
        run = QAction("Generate Results", self)
        run.triggered.connect(self.generate)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(show)
        menu.addAction(run)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.showNormal()
            if reason == QSystemTrayIcon.ActivationReason.Trigger
            else None
        )
        self.tray.show()
        self.setWindowIcon(_make_icon())

    # ------------------------------------------------------------------- state

    def _toggles(self) -> dict[str, bool]:
        return {pid: cb.isChecked() for pid, cb in self._checkboxes.items() if pid in PARAM_IDS}

    def _show_p8(self) -> bool:
        cb = self._checkboxes.get(P8_ID)
        return cb.isChecked() if cb else True

    # ------------------------------------------------------------------ actions

    def generate(self) -> None:
        """The single trigger. Runs the whole pipeline synchronously."""
        if self._csv_path is None or not self._csv_path.exists():
            QMessageBox.warning(
                self,
                "No Chartink CSV",
                "Upload the Chartink scan export first - it supplies the stage-1 ticker list.",
            )
            return

        source = self.source_combo.currentData()
        if source == LIVE and not has_credentials():
            answer = QMessageBox.question(
                self,
                "Screener.in login needed",
                "Live mode needs your Screener.in credentials. Sign in now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._screener_login()
            if not has_credentials():
                return

        if not self._ensure_kite_token():
            return

        self.run_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self._clear_banners()

        def progress(message: str, pct: int) -> None:
            self.status.setText(message)
            self.progress.setValue(pct)
            QApplication.processEvents()  # keep the window responsive during the sync run

        try:
            self._result = run_pipeline(
                chartink_csv=self._csv_path, data_source=source, progress=progress
            )
        except Exception as exc:  # a crash must not leave the user without an explanation
            self._add_banner("error", f"Pipeline failed: {exc}")
            self.status.setText("Run failed.")
            return
        finally:
            self.run_button.setEnabled(True)
            self.progress.setVisible(False)

        self._render()

    def _ensure_kite_token(self) -> bool:
        """Checked on every press, per spec section 10 - prompt inline rather than fail later."""
        check = check_token()
        if check.ok or check.state in (TokenState.NO_API_KEY, TokenState.LIBRARY_MISSING):
            # Not authenticated, but that is a banner condition, not a blocker - the run
            # still produces fundamentals-based scores without live quotes.
            return True

        url = build_login_url()
        answer = QMessageBox.question(
            self,
            "Kite login required",
            f"{check.message}\n\nOpen the Zerodha login page and paste the redirect URL back?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return True  # proceed without live prices

        if url:
            webbrowser.open(url)
        redirect, ok = QInputDialog.getText(
            self, "Kite login", "Paste the full URL you were redirected to after logging in:"
        )
        if ok and redirect.strip():
            outcome = complete_login(redirect.strip())
            if not outcome.ok:
                self._add_banner("warning", outcome.message)
        return True

    def _on_toggle(self) -> None:
        """Re-score and re-render instantly. Never re-runs the pipeline."""
        self.summary.setText(summary_line(self._toggles()))
        if self._result is not None:
            self._render(rerun_banners=False)

    # ----------------------------------------------------------------- rendering

    def _render(self, rerun_banners: bool = True) -> None:
        if self._result is None:
            return
        toggles = self._toggles()
        self._ranked = score_all(self._result.stocks, toggles)

        if rerun_banners:
            self._render_banners(self._result.context)

        context = self._result.context
        as_of = context.as_of_trading_day or context.run_at
        self.status.setText(
            f"Run {context.run_at:%d %b %Y} - data as of {as_of:%d %b %Y}. "
            f"Stage 1 returned {context.stage1_count}; {len(self._ranked)} scored."
        )
        self.summary.setText(summary_line(toggles))
        self._render_table(toggles)
        self._render_cards(toggles)

    def _render_banners(self, context: RunContext) -> None:
        self._clear_banners()
        for warning in context.warnings:
            self._add_banner(warning.severity, warning.message)

    def _clear_banners(self) -> None:
        while self.banner_box.count():
            item = self.banner_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_banner(self, severity: str, message: str) -> None:
        label = QLabel(message)
        label.setWordWrap(True)
        label.setStyleSheet(SEVERITY_STYLE.get(severity, SEVERITY_STYLE["info"]))
        self.banner_box.addWidget(label)

    def _render_table(self, toggles: dict[str, bool]) -> None:
        show_p8 = self._show_p8()
        self.table.setRowCount(len(self._ranked))
        self.table.setColumnHidden(4, not show_p8)

        for row, stock in enumerate(self._ranked):
            flags = "; ".join(
                f.message for f in stock.all_flags
                if f.severity in ("risk", "positive") and (show_p8 or f.param_id != P8_ID)
            )
            values = [
                stock.symbol,
                stock.industry or "-",
                f"{stock.score_display}  ({stock.pct_of_max:.0f}%)",
                stock.tier.value,
                ("Yes" if stock.sector_tailwind else "No") if show_p8 else "",
                f"{stock.pegy:.2f}" if stock.pegy is not None else "-",
                f"{stock.pb:.2f}" if stock.pb is not None else "-",
                flags,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 3:
                    item.setForeground(QColor(TIER_COLOURS[stock.tier]))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(row, col, item)

    def _render_cards(self, toggles: dict[str, bool]) -> None:
        finalists = [s for s in self._ranked if s.tier.rank >= Tier.QUALITY_GROWER.rank]
        if not finalists:
            self.cards.setPlainText(
                "No stock reached QUALITY GROWER with the current parameter selection.\n"
                "Detail cards are generated for QUALITY GROWER and above (spec section 6)."
            )
            return
        self.cards.setPlainText(
            "\n\n".join(format_detail_card(s, toggles) for s in finalists)
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("NSE Two-Stage Screener")
    window = ScreenerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

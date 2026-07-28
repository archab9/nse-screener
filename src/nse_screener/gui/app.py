"""Windows desktop app.

Design constraints carried through from the build spec:
  - A single "Generate Results" button is the ONLY trigger. No scheduler, no timer, no
    background runner.
  - The pipeline runs synchronously on that press, with a progress indicator.
  - Each of P1-P7 has its own checkbox, all ON by default. P8 gets a separate toggle,
    since it was never part of the score.
  - Toggling recomputes and re-renders immediately, without re-running the pipeline.
  - Banners are never suppressed: stale fundamentals, missing credentials, fetch failures.

Two kinds of live update, which are not the same thing:
  - toggling a parameter only changes the arithmetic  -> re-score
  - changing a threshold changes the verdicts         -> re-evaluate against cached data
Neither ever re-fetches.
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
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nse_screener.classification import ClassificationStore, Source, rank_within_industry
from nse_screener.config import resolve_path
from nse_screener.gui.kite_dialog import KiteSettingsDialog
from nse_screener.gui.login_dialog import ScreenerLoginDialog
from nse_screener.gui.sector_leadership_tab import SectorLeadershipTab
from nse_screener.gui.sectors_tab import SectorsTab
from nse_screener.gui.thresholds_tab import ThresholdsTab
from nse_screener.gui.watchlist_tab import WatchlistTab
from nse_screener.sector_history import SectorHistory
from nse_screener.market.kite import TokenState, build_login_url, check_token, complete_login
from nse_screener.models import P8_ID, PARAM_IDS, PARAM_NAMES, RunContext, ScoredStock, Tier
from nse_screener.pipeline import LIVE, LOCAL, PipelineResult, reevaluate, run_pipeline
from nse_screener.report import format_detail_card
from nse_screener.scoring import score_all, summary_line
from nse_screener.stage1.symbols import hits_from_text, load_symbol_file
from nse_screener.stage2.screener_client import has_credentials
from nse_screener.watchlist import Watchlist, WatchState

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

LEADER_BG = QColor("#c8e6c9")   # tailwind sector AND top-3 by market cap
LEADER_FG = QColor("#1b5e20")

UNRESOLVED_BG = QColor("#f5f5f5")
UNRESOLVED_FG = QColor("#6a1b9a")

COLUMNS = [
    "Ticker", "Sector", "Industry", "Score", "Tier", "Tailwind",
    "PEGY", "PB", "Watchlist", "Description", "Key flags",
]
COL_INDUSTRY, COL_TIER, COL_TAILWIND, COL_WATCH, COL_DESC = 2, 4, 5, 8, 9

INPUT_CSV, INPUT_TXT, INPUT_MANUAL = "csv", "txt", "manual"


def _make_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#1b5e20"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("white"))
    painter.setFont(QFont("Segoe UI", 30, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "S")
    painter.end()
    return QIcon(pixmap)


class ScreenerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("NSE Two-Stage Screener")
        self.resize(1500, 950)

        self._result: PipelineResult | None = None
        self._ranked: list[ScoredStock] = []
        self._checkboxes: dict[str, QCheckBox] = {}
        self._input_path: Path | None = None
        self._watchlist = Watchlist.load()
        self._history = SectorHistory.load()
        self._classification = ClassificationStore.load()

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.tabs.addTab(self._build_screener_tab(), "Screener")

        self.leadership_tab = SectorLeadershipTab(self._history)
        self.leadership_tab.changed.connect(self._on_leadership_changed)
        self.tabs.addTab(self.leadership_tab, "Sector Leadership")

        self.thresholds_tab = ThresholdsTab()
        self.thresholds_tab.changed.connect(self._on_thresholds_changed)
        self.tabs.addTab(self.thresholds_tab, "Thresholds")

        self.watchlist_tab = WatchlistTab(self._watchlist)
        self.watchlist_tab.changed.connect(self._render_table_only)
        self.tabs.addTab(self.watchlist_tab, "Watchlist")

        self.sectors_tab = SectorsTab()
        self.tabs.addTab(self.sectors_tab, "Sectors")

        self._build_tray()

    # ------------------------------------------------------------------ screener tab

    def _build_screener_tab(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)

        layout.addWidget(self._build_sources())
        layout.addWidget(self._build_controls())

        self.banner_box = QVBoxLayout()
        self.banner_box.setSpacing(4)
        layout.addLayout(self.banner_box)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status = QLabel("Choose an input, then press Generate Results.")
        self.status.setStyleSheet("color:#555; padding:2px;")
        layout.addWidget(self.status)

        self.summary = QLabel(summary_line(self._toggles()))
        self.summary.setStyleSheet("font-weight:bold; padding:2px;")
        layout.addWidget(self.summary)

        legend = QLabel(
            "Green row = the stock's Industry is leadership-aligned (early signal) AND the "
            "stock is top-3 by core score among stocks scored here in that Industry.   "
            "Purple ticker = Sector unresolved, so it could not be evaluated at all."
        )
        legend.setWordWrap(True)
        legend.setStyleSheet("color:#1b5e20; font-size:11px; padding:2px;")
        layout.addWidget(legend)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(1, 190)
        self.table.setColumnWidth(3, 145)
        self.table.setColumnWidth(COL_DESC, 320)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        splitter.addWidget(self.table)

        self.cards = QTextEdit()
        self.cards.setReadOnly(True)
        self.cards.setFont(QFont("Consolas", 9))
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(self.cards)
        splitter.addWidget(area)
        splitter.setSizes([430, 470])
        layout.addWidget(splitter, stretch=1)
        return root

    def _build_sources(self) -> QWidget:
        box = QGroupBox("Data sources")
        outer = QVBoxLayout(box)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Stage 1 input:"))
        self.input_combo = QComboBox()
        self.input_combo.addItem("Chartink CSV export", INPUT_CSV)
        self.input_combo.addItem("Text file (one symbol per line)", INPUT_TXT)
        self.input_combo.addItem("Type or paste symbols", INPUT_MANUAL)
        self.input_combo.currentIndexChanged.connect(self._on_input_mode)
        row1.addWidget(self.input_combo)

        self.browse_button = QPushButton("Choose file...")
        self.browse_button.clicked.connect(self._choose_file)
        row1.addWidget(self.browse_button)

        self.input_label = QLabel()
        self.input_label.setStyleSheet("color:#555;")
        row1.addWidget(self.input_label, stretch=1)
        outer.addLayout(row1)

        self.manual_box = QPlainTextEdit()
        self.manual_box.setPlaceholderText(
            "RELIANCE, HAL, BEL\nor one symbol per line - commas, spaces and newlines all work"
        )
        self.manual_box.setMaximumHeight(70)
        self.manual_box.setVisible(False)
        outer.addWidget(self.manual_box)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Stage 2 fundamentals:"))
        self.source_combo = QComboBox()
        self.source_combo.addItem("Live from Screener.in (Premium login)", LIVE)
        self.source_combo.addItem("Saved local export", LOCAL)
        self.source_combo.currentIndexChanged.connect(self._refresh_source_label)
        row2.addWidget(self.source_combo)

        self.login_button = QPushButton("Screener.in login...")
        self.login_button.clicked.connect(self._screener_login)
        row2.addWidget(self.login_button)

        kite_button = QPushButton("Kite API...")
        kite_button.clicked.connect(self._kite_settings)
        row2.addWidget(kite_button)

        self.login_label = QLabel()
        self.login_label.setStyleSheet("color:#555;")
        row2.addWidget(self.login_label, stretch=1)
        outer.addLayout(row2)

        try:
            default_csv = resolve_path("chartink_csv")
            if default_csv.exists():
                self._input_path = default_csv
        except (KeyError, OSError):
            pass

        self._refresh_source_label()
        return box

    def _build_controls(self) -> QWidget:
        box = QGroupBox(
            "Parameters - toggling removes a parameter from the score AND the denominator"
        )
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

    def _input_mode(self) -> str:
        return self.input_combo.currentData()

    def _on_input_mode(self) -> None:
        mode = self._input_mode()
        self.manual_box.setVisible(mode == INPUT_MANUAL)
        self.browse_button.setVisible(mode != INPUT_MANUAL)
        self._refresh_source_label()

    def _choose_file(self) -> None:
        mode = self._input_mode()
        start = str(self._input_path.parent) if self._input_path else ""
        if mode == INPUT_CSV:
            caption, filters = "Select the Chartink scan export", "CSV files (*.csv);;All files (*)"
        else:
            caption, filters = "Select a symbol list", "Text files (*.txt);;All files (*)"
        path, _ = QFileDialog.getOpenFileName(self, caption, start, filters)
        if path:
            self._input_path = Path(path)
            self._refresh_source_label()

    def _screener_login(self) -> None:
        if ScreenerLoginDialog(self).exec():
            self._refresh_source_label()

    def _kite_settings(self) -> None:
        KiteSettingsDialog(self).exec()
        self._refresh_source_label()

    def _refresh_source_label(self) -> None:
        mode = self._input_mode()
        if mode == INPUT_MANUAL:
            self.input_label.setText("typed symbols")
        else:
            self.input_label.setText(
                self._input_path.name if self._input_path else "no file selected"
            )

        live = self.source_combo.currentData() == LIVE
        self.login_button.setEnabled(live)
        if not live:
            self.login_label.setText("reading a saved export from disk")
        elif has_credentials():
            self.login_label.setText("Screener.in credentials stored")
        else:
            self.login_label.setText("no Screener.in credentials stored")

    # ------------------------------------------------------------------ actions

    def generate(self) -> None:
        """The single trigger. Runs the whole pipeline synchronously."""
        hits, error = self._collect_hits()
        if error:
            QMessageBox.warning(self, "Stage 1 input", error)
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
            QApplication.processEvents()

        try:
            self._result = run_pipeline(
                chartink_csv=self._input_path if self._input_mode() == INPUT_CSV else None,
                hits=hits,
                data_source=source,
                progress=progress,
                exclude_symbols=self._watchlist.removed_symbols(),
            )
        except Exception as exc:
            self._add_banner("error", f"Pipeline failed: {exc}")
            self.status.setText("Run failed.")
            return
        finally:
            self.run_button.setEnabled(True)
            self.progress.setVisible(False)

        self._render()

    def _collect_hits(self) -> tuple[list | None, str]:
        """Resolve the stage-1 list. Returns (hits, error). hits=None means 'read the CSV'."""
        mode = self._input_mode()

        if mode == INPUT_CSV:
            if self._input_path is None or not self._input_path.exists():
                return None, "Choose the Chartink CSV export first."
            return None, ""

        if mode == INPUT_TXT:
            if self._input_path is None or not self._input_path.exists():
                return None, "Choose a text file of symbols first."
            hits, rejected = load_symbol_file(self._input_path)
            if not hits:
                return None, f"No usable symbols found in {self._input_path.name}."
            self._warn_rejected(rejected)
            return hits, ""

        hits, rejected = hits_from_text(self.manual_box.toPlainText())
        if not hits:
            return None, "Type at least one symbol."
        self._warn_rejected(rejected)
        return hits, ""

    def _warn_rejected(self, rejected: list[str]) -> None:
        if rejected:
            self._add_banner(
                "warning",
                f"Ignored {len(rejected)} entry/entries that do not look like NSE symbols: "
                + ", ".join(rejected[:10]),
            )

    def _ensure_kite_token(self) -> bool:
        check = check_token()
        if check.ok or check.state in (TokenState.NO_API_KEY, TokenState.LIBRARY_MISSING):
            return True

        answer = QMessageBox.question(
            self,
            "Kite login required",
            f"{check.message}\n\nOpen the Zerodha login page and paste the redirect URL back?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return True

        url = build_login_url()
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
        """Toggles change arithmetic only - re-score, never re-evaluate or re-fetch."""
        self.summary.setText(summary_line(self._toggles()))
        if self._result is not None:
            self._render(rerun_banners=False)

    def _on_thresholds_changed(self) -> None:
        """Thresholds change the verdicts themselves - re-evaluate against cached data."""
        if self._result is None:
            return
        reevaluate(self._result)
        self._render(rerun_banners=False)
        self.tabs.setCurrentIndex(0)

    # ----------------------------------------------------------------- rendering

    def _on_leadership_changed(self) -> None:
        """Reference data or a breadth setting changed - re-resolve and re-highlight.

        Resolution is re-run here as well as after a screening run, because a refreshed
        reference export can classify stocks that were previously Unresolved.
        """
        if self._result is not None:
            self._resolve_classifications()
            self._render_table(self._toggles())

    def _resolve_classifications(self) -> None:
        """Resolve every tracked symbol, whatever path it arrived by.

        The Chartink CSV carries no Sector/Industry, so CSV-imported stocks need this
        exactly as much as typed or text-file ones. One code path for all three.
        """
        symbols = [s.symbol for s in self._result.stocks] if self._result else []
        self._classification.resolve(symbols, self.leadership_tab.reference())

    def _render(self, rerun_banners: bool = True) -> None:
        if self._result is None:
            return
        toggles = self._toggles()
        self._ranked = score_all(self._result.stocks, toggles)
        self._resolve_classifications()

        if rerun_banners:
            self._render_banners(self._result.context)

        context = self._result.context
        as_of = context.as_of_trading_day or context.run_at
        self.status.setText(
            f"Run {context.run_at:%d %b %Y} - data as of {as_of:%d %b %Y}. "
            f"Stage 1 supplied {context.stage1_count}; {len(self._ranked)} scored."
        )
        self.summary.setText(summary_line(toggles))
        self._render_table(toggles)
        self._render_cards(toggles)
        self.sectors_tab.refresh(self._ranked)

    def _render_table_only(self) -> None:
        if self._result is not None:
            self._render_table(self._toggles())

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
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._ranked))
        self.table.setColumnHidden(COL_TAILWIND, not show_p8)

        aligned = self.leadership_tab.leadership_industries()
        ranks = rank_within_industry(self._ranked, self._classification)

        for row, stock in enumerate(self._ranked):
            flags = "; ".join(
                f.message for f in stock.all_flags
                if f.severity in ("risk", "positive") and (show_p8 or f.param_id != P8_ID)
            )
            classification = self._classification.get(stock.symbol)
            unresolved = not classification.resolved
            industry = classification.industry

            # Green requires BOTH: a leadership-aligned Industry, and a top-3 core score
            # among the stocks scored here in that same Industry. Unresolved stocks are
            # excluded by definition - they cannot be evaluated.
            rank, total = ranks.get(stock.symbol, (None, None))
            leader = (
                not unresolved and industry in aligned and rank is not None and rank <= 3
            )

            values = [
                stock.symbol,
                classification.sector or stock.industry or "-",
                "Unresolved" if unresolved else industry,
                f"{stock.score_display}  ({stock.pct_of_max:.0f}%)",
                stock.tier.value,
                ("Yes" if stock.sector_tailwind else "No") if show_p8 else "",
                f"{stock.pegy:.2f}" if stock.pegy is not None else "-",
                f"{stock.pb:.2f}" if stock.pb is not None else "-",
                "",  # watchlist combo goes here
                self._describe(stock),
                flags,
            ]
            for col, value in enumerate(values):
                if col == COL_WATCH:
                    continue
                item = QTableWidgetItem(value)
                if col == COL_TIER:
                    item.setForeground(QColor(TIER_COLOURS[stock.tier]))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if leader:
                    item.setBackground(LEADER_BG)
                    if col == 0:
                        item.setForeground(LEADER_FG)
                        item.setToolTip(self._leader_tooltip(industry, rank, total))
                elif unresolved:
                    # Visually distinct from "evaluated and did not qualify" - a silent
                    # miss here is worse than an error.
                    item.setBackground(UNRESOLVED_BG)
                    if col in (0, COL_INDUSTRY):
                        item.setForeground(UNRESOLVED_FG)
                        item.setToolTip(
                            "Sector unresolved - not found in the sector reference export, "
                            "so this stock is excluded from leadership highlighting. "
                            "Set it by hand on the Sector Leadership tab, or refresh the export."
                        )
                if col == COL_DESC:
                    item.setToolTip(self._describe(stock, full=True))
                self.table.setItem(row, col, item)

            self.table.setCellWidget(row, COL_WATCH, self._watch_widget(stock))

        self.table.blockSignals(False)
        self._report_unresolved()

    def _leader_tooltip(self, industry: str, rank: int | None, total: int | None) -> str:
        entry = self.leadership_tab.entry_for(industry)
        if entry is None or entry.breadth_pct is None:
            return f"Ranked #{rank} of {total} scored in {industry}."
        trend = f" ({entry.trend.label})" if entry.trend.label != "insufficient history" else ""
        return (
            f"Industry breadth {entry.breadth_pct:.0f}%{trend} - "
            f"ranked #{rank} of {total} scored in this Industry."
        )

    def _report_unresolved(self) -> None:
        unresolved = [
            s.symbol for s in self._ranked if not self._classification.get(s.symbol).resolved
        ]
        if not unresolved:
            return
        if not self.leadership_tab.has_reference():
            return  # already banner-ed as "no reference data"
        self._add_banner(
            "warning",
            f"{len(unresolved)} stock(s) unresolved against the sector reference export and "
            f"excluded from leadership highlighting: {', '.join(unresolved[:12])}"
            + (" ..." if len(unresolved) > 12 else ""),
        )

    def _watch_widget(self, stock: ScoredStock) -> QWidget:
        combo = QComboBox()
        for state in (WatchState.NONE, WatchState.WATCHING, WatchState.REMOVED):
            combo.addItem(state.label, state)
        current = self._watchlist.state_of(stock.symbol)
        combo.setCurrentIndex(combo.findData(current))
        combo.currentIndexChanged.connect(
            lambda _i, s=stock, c=combo: self._on_watch_changed(s, c.currentData())
        )
        return combo

    def _on_watch_changed(self, stock: ScoredStock, state: WatchState) -> None:
        self._watchlist.set_state(
            stock.symbol, state, tier=stock.tier.value, score=stock.score_display
        )
        self._watchlist.save()
        self.watchlist_tab.refresh()

    def _describe(self, stock: ScoredStock, full: bool = False) -> str:
        """Business USP and latest concall note, quoted from Screener.in.

        Never generated here - if Screener.in has no summary for a company, the cell says
        so and the detail card carries the transcript link instead.
        """
        company = self._result.store.get(stock.symbol) if self._result and self._result.store else None
        if company is None:
            return ""

        parts: list[str] = []
        if company.concall_summary:
            label = f"Concall {company.concall_date}".strip()
            parts.append(f"{label}: {company.concall_summary}")
        if company.key_points:
            parts.append("USP: " + " ".join(company.key_points[:3]))
        elif company.about:
            parts.append(company.about)
        if not parts:
            return "no Screener.in description available"

        text = " | ".join(parts).replace("\n", " ")
        return text if full else (text[:190] + ("..." if len(text) > 190 else ""))

    def _render_cards(self, toggles: dict[str, bool]) -> None:
        finalists = [s for s in self._ranked if s.tier.rank >= Tier.QUALITY_GROWER.rank]
        if not finalists:
            self.cards.setPlainText(
                "No stock reached QUALITY GROWER with the current parameter selection.\n"
                "Detail cards are generated for QUALITY GROWER and above."
            )
            return
        self.cards.setPlainText(
            "\n\n".join(self._card_with_narrative(s, toggles) for s in finalists)
        )

    def _card_with_narrative(self, stock: ScoredStock, toggles: dict[str, bool]) -> str:
        card = format_detail_card(stock, toggles)
        company = self._result.store.get(stock.symbol) if self._result and self._result.store else None
        if company is None:
            return card

        extra = ["", "Business and latest concall (from Screener.in):"]
        if company.about:
            extra.append(f"    About: {company.about}")
        for point in company.key_points[:6]:
            extra.append(f"    - {point}")
        if company.concall_summary:
            extra.append(f"    Concall summary ({company.concall_date}):")
            extra.extend(f"      {line}" for line in company.concall_summary.splitlines()[:10])
        elif company.concall_links:
            extra.append("    No Screener.in concall summary available. Transcripts:")
            for date_label, kind, url in company.concall_links[:3]:
                extra.append(f"      {date_label} {kind}: {url}")
        if company.industry_rank:
            extra.append(
                f"    Industry rank: #{company.industry_rank} of "
                f"{company.industry_peer_count} by market cap in {company.industry}"
            )
        return card + "\n".join(extra)

    def _on_cell_double_clicked(self, row: int, column: int) -> None:
        """Double-clicking the Industry cell opens the manual classification dialog."""
        if column != COL_INDUSTRY or not (0 <= row < len(self._ranked)):
            return
        from nse_screener.gui.classify_dialog import ClassifyDialog

        symbol = self._ranked[row].symbol
        if ClassifyDialog(symbol, self._classification, self.leadership_tab.reference(), self).exec():
            self._resolve_classifications()
            self._render_table(self._toggles())

    def _on_row_selected(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if len(rows) != 1:
            return
        row = rows.pop()
        if 0 <= row < len(self._ranked):
            stock = self._ranked[row]
            if stock.tier.rank >= Tier.QUALITY_GROWER.rank:
                self.cards.setPlainText(self._card_with_narrative(stock, self._toggles()))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("NSE Two-Stage Screener")
    window = ScreenerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

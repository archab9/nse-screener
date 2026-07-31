"""End-to-end verification sweep over every module and GUI surface.

    python scripts/verify_app.py            # includes live Screener.in checks
    python scripts/verify_app.py --offline  # skip anything that needs the network

Complements the unit tests: those check units in isolation, this drives the assembled
app the way a person does - load data, press the button, toggle things, read the table.
Exits non-zero if any check fails, so it can gate a release.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

results: list[tuple[str, str, str]] = []
_win = None
_app = None


def check(name: str, *, network: bool = False):
    def deco(fn):
        if network and OFFLINE:
            results.append(("SKIP", name, "offline"))
            return fn
        try:
            results.append(("PASS", name, fn() or ""))
        except Exception as exc:
            results.append(("FAIL", name, f"{type(exc).__name__}: {exc}"))
            traceback.print_exc()
        return fn

    return deco


parser = argparse.ArgumentParser()
parser.add_argument("--offline", action="store_true")
OFFLINE = parser.parse_args().offline


# ------------------------------------------------------------------------ core
@check("every module imports")
def _():
    import importlib

    mods = [
        "config", "models", "scoring", "report", "pipeline", "thresholds", "watchlist",
        "leaderboard", "peer_ranking", "breadth", "classification",
        "sector_reference", "sector_history",
        "symbol_utils", "concall", "run_history", "stage1.csv_import", "stage1.symbols",
        "stage2.fundamentals", "stage2.params", "stage2.screener_client", "market.kite",
        "backtest.forward_returns",
    ]
    for m in mods:
        importlib.import_module(f"nse_screener.{m}")
    return f"{len(mods)} modules"


@check("stage 1 - CSV / text file / typed symbols")
def _():
    from nse_screener.stage1.csv_import import load_chartink_csv
    from nse_screener.stage1.symbols import hits_from_text, load_symbol_file

    csv_hits = load_chartink_csv("data/stage1/VOLUME SCAN.csv")
    path = Path(tempfile.mkdtemp()) / "s.txt"
    path.write_text("HAL\nBEL\n\nRELIANCE\n")
    txt_hits, _ = load_symbol_file(path)
    typed, rejected = hits_from_text("HAL, bel  !!bogus")
    assert len(csv_hits) == 21 and len(txt_hits) == 3 and len(typed) == 2 and rejected
    return f"csv={len(csv_hits)} txt={len(txt_hits)} typed={len(typed)} rejected={len(rejected)}"


@check("stage 2 - local load and all 8 parameters")
def _():
    from nse_screener.sector_reference import load_sector_reference
    from nse_screener.stage2 import params as P
    from nse_screener.stage2.fundamentals import load_fundamentals, load_sector_index_valuations

    store = load_fundamentals()
    company = store.get("NEWGEN")
    verdicts = {f"P{i}": getattr(P, f"evaluate_p{i}")(company).verdict.name for i in range(1, 7)}
    verdicts["P7"] = P.evaluate_p7(company, load_sector_index_valuations()).verdict.name
    verdicts["P8"] = P.evaluate_p8(company, load_sector_reference()).verdict.name
    # A wrong argument type must degrade, not crash the run.
    assert P.evaluate_p8(company, []).verdict.label == "N/A"
    return " ".join(f"{k}={v}" for k, v in verdicts.items())


@check("stage 2 - LIVE Screener.in parse", network=True)
def _():
    from nse_screener.stage2.screener_client import ScreenerClient

    c = ScreenerClient().fetch_company("HAL")
    assert c.pe and c.pb and c.quarterly and c.annual and c.shareholding
    return f"pe={c.pe} rank=#{c.industry_rank}/{c.industry_peer_count} concalls={len(c.concall_links)}"


@check("stage 2 - LIVE batch, no rate-limit drops", network=True)
def _():
    import time

    from nse_screener.stage2.screener_client import ScreenerClient

    syms = ["AGARIND", "INFOBEAN", "SPECTRUM", "TTKPRESTIG", "GNA", "BOROLTD"]
    client = ScreenerClient()
    start = time.time()
    store, failures = client.fetch_many(syms)
    assert not failures, failures
    return f"{len(store.companies)}/{len(syms)} in {time.time() - start:.0f}s, {client.rate_limit_hits} x 429"


@check("concall - 5 positive / 3 negative, negation handled")
def _():
    from nse_screener.concall import extract_takeaways

    summary = "\n".join(
        f"- {line}"
        for line in [
            "Revenue grew 24% YoY on a record order book.",
            "Operating margin expanded 210 bps.",
            "Commissioned new capacity ahead of schedule.",
            "Debt reduction of Rs 340 crore completed.",
            "Management raised guidance for FY27.",
            "Won a Rs 1,200 crore overseas order.",
            "Export demand remained subdued.",
            "Input cost inflation pressured margins.",
            "Commissioning of the Pune line was delayed.",
            "Employee count stood at 4,210.",
        ]
    )
    t = extract_takeaways(summary)
    assert len(t.positives) == 5 and len(t.negatives) == 3
    assert not any("Employee" in x.text for x in t.positives + t.negatives)
    assert not extract_takeaways("- Margin pressure eased.").negatives
    return f"{len(t.positives)} pos / {len(t.negatives)} neg from {t.source_points} points"


@check("scoring - toggles, tiers, ranking")
def _():
    from nse_screener.models import PARAM_IDS, Tier
    from nse_screener.scoring import active_maximum, tier_for

    assert active_maximum(None) == 14
    assert active_maximum({p: p != "P5" for p in PARAM_IDS}) == 12
    assert tier_for(90) is Tier.ELITE_COMPOUNDER and tier_for(89.9) is Tier.QUALITY_GROWER
    assert tier_for(50) is Tier.WATCHLIST and tier_for(49.9) is Tier.EXCLUDED
    return "denominator shrinks; 90/70/50 cutoffs correct"


@check("pipeline - full run, re-evaluate, empty input")
def _():
    from nse_screener.pipeline import reevaluate, run_pipeline
    from nse_screener.scoring import score_all
    from nse_screener import config

    result = run_pipeline(data_source="local", use_kite=False)
    ranked = score_all(result.stocks, None)
    before = {s.symbol: s.results["P2"].verdict.name for s in result.stocks}
    config.save_overrides({"p2_capital_efficiency": {"roce_min_pct": 28.0}})
    reevaluate(result)
    changed = before != {s.symbol: s.results["P2"].verdict.name for s in result.stocks}
    config.reset_overrides()
    empty = run_pipeline(data_source="local", use_kite=False, hits=[])
    assert len(ranked) == 6 and changed and empty.stocks == []
    return f"{len(ranked)} scored; threshold edit changes verdicts; empty handled"


@check("sector leadership - breadth, gate, history, classification")
def _():
    from nse_screener.breadth import Status, compute_breadth
    from nse_screener.classification import ClassificationStore
    from nse_screener.sector_history import SectorHistory
    from nse_screener.sector_reference import load_sector_reference

    ref = load_sector_reference()
    entries = {e.industry: e for e in compute_breadth(ref)}
    assert entries["Petroleum Products"].status is Status.INSUFFICIENT_SAMPLE
    assert entries["Aerospace & Defense"].status is Status.LEADERSHIP_ALIGNED

    hist = SectorHistory.load(Path(tempfile.mkdtemp()) / "h.json")
    hist.record_snapshot(compute_breadth(ref), ["roce"], label="FY2026-Q1")
    hist.save()

    store = ClassificationStore.load(Path(tempfile.mkdtemp()) / "c.json")
    store.resolve(["NEWGEN", "PICCADIL"], ref)
    assert store.get("NEWGEN").resolved and not store.get("PICCADIL").resolved
    return " ".join(f"{k[:14]}={v.breadth_display}" for k, v in entries.items())


@check("watchlist and backtest")
def _():
    import pandas as pd

    from nse_screener.backtest.forward_returns import run_backtest
    from nse_screener.watchlist import Watchlist, WatchState

    path = Path(tempfile.mkdtemp()) / "w.json"
    w = Watchlist.load(path)
    w.set_state("HAL", WatchState.WATCHING)
    w.set_state("VSSL", WatchState.REMOVED)
    w.save()
    assert Watchlist.load(path).removed_symbols() == {"VSSL"}

    dates = pd.bdate_range("2024-01-01", periods=400)
    frame = pd.DataFrame({
        "symbol": "T", "date": dates,
        "close": [100 * (1.0005 ** i) for i in range(400)], "volume": [100_000] * 400,
    })
    frame.loc[300, "volume"] = 1_000_000
    frame.loc[300, "close"] = frame.loc[299, "close"] * 1.09
    result = run_backtest(frame)
    assert result.trigger_count == 1 and str(result).count("SURVIVORSHIP BIAS") >= 2
    return "watchlist persists; backtest triggers with caveat stated twice"


@check("run history - record, sort, prune", )
def _():
    from datetime import datetime, timedelta

    from nse_screener.models import PARAM_IDS, ParamResult, ScoredStock, Tier, Verdict
    from nse_screener.run_history import RunHistory

    def make(symbol, yes):
        verdicts = {p: (Verdict.YES if i < yes else Verdict.NO) for i, p in enumerate(PARAM_IDS)}
        s = ScoredStock(symbol=symbol, name=symbol,
                        results={p: ParamResult(p, v) for p, v in verdicts.items()})
        s.core_score, s.active_max, s.tier = yes * 2, 14, Tier.WATCHLIST
        return s

    hist = RunHistory(path=Path(tempfile.mkdtemp()) / "runs.json")
    run = hist.record([make("LOW", 1), make("HIGH", 6), make("MID", 3)])
    assert [s.symbol for s in run.stocks] == ["HIGH", "MID", "LOW"]
    hist.record([make("OLD", 5)], when=datetime.now() - timedelta(days=90))
    assert len(hist.runs) == 1, "back-dated run must be pruned"
    hist.save()
    assert len(RunHistory.load(hist.path).runs) == 1
    return "sorted by hits; 30-day pruning; round-trips"


@check("kite - reports token state cleanly", network=True)
def _():
    from nse_screener.market.kite import check_token

    return check_token().message[:70]


# ------------------------------------------------------------------------- GUI
@check("GUI - window and all tabs construct")
def _():
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from nse_screener.gui.app import ScreenerWindow

    global _app, _win
    # Kite tokens expire daily; an expired one makes generate() raise a modal login
    # prompt - right for a person at the keyboard, fatal for a headless sweep.
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    _app = QApplication.instance() or QApplication([])
    _win = ScreenerWindow()
    _win.show()
    _win._runs.path = Path(tempfile.mkdtemp()) / "runs.json"
    _win._runs.runs = []
    tabs = [_win.tabs.tabText(i) for i in range(_win.tabs.count())]
    assert len(tabs) == 6, tabs
    return " | ".join(tabs)


@check("GUI - dialogs construct")
def _():
    from nse_screener.gui.classify_dialog import ClassifyDialog
    from nse_screener.gui.kite_dialog import KiteSettingsDialog
    from nse_screener.gui.login_dialog import ScreenerLoginDialog

    login = ScreenerLoginDialog(_win)
    KiteSettingsDialog(_win)
    ClassifyDialog("HAL", _win._classification, _win.leadership_tab.reference(), _win)
    assert login.password.echoMode().name == "Password"
    return "login (masked) + kite + classify"


@check("GUI - full run renders table and cards")
def _():
    _win.source_combo.setCurrentIndex(1)
    _win.input_combo.setCurrentIndex(2)
    _win.manual_box.setPlainText("NEWGEN AJAXENGG VSSL BLKASHYAP PICCADIL")
    _win.generate()
    assert _win.table.rowCount() == 5 and _win.cards.toPlainText()
    return f"{_win.table.rowCount()} rows, {len(_win.cards.toPlainText())} chars of cards"


@check("GUI - overlay toggle and banner stability")
def _():
    from nse_screener.gui.app import COL_LEADER, LEADER_BG

    green = lambda: sum(
        1 for r in range(_win.table.rowCount())
        if _win.table.item(r, 0).background().color() == LEADER_BG
    )
    on = green()
    _win.sector_overlay.setChecked(False)
    off, hidden = green(), _win.table.isColumnHidden(COL_LEADER)
    _win.sector_overlay.setChecked(True)
    baseline = _win.banner_box.count()
    for i in range(10):
        _win._checkboxes["P5"].setChecked(i % 2 == 0)
    stable = _win.banner_box.count() == baseline
    _win._checkboxes["P5"].setChecked(True)  # leave every parameter on for later checks
    assert off == 0 and hidden and green() == on and stable
    return f"green {on}->0->{green()}; banners stable at {baseline} over 10 toggles"


@check("GUI - threshold edit re-evaluates live")
def _():
    before = [(s.symbol, s.core_score) for s in _win._ranked]
    _win.thresholds_tab._spins[("p2_capital_efficiency", "roce_min_pct")].setValue(28.0)
    after = [(s.symbol, s.core_score) for s in _win._ranked]
    _win.thresholds_tab._reset_all()
    assert before != after and before == [(s.symbol, s.core_score) for s in _win._ranked]
    return f"{before[0]} -> {after[0]} -> reset"


@check("GUI - takeaway rows sized so nothing clips")
def _():
    from nse_screener.gui.app import COL_DESC

    summary = "\n".join(
        f"- {x}" for x in [
            "Revenue grew 24% YoY on a record order book.",
            "Operating margin expanded 210 bps.",
            "Commissioned new capacity ahead of schedule.",
            "Debt reduction completed.", "Management raised guidance for FY27.",
            "Export demand remained subdued.", "Input cost inflation pressured margins.",
            "Commissioning of the Pune line was delayed.",
        ]
    )
    for company in _win._result.store.companies.values():
        company.concall_summary, company.concall_date = summary, "May 2026"
    _win._render_table(_win._toggles())
    metrics = _win.table.fontMetrics()
    for r in range(_win.table.rowCount()):
        lines = _win.table.item(r, COL_DESC).text().count("\n") + 1
        assert _win.table.rowHeight(r) >= lines * metrics.lineSpacing(), f"row {r} clipped"
    return f"col={_win.table.columnWidth(COL_DESC)}px row={_win.table.rowHeight(0)}px"


@check("GUI - run recorded to 30-day history")
def _():
    assert len(_win._runs.runs) >= 1
    run = _win._runs.runs[-1]
    assert run.scored_count == _win.table.rowCount()
    assert _win.history_tab.table.rowCount() == run.scored_count
    return f"{len(_win._runs.runs)} run(s), {run.scored_count} stocks, {_win._runs.retention_days()}d window"


@check("GUI - history sorted by parameters hit, most first")
def _():
    table = _win.history_tab.table
    hits = [int(table.item(r, 2).text().split()[0]) for r in range(table.rowCount())]
    assert hits == sorted(hits, reverse=True), hits
    names = [table.item(r, 0).text() for r in range(table.rowCount())]
    return " > ".join(f"{n}({h})" for n, h in zip(names, hits))


@check("GUI - clicking a stock shows its detailed results")
def _():
    from PyQt6.QtCore import Qt

    _win.history_tab.table.selectRow(0)
    text = _win.history_tab.detail.toPlainText()
    # Ticker cells may carry a trophy prefix; the bare symbol lives on the item.
    symbol = _win.history_tab.table.item(0, 0).data(Qt.ItemDataRole.UserRole)
    for expected in (symbol, "parameters hit:", "P1 Record financials", "Appeared in"):
        assert expected in text, expected
    return f"{len(text)} chars incl. per-parameter evidence and appearances"


@check("GUI - peer rank on price return, subsector and sector")
def _():
    from nse_screener.gui.app import COL_CAP, COL_LEADER

    rows = {
        _win.table.item(r, 0).text(): (
            _win.table.item(r, COL_CAP).text(), _win.table.item(r, COL_LEADER).text()
        )
        for r in range(_win.table.rowCount())
    }
    ranked = {k: v for k, v in rows.items() if v[1] != "-"}
    assert ranked, "fixture should rank at least one stock"
    assert all("Price return 1y" in v[1] and "sub" in v[1] and "sec" in v[1]
               for v in ranked.values()), ranked
    from nse_screener.peer_ranking import CAP_ORDER
    assert all(v[0] in CAP_ORDER for v in rows.values()), rows
    return "; ".join(f"{k} [{v[0]}] {v[1]}" for k, v in list(ranked.items())[:3])


@check("GUI - 1y/3y/5y ranks shown per stock in detail")
def _():
    _win.table.selectRow(0)
    text = _win.cards.toPlainText()
    for label in ("Price return 1y", "Price return 3y", "Price return 5y"):
        assert label in text, label
    assert "In subsector" in text and "In sector" in text
    start = text.index("Rank among peers")
    return " | ".join(text[start:start + 620].splitlines()[3:7])


@check("GUI - pass/fail badges per parameter")
def _():
    from nse_screener.gui.app import COL_BADGES, COL_HIT

    rows = [
        (_win.table.item(r, 0).text(), _win.table.item(r, COL_HIT).text(),
         _win.table.item(r, COL_BADGES).text())
        for r in range(_win.table.rowCount())
    ]
    assert all("P1" in b and "P7" in b for _s, _h, b in rows)
    return rows[0][0] + "  " + rows[0][1] + "  " + rows[0][2]


@check("GUI - watchlist sorted like history, with detail")
def _():
    from nse_screener.watchlist import WatchState

    for symbol in ("VSSL", "NEWGEN", "AJAXENGG", "BLKASHYAP"):
        _win._watchlist.set_state(symbol, WatchState.WATCHING)
    _win._watchlist.save()
    _win.watchlist_tab.refresh()

    table = _win.watchlist_tab.table
    hits = [int(table.item(r, 2).text().split()[0]) for r in range(table.rowCount())]
    assert hits == sorted(hits, reverse=True), hits

    from PyQt6.QtCore import Qt

    table.selectRow(0)
    text = _win.watchlist_tab.detail.toPlainText()
    symbol = table.item(0, 0).data(Qt.ItemDataRole.UserRole)
    assert symbol and symbol in text and "Passed (" in text and "Failed (" in text
    order = " > ".join(f"{table.item(r, 0).text()}({h})" for r, h in enumerate(hits))
    return order


@check("GUI - run filter on entire watchlist")
def _():
    symbols = set(_win.watchlist_tab.symbols())
    _win.watchlist_tab._run_watchlist()
    shown = {_win.table.item(r, 0).text() for r in range(_win.table.rowCount())}
    assert shown == symbols, (shown, symbols)
    return f"screened {len(shown)} watchlist stock(s) through the normal pipeline"


@check("GUI - detail pane refreshes with the table")
def _():
    """Regression: selectRow(0) emits nothing when row 0 is already selected, so the
    detail pane kept the previous stock's numbers beside a refreshed table."""
    tab = _win.history_tab
    tab.refresh()
    from PyQt6.QtCore import Qt

    tab.table.selectRow(0)
    first = tab.table.item(0, 0).data(Qt.ItemDataRole.UserRole)
    assert first and first in tab.detail.toPlainText()
    tab.refresh()
    assert tab.table.item(0, 0).data(Qt.ItemDataRole.UserRole) in tab.detail.toPlainText()
    return "detail matches row 0 after re-render"


@check("GUI - Sector Ranks tab: all horizons + strength, sorted")
def _():
    from nse_screener.sector_reference import load_sector_reference

    tab = _win.sector_ranks_tab
    tab.run_tests()
    from nse_screener.sector_universe import load_sector_universe

    reference = load_sector_reference()
    universe = load_sector_universe()
    board = tab.board()

    # The taxonomy decides the rows, not the export. Every sector and subsector the NSE
    # classification knows about must be present, priced or not - being built only from
    # the export is what made this look like a five-row table.
    listed_sectors = {g.name for g in board.sectors}
    listed_subs = {g.name for g in board.subsectors}
    for name in universe.sectors:
        assert name in listed_sectors, f"sector missing: {name}"
    for name in universe.subsectors:
        assert name in listed_subs, f"subsector missing: {name}"
    assert tab.table.rowCount() == len(board.all_groups())
    sectors, subs = listed_sectors, listed_subs
    headers = [tab.table.horizontalHeaderItem(i).text() for i in range(tab.table.columnCount())]
    for expected in ("6m return", "1y return", "3y return", "5y return", "Strength"):
        assert expected in headers, headers
    strengths = [
        float(tab.table.item(r, len(headers) - 1).text())
        for r in range(tab.table.rowCount())
        if tab.table.item(r, len(headers) - 1).text() not in ("-", "")
    ]
    assert strengths == sorted(strengths, reverse=True), strengths
    top = tab.table.item(0, 1).text()
    thin = sum(1 for g in board.all_groups() if g.thin)
    assert all(not g.medal for g in board.all_groups() if g.thin), "thin groups must not medal"
    unpriced = sum(1 for g in board.all_groups() if not g.has_returns)
    return (f"{tab.table.rowCount()} rows: {len(sectors)} sectors + {len(subs)} subsectors "
            f"(universe has {len(universe.sectors)}/{len(universe.subsectors)}), "
            f"{unpriced} unpriced, {thin} thin; top {top} ({strengths[0]})")


@check("GUI - dark theme applied with readable contrast")
def _():
    from PyQt6.QtGui import QPalette

    from nse_screener.gui import theme

    palette = _app.palette()
    window = palette.color(QPalette.ColorRole.Window)
    text = palette.color(QPalette.ColorRole.WindowText)
    assert window.lightness() < 90, f"window should be dark, got {window.lightness()}"
    assert text.lightness() > 170, f"text should be light, got {text.lightness()}"
    # Anything that paints a background must paint a foreground too, or it inherits the
    # theme's light text onto a light fill.
    assert theme.LEADER_BG.lightness() < 110
    assert theme.LEADER_FG.lightness() > 130
    assert all(c.lightness() < 110 for c in theme.MEDAL_BG)
    return (f"window L={window.lightness()} text L={text.lightness()}; "
            f"highlight fills all dark with light foregrounds")


@check("GUI - P8 gives an actual peer rank, not a top-3 flag")
def _():
    ranked = [(s.symbol, s.results["P8"]) for s in _win._ranked if "P8" in s.results]
    with_rank = [(sym, r) for sym, r in ranked if r.evidence.get("Rank in subsector")]
    assert with_rank, "P8 should rank stocks found in the reference"
    out = []
    for sym, result in with_rank[:3]:
        sub = result.evidence["Rank in subsector"]
        sec = result.evidence["Rank in sector"]
        assert "of" in sub, sub
        out.append(f"{sym} {sub} sub / {sec} sec")
    return "; ".join(out)


@check("GUI - medals, trophy and sector-test button")
def _():
    from nse_screener.leaderboard import GOLD, TROPHY

    _win.history_tab.run_sector_tests()
    _win.watchlist_tab.run_sector_tests()
    board = _win.history_tab._board
    assert board.available, "leaderboard should build from the fixture export"
    # Sorted on strength, and a thin group can top that without earning a medal, so check
    # the medals themselves rather than the first three rows.
    medalled = [g for g in board.sectors if g.medal]
    assert len(medalled) == 3, [g.name for g in medalled]
    assert all(not g.thin for g in medalled)

    # Medals now live on the Sector Ranks tab; History keeps only the trophy.
    assert any(GOLD in g.medal for g in board.sectors), [g.medal for g in board.sectors]
    table = _win.history_tab.table
    trophies = [
        table.item(r, 0).text() for r in range(table.rowCount())
        if TROPHY in table.item(r, 0).text()
    ]
    assert trophies, "fixture should award exactly one trophy"
    top = ", ".join(g.display for g in board.sectors[:3])
    return f"trophy: {trophies[0]} | sectors: {top}"


@check("GUI - trophy explains itself when not awarded")
def _():
    table = _win.history_tab.table
    for r in range(table.rowCount()):
        if "🏆" not in table.item(r, 0).text():
            table.selectRow(r)
            text = _win.history_tab.detail.toPlainText()
            assert "Trophy criteria:" in text
            start = text.index("Trophy criteria:")
            return " | ".join(text[start:start + 400].splitlines()[1:5])
    return "every row has a trophy (unexpected but not a failure)"


@check("GUI - watchlist control and industry column")
def _():
    from nse_screener.gui.app import COL_INDUSTRY, COL_WATCH
    from nse_screener.watchlist import WatchState

    combo = _win.table.cellWidget(0, COL_WATCH)
    symbol = _win.table.item(0, 0).text()
    combo.setCurrentIndex(combo.findData(WatchState.WATCHING))
    watched = _win._watchlist.state_of(symbol) is WatchState.WATCHING
    combo.setCurrentIndex(combo.findData(WatchState.NONE))
    industries = {
        _win.table.item(r, 0).text(): _win.table.item(r, COL_INDUSTRY).text()
        for r in range(_win.table.rowCount())
    }
    assert watched, "watchlist combo must write through"
    assert all(v.strip() and v != "-" for v in industries.values()), industries
    return f"watchlist writes through; every row names its industry; "\
           f"{_win.leadership_tab.table.rowCount()} industries on the breadth tab"


# ---------------------------------------------------------------------- report
print()
width = max(len(n) for _s, n, _d in results)
for status, name, detail in results:
    mark = {"PASS": "OK  ", "FAIL": "FAIL", "SKIP": "skip"}[status]
    print(f"[{mark}] {name:<{width}}  {detail}")

failed = [r for r in results if r[0] == "FAIL"]
skipped = [r for r in results if r[0] == "SKIP"]
print()
print(f"{len(results) - len(failed) - len(skipped)}/{len(results) - len(skipped)} checks passed"
      + (f", {len(skipped)} skipped" if skipped else ""))
sys.exit(1 if failed else 0)

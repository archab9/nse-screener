"""GUI behaviour that plain unit tests cannot reach.

Runs offscreen. The window is explicitly shown, because Qt reports isVisible() as False
for every child of a window that was never shown - which looks exactly like a widget
failing to appear.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from nse_screener.gui.app import COL_INDUSTRY, LEADER_BG, ScreenerWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from nse_screener import watchlist as watchlist_module

    # Keep the test off the real user's watchlist / classification files.
    monkeypatch.setattr(watchlist_module, "default_path", lambda: tmp_path / "watchlist.json")

    # generate() prompts to log in when the Kite access token has expired - correct for a
    # person at the keyboard, but a modal with nobody to dismiss it hangs the suite. Kite
    # tokens expire daily, so without this the tests pass or hang depending on the hour.
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    )

    window = ScreenerWindow()
    window.show()
    window._runs.path = tmp_path / "run_history.json"
    window._runs.runs = []
    window.source_combo.setCurrentIndex(1)   # saved local export
    window.input_combo.setCurrentIndex(2)    # typed symbols
    window.manual_box.setPlainText("NEWGEN AJAXENGG PICCADIL")
    window.generate()
    yield window
    window.close()


def green_rows(window) -> int:
    return sum(
        1
        for row in range(window.table.rowCount())
        if window.table.item(row, 0).background().color() == LEADER_BG
    )


class TestSectorOverlayToggle:
    def test_on_by_default(self, window):
        assert window.sector_overlay.isChecked() is True
        assert window.table.isColumnHidden(COL_INDUSTRY) is False

    def test_off_removes_column_and_highlight(self, window):
        assert green_rows(window) > 0, "fixture should produce at least one leader"
        window.sector_overlay.setChecked(False)
        assert window.table.isColumnHidden(COL_INDUSTRY) is True
        assert green_rows(window) == 0
        assert window.unresolved_label.isVisible() is False

    def test_toggling_back_on_restores_it(self, window):
        before = green_rows(window)
        window.sector_overlay.setChecked(False)
        window.sector_overlay.setChecked(True)
        assert green_rows(window) == before
        assert window.table.isColumnHidden(COL_INDUSTRY) is False

    def test_overlay_never_changes_scores_or_tiers(self, window):
        before = [(s.symbol, s.core_score, s.tier) for s in window._ranked]
        window.sector_overlay.setChecked(False)
        after = [(s.symbol, s.core_score, s.tier) for s in window._ranked]
        assert before == after


class TestBannersDoNotAccumulate:
    """The unresolved notice appended to the banner stack on every table render, but
    parameter toggles deliberately skip clearing banners - so each click added a copy."""

    def test_repeated_toggles_keep_the_banner_count_stable(self, window):
        baseline = window.banner_box.count()
        for i in range(8):
            window._checkboxes["P5"].setChecked(i % 2 == 0)
        assert window.banner_box.count() == baseline

    def test_overlay_toggles_do_not_stack_banners(self, window):
        baseline = window.banner_box.count()
        for i in range(6):
            window.sector_overlay.setChecked(i % 2 == 0)
        assert window.banner_box.count() == baseline


class TestRunHistoryTab:
    def test_a_run_is_recorded_automatically(self, window):
        assert len(window._runs.runs) == 1
        assert window._runs.runs[0].scored_count == window.table.rowCount()

    def test_history_tab_lists_the_run(self, window):
        assert window.history_tab.table.rowCount() == window.table.rowCount()

    def test_sorted_by_parameters_hit_descending(self, window):
        hits = [
            int(window.history_tab.table.item(r, 1).text().split()[0])
            for r in range(window.history_tab.table.rowCount())
        ]
        assert hits == sorted(hits, reverse=True), hits

    def test_clicking_a_stock_shows_its_breakdown(self, window):
        window.history_tab.table.selectRow(0)
        text = window.history_tab.detail.toPlainText()
        symbol = window.history_tab.table.item(0, 0).text()
        assert symbol in text
        assert "parameters hit:" in text
        assert "P1 Record financials" in text

    def test_second_run_appends(self, window):
        window.generate()
        assert len(window._runs.runs) == 2


class TestUnresolvedIsVisiblyDistinct:
    def test_unresolved_stock_is_labelled_not_silently_skipped(self, window):
        industries = {
            window.table.item(r, 0).text(): window.table.item(r, COL_INDUSTRY).text()
            for r in range(window.table.rowCount())
        }
        assert industries["PICCADIL"] == "Unresolved"
        assert window.unresolved_label.isVisible() is True
        assert "PICCADIL" in window.unresolved_label.text()

    def test_resolved_stocks_show_their_industry(self, window):
        industries = {
            window.table.item(r, 0).text(): window.table.item(r, COL_INDUSTRY).text()
            for r in range(window.table.rowCount())
        }
        assert industries["NEWGEN"] == "Aerospace & Defense"

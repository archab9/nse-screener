"""Tests for concall takeaway extraction and the rate-limit handling."""

from __future__ import annotations

import pytest

from nse_screener.concall import MAX_NEGATIVE, MAX_POSITIVE, extract_takeaways

SUMMARY = """
- Revenue grew 24% YoY driven by a record order book in the defence segment.
- Operating margin expanded 210 bps on better product mix.
- The company commissioned its new Hyderabad capacity ahead of schedule.
- Debt reduction of Rs 340 crore completed; the company is now nearly debt-free.
- Management raised guidance for FY27 to 20-22% revenue growth.
- Won a Rs 1,200 crore order from an overseas customer.
- Export demand remained subdued through the quarter.
- Input cost inflation in copper pressured the wires division.
- Commissioning of the Pune line was delayed by two months.
- Employee count stood at 4,210 as of quarter end.
"""


class TestExtraction:
    def test_caps_at_five_positive_and_three_negative(self):
        t = extract_takeaways(SUMMARY)
        assert len(t.positives) == MAX_POSITIVE == 5
        assert len(t.negatives) == MAX_NEGATIVE == 3

    def test_positives_are_actually_positive(self):
        text = " ".join(x.text.lower() for x in extract_takeaways(SUMMARY).positives)
        assert "grew" in text or "record" in text or "expanded" in text
        assert "delayed" not in text

    def test_negatives_are_actually_negative(self):
        text = " ".join(x.text.lower() for x in extract_takeaways(SUMMARY).negatives)
        for expected in ("subdued", "inflation", "delayed"):
            assert expected in text

    def test_neutral_lines_are_dropped(self):
        joined = " ".join(
            x.text for x in extract_takeaways(SUMMARY).positives + extract_takeaways(SUMMARY).negatives
        )
        assert "Employee count" not in joined

    def test_strongest_signal_ranks_first(self):
        t = extract_takeaways(SUMMARY)
        assert t.positives[0].score >= t.positives[-1].score
        assert t.negatives[0].score <= t.negatives[-1].score

    def test_pointers_are_brief_and_prefixed(self):
        for line in extract_takeaways(SUMMARY).as_pointers():
            assert line[0] in "+-"
            assert len(line) <= 124, line

    def test_long_bullet_is_split_into_sentences(self):
        long_bullet = (
            "- Revenue grew strongly across all segments and the order book reached a record "
            "high during the quarter which management attributes to defence demand. However "
            "export demand was subdued and input cost inflation pressured margins in the "
            "wires division through the period under review."
        )
        t = extract_takeaways(long_bullet)
        assert t.positives and t.negatives, "both halves should surface"


class TestNegationHandling:
    def test_denied_negative_is_not_counted_as_negative(self):
        t = extract_takeaways("- Margin pressure eased through the quarter as costs normalised.")
        assert not any("pressure" in x.text.lower() for x in t.negatives)

    def test_plain_negative_still_counts(self):
        t = extract_takeaways("- Margin pressure intensified through the quarter.")
        assert t.negatives

    @pytest.mark.parametrize("phrase", ["no slowdown in demand", "the delay is behind us"])
    def test_negation_phrases(self, phrase):
        assert not extract_takeaways(f"- {phrase}").negatives


class TestEdgeCases:
    def test_empty_summary(self):
        t = extract_takeaways("")
        assert t.empty and t.as_text() == ""

    def test_only_positives(self):
        t = extract_takeaways("- Revenue grew 30%.\n- Margin expanded.")
        assert t.positives and not t.negatives

    def test_short_noise_lines_ignored(self):
        assert extract_takeaways("- ok\n- yes\n").empty

    def test_source_point_count_reported(self):
        assert extract_takeaways(SUMMARY).source_points == 10


class TestRateLimitPlumbing:
    def test_throttle_spaces_requests(self):
        import time

        from nse_screener.stage2.screener_client import _Throttle

        throttle = _Throttle(0.15)
        start = time.monotonic()
        for _ in range(3):
            throttle.wait()
        assert time.monotonic() - start >= 0.28, "three calls must span two intervals"

    def test_retry_after_header_is_honoured(self):
        from nse_screener.stage2.screener_client import _retry_after

        class R:
            headers = {"Retry-After": "7"}

        assert _retry_after(R()) == 7.0

    def test_absurd_retry_after_is_capped(self):
        from nse_screener.stage2.screener_client import _retry_after

        class R:
            headers = {"Retry-After": "99999"}

        assert _retry_after(R()) == 60.0

    def test_missing_or_bad_header_returns_none(self):
        from nse_screener.stage2.screener_client import _retry_after

        class Empty:
            headers: dict = {}

        class Bad:
            headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}

        assert _retry_after(Empty()) is None
        assert _retry_after(Bad()) is None

    def test_client_reads_pacing_from_config(self):
        from nse_screener.stage2.screener_client import ScreenerClient

        client = ScreenerClient()
        assert client._throttle.min_interval > 0
        assert client._max_retries >= 1

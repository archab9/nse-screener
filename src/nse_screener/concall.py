"""Split a concall summary into positive and negative takeaways.

The input is Screener.in's own concall summary bullets. Nothing here writes new prose -
each takeaway is a shortened version of a sentence Screener already published, so what you
read traces back to the source. What IS inferred is the positive/negative split, and that
is done by keyword matching, not by understanding.

That matters enough to state plainly: a bullet like "margin pressure eased" contains a
negative phrase and a positive one, and a keyword classifier is not reliable on that kind
of sentence. Treat the split as a first pass over material you should read yourself, which
is why the detail card keeps the transcript link next to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_POSITIVE = 5
MAX_NEGATIVE = 3
MAX_POINTER_CHARS = 120

# Weighted so a strong, unambiguous phrase outranks a single generic word.
POSITIVE_TERMS: dict[str, int] = {
    "margin expansion": 3, "margin expanded": 3, "order book": 2, "record": 3,
    "all-time high": 3, "highest ever": 3, "strong growth": 3, "robust": 2,
    "outperform": 3, "market share gain": 3, "gained market share": 3,
    "capacity addition": 2, "commissioned": 2, "debt reduction": 3, "debt-free": 3,
    "deleverag": 3, "guidance raised": 3, "raised guidance": 3, "upgraded": 2,
    "new order": 2, "order win": 3, "won": 2, "secured": 2, "bagged": 2,
    "approval": 2, "launch": 1, "expansion": 2, "ramp-up": 2, "ramp up": 2,
    "improved": 2, "improvement": 2, "increase": 1, "increased": 1, "growth": 1,
    "grew": 2, "higher": 1, "strong": 2, "healthy": 2, "tailwind": 3,
    "turnaround": 3, "profitab": 2, "expanded": 2, "traction": 2, "demand": 1,
}

NEGATIVE_TERMS: dict[str, int] = {
    "margin pressure": 3, "margin contraction": 3, "margin declined": 3,
    "headwind": 3, "slowdown": 3, "subdued": 3, "muted": 3, "weak": 3,
    "weakness": 3, "decline": 2, "declined": 2, "degrowth": 3, "de-growth": 3,
    "fell": 2, "drop": 2, "dropped": 2, "lower": 1, "loss": 3, "impairment": 3,
    "write-off": 3, "writeoff": 3, "delay": 3, "delayed": 3, "deferred": 2,
    "deferral": 2, "postponed": 2, "cost inflation": 3, "input cost": 2,
    "competitive intensity": 2, "competition intensif": 3, "shortage": 3,
    "disruption": 3, "guidance cut": 3, "cut guidance": 3, "lowered guidance": 3,
    "challenge": 2, "challenging": 2, "pressure": 2, "shortfall": 3,
    "under-utilis": 2, "underutilis": 2, "sluggish": 3, "deteriorat": 3,
}

# Phrases that flip the sentiment of a following negative word.
_NEGATED = re.compile(
    r"\b(no|not|without|absent|eased|easing|abating|reversed|behind us|"
    r"recovered from|despite)\b",
    re.I,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_BULLET_PREFIX = re.compile(r"^\s*[-•\*●\d\.\)]+\s*")


@dataclass
class Takeaway:
    text: str
    score: int
    matched: list[str] = field(default_factory=list)

    @property
    def pointer(self) -> str:
        text = self.text.strip().rstrip(".")
        if len(text) <= MAX_POINTER_CHARS:
            return text
        cut = text[:MAX_POINTER_CHARS].rsplit(" ", 1)[0]
        return cut + "..."


@dataclass
class ConcallTakeaways:
    positives: list[Takeaway] = field(default_factory=list)
    negatives: list[Takeaway] = field(default_factory=list)
    source_points: int = 0

    @property
    def empty(self) -> bool:
        return not self.positives and not self.negatives

    def as_pointers(self) -> list[str]:
        lines = [f"+ {t.pointer}" for t in self.positives]
        lines += [f"- {t.pointer}" for t in self.negatives]
        return lines

    def as_text(self) -> str:
        return "\n".join(self.as_pointers())


def _split_points(summary: str) -> list[str]:
    """One takeaway per bullet; long bullets are split into sentences."""
    points: list[str] = []
    for raw_line in (summary or "").splitlines():
        line = _BULLET_PREFIX.sub("", raw_line).strip()
        if len(line) < 15:
            continue
        if len(line) > 220:
            points.extend(p.strip() for p in _SENTENCE_SPLIT.split(line) if len(p.strip()) > 15)
        else:
            points.append(line)
    return points


def _score(text: str) -> tuple[int, list[str]]:
    """Positive minus negative weight. Negated negatives are not counted as negatives."""
    lowered = text.lower()
    score = 0
    matched: list[str] = []

    for term, weight in POSITIVE_TERMS.items():
        if term in lowered:
            score += weight
            matched.append(f"+{term}")

    for term, weight in NEGATIVE_TERMS.items():
        index = lowered.find(term)
        if index < 0:
            continue
        # "margin pressure eased" / "no slowdown" - the negative is being denied.
        window = lowered[max(0, index - 45) : index + len(term) + 25]
        if _NEGATED.search(window):
            matched.append(f"~{term}")
            continue
        score -= weight
        matched.append(f"-{term}")

    return score, matched


def extract_takeaways(
    summary: str, max_positive: int = MAX_POSITIVE, max_negative: int = MAX_NEGATIVE
) -> ConcallTakeaways:
    """Top positive and negative points from a Screener.in concall summary.

    Ties break toward the earlier bullet, because Screener lists the most material points
    first and a stable order matters more than a marginal score difference.
    """
    points = _split_points(summary)
    scored = [(i, p, *_score(p)) for i, p in enumerate(points)]

    positives = sorted(
        (Takeaway(p, s, m) for i, p, s, m in scored if s > 0),
        key=lambda t: (-t.score, points.index(t.text)),
    )
    negatives = sorted(
        (Takeaway(p, s, m) for i, p, s, m in scored if s < 0),
        key=lambda t: (t.score, points.index(t.text)),
    )

    return ConcallTakeaways(
        positives=positives[:max_positive],
        negatives=negatives[:max_negative],
        source_points=len(points),
    )

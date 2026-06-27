"""Similarity engine for finding issues that match a given failure description.

V1 scoring uses three weighted signals:
  - Title similarity    (0.3)  — Jaccard token overlap
  - Description/stacktrace similarity (0.5) — search-engine score (BM25+semantic)
  - Metadata overlap    (0.2)  — component, version, health-warning match

Also provides deterministic failure fingerprinting for duplicate detection.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ceph_issue_kb.models import NormalizedIssue
    from ceph_issue_kb.search.engine import SearchEngine

TITLE_WEIGHT = 0.3
DESCRIPTION_WEIGHT = 0.5
METADATA_WEIGHT = 0.2


@dataclass
class SimilarityResult:
    """One result from the similarity engine."""

    issue: NormalizedIssue
    similarity: float  # 0–1
    matched_signals: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _jaccard(text_a: str, text_b: str) -> float:
    """Token-level Jaccard similarity between two texts."""
    a, b = _tokenize(text_a), _tokenize(text_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _normalize_stacktrace(stacktrace: str) -> str:
    """Strip volatile parts of a stacktrace to get a stable key."""
    text = re.sub(r"0x[0-9a-fA-F]+", "ADDR", stacktrace)
    text = re.sub(r"line \d+", "line N", text)
    text = re.sub(r'File "[^"]*/', 'File "', text)
    text = re.sub(r"\b\d{5,}\b", "NUM", text)
    return text.strip().lower()


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------

def fingerprint(
    stacktrace: str,
    assertion: str = "",
    component: str = "",
) -> str:
    """Deterministic 16-char hex fingerprint for a failure.

    The same failure across different issues produces the same fingerprint,
    enabling instant duplicate detection.
    """
    normalized = _normalize_stacktrace(stacktrace) if stacktrace else ""
    key = f"{normalized}:{assertion.strip().lower()}:{component.strip().lower()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Similarity Engine
# ---------------------------------------------------------------------------

class SimilarityEngine:
    """Score how similar a failure description is to known issues."""

    def __init__(self, search_engine: SearchEngine) -> None:
        self._search = search_engine

    def find_similar(
        self,
        description: str | None,
        stacktrace: str | None = None,
        component: str | None = None,
        limit: int = 10,
    ) -> list[SimilarityResult]:
        """Find issues similar to the given failure description.

        Returns up to *limit* results sorted by descending similarity score.
        """
        description = description or ""
        stacktrace = stacktrace or ""
        if not description and not stacktrace:
            return []

        query_text = description
        if stacktrace:
            query_text = f"{query_text} {stacktrace}".strip()

        candidates = self._search.search(query_text, limit=limit * 3)
        if not candidates:
            return []

        max_score = max(c.score for c in candidates) if candidates else 1.0
        max_score = max_score if max_score > 0 else 1.0

        results: list[SimilarityResult] = []
        for sr in candidates:
            issue = sr.issue
            signals: list[str] = []

            title_sim = _jaccard(query_text, issue.title)
            if title_sim > 0.05:
                signals.append(f"similar title ({title_sim:.0%})")

            desc_sim = sr.score / max_score
            if stacktrace and issue.stacktraces:
                st_sims = [_jaccard(stacktrace, st) for st in issue.stacktraces]
                best_st = max(st_sims)
                if best_st > 0.05:
                    signals.append(f"similar stacktrace ({best_st:.0%})")
                    desc_sim = max(desc_sim, best_st)
            if desc_sim > 0.1:
                signals.append(f"similar description ({desc_sim:.0%})")

            meta_sim = self._metadata_similarity(
                component, issue, signals,
            )

            score = (
                TITLE_WEIGHT * title_sim
                + DESCRIPTION_WEIGHT * min(desc_sim, 1.0)
                + METADATA_WEIGHT * meta_sim
            )

            results.append(SimilarityResult(
                issue=issue,
                similarity=min(score, 1.0),
                matched_signals=signals,
            ))

        results.sort(key=lambda r: r.similarity, reverse=True)
        return results[:limit]

    @staticmethod
    def _metadata_similarity(
        component: str | None,
        issue: NormalizedIssue,
        signals: list[str],
    ) -> float:
        """Compute metadata-overlap score and append explanations to *signals*."""
        parts: list[float] = []

        if component:
            if component.lower() in [c.lower() for c in issue.components]:
                parts.append(1.0)
                signals.append(f"same component ({component})")
            else:
                parts.append(0.0)

        if issue.health_warnings:
            parts.append(0.5)
            hw_str = ", ".join(issue.health_warnings[:3])
            signals.append(f"health warnings: {hw_str}")

        if issue.assertions:
            parts.append(0.5)
            signals.append(f"has assertions ({len(issue.assertions)})")

        return sum(parts) / max(len(parts), 1)

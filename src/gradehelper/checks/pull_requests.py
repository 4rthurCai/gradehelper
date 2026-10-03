"""Individual pull requests: existence, description quality, peer reviews."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Iterable

from ..clients.gitea import GiteaClient
from ..config import PrDescriptionSettings, ReviewSettings
from ..models import Finding, Student, as_datetime, extract_student_id

log = logging.getLogger(__name__)
_UNFILLED_MARKERS = (
    r"<\s*\d+\s*>",
    r"<\s*[^>]+\s*>",
    r"\[\s*\]",
    r"- \s*$",
    r"input:\s*$",
    r"expected output:\s*$",
    r"observed output:\s*$",
)
_MIN_BODY = 50
_MARKERS_FOR_FULL_SCORE = 10.0
_SIMILARITY_WEIGHT = 0.6


@dataclass(frozen=True)
class IndividualPull:
    repo: str
    number: int
    owner_id: str
    title: str
    body: str


def individual_pulls(repo: str, pulls: Iterable[Any], hw: str, known_ids: set[str]) -> list[IndividualPull]:
    """PRs whose title mentions hN and a known 12-digit student ID."""
    found = []
    hw_pattern = re.compile(rf"\b{re.escape(hw)}\b")
    for pull in pulls:
        title = pull.title or ""
        if not hw_pattern.search(title):
            continue
        match = re.search(r"(?<!\d)(\d{12})(?!\d)", title)
        if not match:
            continue
        if match.group(1) not in known_ids:
            log.warning("%s PR #%s: student ID %s not in roster", repo, pull.number, match.group(1))
            continue
        found.append(IndividualPull(repo, pull.number, match.group(1), title, pull.body or ""))
    return found


def missing_pr_findings(students: Iterable[Student], pulls: list[IndividualPull]) -> list[Finding]:
    owners = {p.owner_id for p in pulls}
    return [Finding(s.id, "noIndividualPR") for s in students if s.id not in owners]


# ---------- PR description ----------

def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\d+", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"<[^>]*>", "", text)
    return text.strip()


def unfilled_marker_count(body: str) -> int:
    return sum(len(re.findall(m, body, re.MULTILINE)) for m in _UNFILLED_MARKERS)


def template_score(body: str, templates: list[str]) -> float:
    """0..1, higher means the body looks like an unfilled template."""
    if not body or len(body.strip()) < _MIN_BODY:
        return 1.0
    normalized = _normalize(body)
    similarity = max(
        (SequenceMatcher(None, normalized, _normalize(t)).ratio() for t in templates if t),
        default=0.0,
    )
    markers = min(unfilled_marker_count(body) / _MARKERS_FOR_FULL_SCORE, 1.0)
    return similarity * _SIMILARITY_WEIGHT + markers * (1 - _SIMILARITY_WEIGHT)


def description_findings(
    pulls: list[IndividualPull], templates: list[str], settings: PrDescriptionSettings
) -> list[Finding]:
    """Judge each student by their best-written PR for this homework."""
    best: dict[str, tuple[float, IndividualPull]] = {}
    for pull in pulls:
        score = template_score(pull.body, templates)
        if pull.owner_id not in best or score < best[pull.owner_id][0]:
            best[pull.owner_id] = (score, pull)
    findings = []
    for owner, (score, pull) in best.items():
        log.info("%s PR #%s description score %.0f%%", pull.repo, pull.number, score * 100)
        if score > settings.unfilled_threshold:
            findings.append(
                Finding(
                    owner,
                    "notWritingPR",
                    details=(f"PR #{pull.number} description looks like an unfilled template",),
                )
            )
    return findings


# ---------- reviews ----------

def is_low_quality_review(text: str | None, settings: ReviewSettings) -> bool:
    if not text or not text.strip():
        return True
    lowered = text.strip().lower()
    if len(lowered) < settings.min_length:
        return True
    remaining = lowered
    for phrase in settings.perfunctory_phrases:
        remaining = re.sub(r"\b" + re.escape(phrase) + r"[.!,]*\b", "", remaining, flags=re.IGNORECASE)
    remaining = re.sub(r"[.,!?\s]+", " ", remaining).strip()
    return len(remaining) / len(lowered) < settings.min_substantive_ratio


@dataclass(frozen=True)
class ReviewEvent:
    """A comment, review or line comment left on someone's PR."""

    author: Any  # Gitea user object
    body: str
    created: Any
    kind: str


def _review_events(gitea: GiteaClient, pull: IndividualPull) -> list[ReviewEvent]:
    events = [
        ReviewEvent(c.user, c.body or "", getattr(c, "created_at", None), "comment")
        for c in gitea.pull_comments(pull.repo, pull.number)
    ]
    for review in gitea.pull_reviews(pull.repo, pull.number):
        events.append(ReviewEvent(review.user, review.body or "", getattr(review, "submitted_at", None), "review"))
        if review.id is not None:
            events += [
                ReviewEvent(c.user, c.body or "", getattr(c, "created_at", None), "line comment")
                for c in gitea.review_comments(pull.repo, pull.number, review.id)
            ]
    return events


def reviewers_of(
    gitea: GiteaClient,
    pull: IndividualPull,
    known_ids: set[str],
    settings: ReviewSettings,
    cutoff: datetime | None,
) -> set[str]:
    """Student IDs that left a substantive, on-time review on this PR."""
    reviewers = set()
    for event in _review_events(gitea, pull):
        if event.author is None:
            continue
        created = as_datetime(event.created)
        if cutoff is not None and (created is None or created > cutoff):
            continue
        author_id = extract_student_id(event.author.full_name or event.author.login)
        if author_id is None or author_id == pull.owner_id or author_id not in known_ids:
            continue
        if is_low_quality_review(event.body, settings):
            log.warning(
                "%s PR #%s: low-quality %s by %s: %r",
                pull.repo, pull.number, event.kind, author_id, event.body[:50],
            )
            continue
        reviewers.add(author_id)
    return reviewers


def review_findings(students: Iterable[Student], reviewer_ids: set[str]) -> list[Finding]:
    return [Finding(s.id, "noReview") for s in students if s.id not in reviewer_ids]

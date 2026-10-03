from datetime import datetime, timedelta, timezone

import pytest

from gradehelper.checks import pull_requests as pr
from gradehelper.checks import release
from gradehelper.config import PrDescriptionSettings, ReviewSettings
from gradehelper.models import Roster

from .conftest import ALICE, BOB, CAROL, ns, user

KNOWN = {ALICE.id, BOB.id, CAROL.id}
REVIEW = ReviewSettings(perfunctory_phrases=["lgtm", "looks good", "thanks", "good job"])
TZ = timezone(timedelta(hours=8))
CUTOFF = datetime(2026, 10, 3, 23, 59, 59, tzinfo=TZ)


def pull(number, title, body=""):
    return ns(number=number, title=title, body=body)


def test_individual_pulls_filter_by_hw_and_known_id():
    pulls = [
        pull(1, f"h2 {ALICE.id} Zhang San"),
        pull(2, f"h12 {BOB.id}"),            # different homework
        pull(3, "h2 team merge"),             # no student id
        pull(4, "h2 520000000099"),           # unknown id
        pull(5, f"[h2] {BOB.id}"),
    ]
    found = pr.individual_pulls("hteam01", pulls, "h2", KNOWN)
    assert [(p.number, p.owner_id) for p in found] == [(1, ALICE.id), (5, BOB.id)]
    missing = pr.missing_pr_findings([ALICE, BOB, CAROL], found)
    assert [f.student_id for f in missing] == [CAROL.id]


def test_template_score():
    template = "## What I did\n<1>\n## Bugs\n- \n## Self evaluation\n[ ]\n"
    assert pr.template_score("", [template]) == 1.0
    assert pr.template_score(template * 3, [template]) > 0.6
    written = (
        "I implemented the matrix parser and fixed an off-by-one bug in the loop bounds. "
        "Tests now cover empty input and negative sizes; I think the error messages could be clearer."
    )
    assert pr.template_score(written, [template]) < 0.6


def test_description_uses_best_pr_per_student():
    template = "## What I did\n<1>\n## Bugs\n- \n"
    pulls = [
        pr.IndividualPull("r", 1, ALICE.id, "h2", template),
        pr.IndividualPull("r", 2, ALICE.id, "h2", "A thorough description of the work done this week, " * 3),
        pr.IndividualPull("r", 3, BOB.id, "h2", ""),
    ]
    findings = pr.description_findings(pulls, [template], PrDescriptionSettings())
    assert [f.student_id for f in findings] == [BOB.id]


def test_low_quality_review():
    assert pr.is_low_quality_review("LGTM!", REVIEW)
    assert pr.is_low_quality_review("looks good, thanks, good job", REVIEW)
    assert not pr.is_low_quality_review("Consider extracting the parsing loop into a helper.", REVIEW)


class FakeGitea:
    def __init__(self, comments=(), reviews=(), line_comments=()):
        self._comments, self._reviews, self._lines = comments, reviews, line_comments

    def pull_comments(self, repo, number):
        return list(self._comments)

    def pull_reviews(self, repo, number):
        return list(self._reviews)

    def review_comments(self, repo, number, review_id):
        return list(self._lines)


def test_reviewers_respect_owner_quality_and_cutoff():
    good = "The loop on line 12 never terminates for n = 0; add a guard."
    gitea = FakeGitea(
        comments=[
            ns(user=user(ALICE.id), body=good, created_at=CUTOFF),             # owner: ignored
            ns(user=user(BOB.id), body="lgtm", created_at=CUTOFF),            # low quality
            ns(user=None, body=good, created_at=CUTOFF),
        ],
        reviews=[ns(id=7, user=user(BOB.id), body="", submitted_at=CUTOFF)],
        line_comments=[
            ns(user=user(CAROL.id), body=good, created_at=CUTOFF + timedelta(seconds=1)),  # late
            ns(user=user(BOB.id), body=good, created_at="2026-10-02T10:00:00+08:00"),
        ],
    )
    p = pr.IndividualPull("hteam01", 1, ALICE.id, "h2", "")
    assert pr.reviewers_of(gitea, p, KNOWN, REVIEW, CUTOFF) == {BOB.id}
    assert pr.reviewers_of(gitea, p, KNOWN, REVIEW, None) == {BOB.id, CAROL.id}
    assert [f.student_id for f in pr.review_findings([ALICE, BOB], {BOB.id})] == [ALICE.id]


def test_release_matches_tag_only():
    releases = [ns(name="h2", tag_name="h2-final"), ns(name="Homework 2", tag_name="h2")]
    assert release.find_release(releases, "h2").name == "Homework 2"
    assert release.find_release([ns(name="h2", tag_name="v2")], "h2") is None


def status(**kwargs):
    return release.ReleaseStatus(**{"tag_exists": True, "exists": True, "status": "success", **kwargs})


GROUP_DL = CUTOFF  # 10/3 23:59 in these tests
FINAL_DL = CUTOFF + timedelta(hours=24)


@pytest.mark.parametrize(
    "case, st, cutoff, accepted, reason",
    [
        ("A on time", status(created=GROUP_DL - timedelta(hours=1)), GROUP_DL, True, None),
        ("B late, group run", status(created=GROUP_DL + timedelta(hours=5)), GROUP_DL, False, "after the cutoff"),
        ("B late, final run", status(created=GROUP_DL + timedelta(hours=5)), FINAL_DL, True, None),
        ("C over 24h, final run", status(created=FINAL_DL + timedelta(hours=1)), FINAL_DL, False, "after the cutoff"),
        ("D no tag", status(tag_exists=False, exists=False), FINAL_DL, False, "tags/h2 missing"),
        ("D tag only", status(exists=False), FINAL_DL, False, "no h2 release was created"),
        ("E draft only", status(exists=False, draft_only=True), FINAL_DL, False, "only a draft"),
    ],
)
def test_submission_cases(roster: Roster, case, st, cutoff, accepted, reason):
    team = roster.teams[0]
    findings, ok = release.submission_findings(team, "h2", st, cutoff)
    assert ok is accepted, case
    if reason is None:
        assert findings == []
    else:
        assert {f.key for f in findings} == {"groupFailSubmit"}
        assert reason in findings[0].details[0]
        assert len(findings) == len(team.members)


def test_red_cross_only_for_accepted_submissions(roster: Roster):
    team = roster.teams[0]
    red = status(status="failure", created=GROUP_DL)
    findings, ok = release.submission_findings(team, "h2", red, GROUP_DL)
    assert ok and {(f.key, f.details) for f in findings} == {("jojFailCompile", ("h2 release JOJ workflow failure",))}
    assert release.submission_findings(team, "h2", status(status="pending", created=GROUP_DL), GROUP_DL) == ([], True)


def test_drafts_are_not_releases():
    releases = [ns(name="h2", tag_name="h2", draft=True), ns(name="v2", tag_name="h2-old", draft=False)]
    assert release.find_release(releases, "h2") is None


class StatusGitea:
    def __init__(self, statuses):
        self._statuses = statuses

    def releases(self, repo):
        return [ns(name="h2", tag_name="h2", created_at=CUTOFF)]

    def tag_commit(self, repo, tag):
        return "a" * 40

    def latest_status(self, repo, sha, context_prefix):
        from gradehelper.clients.gitea import GiteaClient

        fake = ns(repos=ns(repo_list_statuses=lambda *a, page: self._statuses if page == 1 else []), org="o")
        return GiteaClient.latest_status(fake, repo, sha, context_prefix)


def test_status_uses_newest_release_workflow_status_only(roster: Roster):
    statuses = [
        ns(status="failure", context="Run JOJ3 on Push / run (push)", created_at=CUTOFF + timedelta(minutes=5)),
        ns(status="success", context="Run JOJ3 on Release / run (release)", created_at=CUTOFF + timedelta(minutes=1)),
        ns(status="pending", context="Run JOJ3 on Release / run (release)", created_at=CUTOFF),
    ]
    status = release.release_status(StatusGitea(statuses), roster.teams[0], "h2", "Run JOJ3 on Release")
    assert status.status == "success" and not status.failed


def test_lateness_is_against_the_group_deadline(roster: Roster):
    team = roster.teams[0]
    on_time = status(created=GROUP_DL - timedelta(hours=1))
    late = status(created=GROUP_DL + timedelta(hours=30))  # rejected in the final run, still shown as late
    assert not release.lateness(team, on_time, GROUP_DL)[ALICE.id].late
    meta = release.lateness(team, late, GROUP_DL, notes=("check tag",))[BOB.id]
    assert meta.late and meta.release_time.startswith("2026-10-05") and meta.notes == ("check tag",)
    assert release.lateness(team, status(tag_exists=False, exists=False), GROUP_DL)[ALICE.id].late

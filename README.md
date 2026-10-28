# ENGR1510J Grade Helper

[中文说明](README.zh-CN.md)

Automated homework grading for SJTU-JI ENGR1510J: checks Gitea repositories, pull requests,
reviews and the JOJ scoreboard, writes per-homework CSVs, warns students on Mattermost, posts
a feedback issue in each team repo and uploads grades to Canvas. No Joint-Teapot needed.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                 # add '.[dev]' for tests
brew install universal-ctags     # C/C++ code-quality checks
cp /path/to/Joint-Teapot/.env .env   # same variable names; see .env.example
gradehelper roster sync          # hteams.csv from Canvas hteam groups; rerun after drops/team changes
gradehelper doctor               # checks config, credentials, ctags, roster
```

Gitea reads use your own `GITEA_ACCESS_TOKEN`. The feedback issues and replies are posted as the
course bot with `GITEA_BOT_ACCESS_TOKEN` (bot-engr151; `write:issue` is enough), and `feedback`
refuses to run without it.

`roster sync` lists what changed (left the course, no longer in a team, moved, joined) and
asks before overwriting `hteams.csv`; students without a team stay in the file but are not
graded. Only `hteams.csv` is used; the legacy `hteams.json` / `p3teams.json` can be deleted.

Git access uses SSH (`GIT_HOST`, default `ssh://git@focs.gc.sjtu.edu.cn:2222`); repositories are
cached in `repos/`.

## Configure a homework

One file per homework, `config/homeworks/hN.toml`:

```toml
language = "c"                 # matlab | c | cc
pass_threshold = 300           # JOJ hN total at/above this passes all JOJ checks
mandatory = ["main.c", "ex1.c"]
optional = ["ex1.h"]

[deadlines]                    # normally empty, see below
```

Deadlines come from the Canvas assignment named `hN`: group = its due date, final = its
"available until" date (else group + `final_grace_hours`), individual = group minus
`individual_days_before_group` (2) days, individual_final = individual +
`individual_grace_hours` (24). `gradehelper doctor` lists them all and every run says where
they came from. To override, set `individual`, `individual_final`, `group` or `final` in
`[deadlines]` (with a UTC offset); with `group` set, Canvas is not contacted. If several
Canvas assignments start with `hN`, set `canvas_assignment_id`.

JOJ exercise maxima need no configuration: they are read from the JOJ3 configs in
engr151-joj (`master`): `hN/conf-release.json` gives the graded exercises and the release
maxima, `hN/exK/conf.json` the individual maxima. Stages of one exercise (`ex1`,
`ex1-asan`, `[run] ex1-valgrind`, ...) are added up. `[joj.exercises]` / `[joj.release]` in
`hN.toml` override them if ever needed.

The rubric, score floor, tidy allowlist, review heuristics and the feedback issue texts are in
`config/course.toml`.

## Grading a homework: four runs

| When | Command | What happens | Then |
| --- | --- | --- | --- |
| individual deadline | `gradehelper individual 3 --warn` (alias `indv`, `i`) | individual branches, individual PRs, PR descriptions, JOJ scoreboard at the deadline. **A reminder only**: Mattermost warnings (no PR, JOJ below the line, ...) | – |
| individual deadline + 1 day | `gradehelper individual-final 3` (alias `indv-final`) | the same checks again, JOJ scoreboard at this time → `hws/h3indv.csv`. **This is the individual score**; students whose problems are gone since the deadline get a `TA Notes` entry | – |
| group deadline | `gradehelper group 3` (alias `g`) | `h3` tag contents and code quality, peer reviews, release JOJ status; records teams without an on-time release → `hws/h3.csv`. **Not uploaded** | `gradehelper feedback 3` opens the `h3 feedback` issue in every team repo |
| group deadline + 1 day | `gradehelper final 3` (alias `f`) | same group checks with the later cutoff, so late releases are graded too; `Late` still means no release by the group deadline → `hws/h3.csv` | `gradehelper feedback 3` replies in the issues; push `hws/h3.csv` to hw-scoreboard; TAs answer in the issues and adjust the sheet; pull; `gradehelper upload 3` |

`gradehelper run 3` picks the right run from the deadlines (the +1 day runs are
`individual + individual_grace_hours` and `group + final_grace_hours`, or the values in
`[deadlines]`), asks, and runs it.
Before a deadline it runs that stage as a preview; after it, the stage is due again until it
has been run after the deadline, so a preview never makes `run` skip the real run.
`gradehelper ui` does all of this in a local web page.

To rerun a specific stage, call it directly: `gradehelper individual|individual-final|group|final 3` run
any time and can be repeated; add `-t 5` to redo only some teams (other teams' saved results
are kept) and `-d <time>` to replay an earlier cutoff. Reruns only read remote systems; a
TA-edited CSV is kept unless `--overwrite`. Rerunning `individual` or `individual-final`
checks branches and PRs as they are *now*. For testing without touching real results, use a copy of the working
directory (its own `hws/`; `.env` and `repos/` can be symlinked).

Other commands: `gradehelper warn 3 --stage group` (warnings from saved results),
`gradehelper feedback 3 [--stage group|final]`, `gradehelper show 3 [--all]`,
`gradehelper upload 3 [--force]`.

### Feedback issues

`gradehelper feedback 3` works on the latest group run (`--stage` picks one) and posts as the
bot, after a preview:

- after `group`: opens `h3 feedback` in each team repo, assigned to all collaborators, with the
  auto-grader result at the group deadline: a table of deductions (problem, who: "whole team" or
  the student, points), the details, and "TAs might detect more during the manual review. Feel
  free to improve your submission if you wish." With no deduction it says so in one sentence.
- after `final`: replies in that issue with the result 24 hours later. The table also has
  the individual deductions (frozen at individual deadline + 1 day; appeals can cover them), then
  each student's homework score, ending with "If you want to provide more information regarding
  those issues, please reply in this thread." (or "No problem detected by the auto-grader.").
  Teams without a release by the group deadline get the same reply with "Late submission, no
  feedback provided. ..." at the end.

TAs then answer in the same issue by hand (final feedback; for late teams, who to appeal to).
Nothing is posted twice: an existing `h3 feedback` issue is not reopened and an issue that
already has the reply is skipped, so `feedback` can be rerun safely. The group issue lists
only group deductions and no score (the individual part is on Mattermost). All texts are in
`[feedback]` of `config/course.toml`.

Common options: `-t/--teams "1,5"` (only these teams; other teams' saved results are kept),
`-d/--deadline 2026-10-11T23:59:59+08:00` (override the run's cutoff), `-w/--warn`,
`-y/--yes` (skip confirmations), `--overwrite` (see below), `-h/--help` on every command.

### Release timing (example: individual 9/30, group 10/2 23:59)

A submission is a published release with tag `hN` created before the run's cutoff. Results do
not depend on when a run is executed.

| Release created | Group run (cutoff 10/2 23:59, feedback issue) | Final run (cutoff 10/3 23:59) | Late |
| --- | --- | --- | --- |
| by 10/2 23:59 | graded | graded | No |
| 10/2 23:59 – 10/3 23:59 | not submitted, -2.5 (in the issue) | graded; no deduction for lateness | Yes, late note at the end of the reply |
| after 10/3 23:59 | -2.5 | rejected, -2.5 | Yes, late note at the end of the reply |
| none / draft only / tag only | -2.5 | -2.5 | Yes, late note at the end of the reply |

Teams that are not submitted get a single `groupFailSubmit` with the reason; their tag
contents, compile status and group JOJ are not graded. Peer reviews always are (cutoff of the
run). If the `hN` tag points to a commit without a JOJ release run (tag moved after the
release?), the `TA Notes` column says so; it is never shown to students.

### Frozen individual part

The individual deadline run (`hws/hN.indv.json`) is only a reminder. The individual part is
graded one day later and **frozen** in `hws/hN.indv-final.json`: fixes pushed within those 24
hours count, and the `TA Notes` column lists what each such student fixed (e.g. `fixed after
the individual deadline: noIndividualPR`). The group and final runs reuse it as is and do not
re-check branches or JOJ, so later pushes cannot change it. Without a +1 day run they fall back
to the deadline run and say so; if no individual result exists at all, one is generated from
PRs + JOJ only (branches not checked).

### The spreadsheet is the source of truth after the final run

The group run is never posted on Canvas: `upload` refuses until the final run exists. TAs
edit `hws/hN.csv` in the hw-scoreboard repo; `upload` posts exactly that file (git
pull/push is up to you). gradehelper remembers what it last wrote (`hws/.gradehelper-written.json`)
and **refuses to overwrite a CSV that was edited or pulled from elsewhere**; it says so and
keeps the file. Use `--overwrite` only when you really want to regenerate it.

### Manual adjustments

Edit them in the web UI ("adjust" on a row) or in `hws/hN.overrides.toml`; they are reapplied
on every re-run and shown to the student as a note:

```toml
[[override]]
student = "520000000001"       # student ID or jaccount; or team = "hteam-03"
remove = ["noReview"]
note = "review left on Mattermost, checked by TA"
```

Overrides are for adjustments made before the sheet goes to the TAs; after the final run the
TA-edited CSV is what counts.

### Safety

Grading runs only read Canvas, Gitea and git. Sending warnings, posting feedback issues and
uploading always show a preview first and need confirmation (`--yes` to skip on the CLI). Uploads are recorded in
`hws/hN.uploaded.json`; unchanged grades are not re-posted, so re-uploading does not spam
Canvas comments. The web UI listens on 127.0.0.1 only and rejects requests without its
per-session token.

## Rubric

| Key | Points | Stage | When |
| --- | --- | --- | --- |
| `indvFailSubmit` | -1 | individual | branch, `hN/`, mandatory file or README missing; not on the JOJ scoreboard |
| `indvUntidy` | -0.25 | individual | extra files in the individual branch |
| `noIndividualPR` | -0.5 | individual | no PR titled with `hN` and the student ID |
| `notWritingPR` | -0.25 | individual | best PR description still looks like the template |
| `jojFailHomework` | -0.5 | individual | scoreboard at the individual deadline + 1 day: below the pass threshold and exercise average < 25% |
| `jojFailExercise` | -0.25 ×(≤2) | individual | same, graded exercise < 10% |
| `groupFailSubmit` | -2.5 | group | not submitted by the run's cutoff (no `hN` tag, tag without a published release, draft only, or release created after the cutoff); or `hN/`, a mandatory file or README missing in the tag |
| `groupUntidy` | -0.25 | group | extra files in the tag |
| `groupLowCodeQuality` | -0.5 × issue types | group | static checks on submitted files |
| `noReview` | -1 | group | no substantive review on a teammate's PR before the cutoff |
| `jojFailCompile` | -2.5 | group | red cross: the newest `Run JOJ3 on Release` status of the tagged commit is failure/error |
| `jojGroupFailHomework` | -0.5 | group | JOJ3 run of the `hN` release (result issue in the team repo): exercise average < 50% |
| `jojGroupFailExercise` | -0.25 ×(≤2) | group | same run, exercise < 25% |

Total per homework is floored at -2.5. JOJ thresholds are in `[joj.individual]` / `[joj.group]`
of `config/course.toml`. The release run is the result issue whose commit is the `hN` tag.

## Layout

```
config/            course.toml, homeworks/hN.toml
templates/         PR templates used for the description check
src/gradehelper/
  clients/         canvas, gitea, mattermost, git (thin, lazy)
  checks/          layout, code_quality, repo, pull_requests, release, joj, joj_config, joj_release
  grading.py       rubric → score + comment (only place with rubric math)
  report.py        stage results (JSON) and CSVs
  overrides.py     manual adjustments
  feedback.py      the feedback issue in each team repo
  pipeline.py      the four runs
  notify.py        Mattermost warnings
  upload.py        Canvas upload
  cli.py, web/     interfaces
tests/             pytest; fictional data only
legacy/            old code, kept for comparison until the switch
```

## Development

```bash
pip install -e '.[dev]'
pytest --cov=gradehelper
```

Logs go to `gradehelper.log`.

# Setup and configuration

[Quick start](../README.md) · [中文](setup.zh-CN.md)

## Credentials and SSH

Grade Helper reads `.env` from the directory where you run it. Start with the example file or reuse the course's Joint-Teapot `.env`, which uses the same variable names. [Environment template](../.env.example)

| Service | Settings | Used for |
| --- | --- | --- |
| Canvas | `CANVAS_ACCESS_TOKEN`, `CANVAS_COURSE_ID` | Rosters, assignment dates and grade uploads |
| Gitea | `GITEA_ACCESS_TOKEN`, `GITEA_ORG_NAME` | Reading team repositories, PRs, reviews and releases |
| Gitea course bot | `GITEA_BOT_ACCESS_TOKEN` | Posting feedback issues and replies as `bot-engr151`. The bot needs `write:issue`. |
| Mattermost | `MATTERMOST_ACCESS_TOKEN`, `MATTERMOST_TEAM` | Sending reminders |

Cloning and fetching use your SSH credentials. The default `GIT_HOST` is `ssh://git@focs.gc.sjtu.edu.cn:2222`. Clones are cached in `repos/` (`REPOS_DIR`), and interrupted SSH connections are retried.

## Rosters

Run `gradehelper roster sync` when setting up the course or after a student joins, drops or changes teams. It reads Canvas hteam groups, shows the changes and asks before writing `hteams.csv`. Use `gradehelper roster show` to inspect the local roster. Students without a team stay on the roster and are excluded from grading. The CSV replaces the legacy `hteams.json` and `p3teams.json`.

Every grading run checks the roster against Canvas. Accept the changes to update the CSV and continue, or decline to stop. In the web UI, select **Update hteams.csv** on the roster page and run the stage again. The CLI option `--yes` accepts roster updates automatically. If Canvas is unavailable, `--skip-roster-check` (also available in the UI) uses the local roster. Deadlines may still require Canvas.

## Configure a homework

Course-wide settings include the rubric, score floor, layout allowlist, review criteria, JOJ thresholds and feedback issue text. [Course configuration](../config/course.toml)

For each homework, edit `config/homeworks/hN.toml`. Set the language (`matlab`, `c` or `cc`), the individual JOJ pass threshold and the mandatory and optional filenames. Here is a C example. [Course homework files](../config/homeworks)

```toml
language = "c"
pass_threshold = 300
mandatory = ["main.c", "ex1.c"]
optional = ["ex1.h"]

[deadlines]
```

### Deadlines

Leave `[deadlines]` empty to use the Canvas assignment for `hN`. Run `gradehelper doctor` to see the resolved dates and their source for each homework.

| Stage | Default cutoff |
| --- | --- |
| Individual | Group deadline minus `individual_days_before_group` (2 days) |
| Individual final | Individual deadline plus `individual_grace_hours` (24 hours) |
| Group | Canvas due date |
| Final | Canvas “available until”, falling back to group deadline plus `final_grace_hours` (24 hours) |

To override a cutoff, use `individual`, `individual_final`, `group` or `final` under `[deadlines]`. Include a UTC offset, e.g. `group = "2026-10-02T23:59:00+08:00"`. Setting `group` resolves the dates locally. The roster check is independent. If several Canvas assignments match `hN`, set `canvas_assignment_id` at the top of the homework file. An “Extra inputs are not permitted” error can indicate an obsolete key such as `review`. Reviews use the cutoff of the group or final run.

### JOJ exercise maxima

Grade Helper reads the JOJ3 configuration on the `master` branch of `engr151-joj`, under `home/tt/.config/joj/homework/hN/`. The release file `conf-release.json` identifies graded exercises and their release maxima. Each `exK/conf.json` supplies the individual maximum. Stages such as `ex1`, `ex1-asan` and `ex1-valgrind` contribute to the same exercise.

The run reports which maxima it used. If you see `group JOJ skipped`, check that the release configuration defines scored exercises. To override the individual or release maxima, set `[joj.exercises]` or `[joj.release]` in the homework file. [Example homework configuration](../config/homeworks/h3.toml)

## Development

```bash
pip install -e '.[dev]'
pytest --cov=gradehelper
```

With Ruff installed, run `ruff check src tests`. Use fictional names and student IDs in tests and examples. The pipeline runs the four stages, the checks produce findings, and the grading and report modules turn them into scores and CSVs. [Source code](../src/gradehelper) · [PR templates](../templates) · [Tests](../tests) · [Legacy reference implementation](../legacy)

The maintainer checkout pushes Grade Helper's `main` to GitHub and the FOCS `engr151/grade-helper` mirror. Push destinations belong to each local clone. Run `git remote get-url --push --all origin` to see yours. The hw-scoreboard repository used for grade CSVs has its own remote.

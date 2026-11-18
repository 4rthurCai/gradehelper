# Grading a homework

[Quick start](../README.md) · [中文](grading.zh-CN.md)

## Four runs for homework 3

Use `gradehelper run 3` at each deadline. It checks the schedule and saved run times, shows the stage it picked and asks before starting. A run before its cutoff is a preview. Run the command again after the cutoff to record the results for that deadline. You can also choose a stage directly.

| When | Command | Result and next action |
| --- | --- | --- |
| Individual deadline | `gradehelper individual 3 --warn` | Checks branches, PRs, PR descriptions and individual JOJ scores. Preview and send Mattermost reminders so students can fix problems. |
| Individual final (normally 24 hours later) | `gradehelper individual-final 3` | Saves the frozen individual results in `hws/h3.indv-final.json` and writes the scores to `hws/h3indv.csv`. The `TA Notes` column records problems fixed since the first run. |
| Group deadline | `gradehelper group 3` | Checks the `h3` release, tag contents, code quality, JOJ and peer reviews. Writes the combined scores to `hws/h3.csv`. Run `gradehelper feedback 3 --stage group` to open the feedback issues. |
| Final cutoff | `gradehelper final 3` | Repeats group checks with the later cutoff and updates `hws/h3.csv`. Run `gradehelper feedback 3 --stage final` to reply in the same issues, then send the CSV for TA review. |

The final cutoff comes from Canvas or the configured grace period. Run `gradehelper doctor` to check the exact times. For the web interface, run `gradehelper ui`. It opens on `127.0.0.1:8151` by default and uses a per-session token for changes. [Deadline configuration](setup.md#deadlines) · [Grading rules](rubric.md)

## Rerun or inspect a stage

A stage can be repeated directly at any time. Aliases are `i` or `indv` for `individual`, `indv-final` for `individual-final`, `g` for `group` and `f` for `final`.

```bash
gradehelper group 3 -t 5
gradehelper group 3 -t 5,12 -d 2026-10-02T23:59:59+08:00
gradehelper show 3 --all
```

- `-t/--teams` replaces saved results for the selected teams and keeps the other teams' results. Give the stage-specific commands a cutoff with `-d/--deadline`, including the UTC offset.
- Individual stages check branches and PRs as they exist when the command runs. A historical cutoff selects the JOJ scoreboard for that time. Group stages use the cutoff for release eligibility and peer reviews. The `Late` flag always uses the group deadline.
- `show` displays rows with deductions and refreshes the CSV. Use `--all` to see everyone. A TA-edited CSV is preserved unless you request `--overwrite`.
- For a trial run, use a separate checkout with its own `hws/`. The `.env` and `repos/` locations can be shared. Inspect `gradehelper.log` for run details or use `gradehelper group --help` for command options.

## Reminders and feedback

The grading checks read remote services and save results locally. To send reminders, add `--warn` to a run or use saved results, e.g. `gradehelper warn 3 --stage group`. The `warn --stage` values are `individual`, `individual_final`, `group` and `final`.

`gradehelper feedback 3` uses the saved final run if available, otherwise the group run. Use `--stage` to choose explicitly. The course bot posts after a preview.

- **Group issue.** The bot opens `h3 feedback` in each team repo and assigns the collaborators. It lists group deductions, who they apply to and the points. The message invites students to improve the submission before final review. Individual reminders go through Mattermost.
- **Final reply.** The bot adds the frozen individual deductions and each student's score. Teams can reply in the thread with information for an appeal. Teams marked late also receive the course's late-submission message. TAs follow up in the same issue and tell late teams whom to contact.

Rerunning `feedback` skips an existing group issue or a final reply that was already posted. The wording is configurable in the course file's `[feedback]` section. Reminders, feedback and uploads show a preview before sending. On the CLI, `-y/--yes` skips confirmation. [Course configuration](../config/course.toml)

## Adjust and upload grades

Before handing off the sheet, choose **adjust** on a row in the web UI or edit `hws/h3.overrides.toml`. Overrides are reapplied on reruns. The note appears in the student comment and feedback issue.

```toml
[[override]]
student = "520000000001"  # student ID or jaccount, or use team = "hteam-03"
remove = ["noReview"]
# add = { indvUntidy = 1 }
# score = -0.5
note = "Review left on Mattermost, checked by TA"
```

1. After the final run, commit and push the CSV in the hw-scoreboard repository (`hws/` in the grading checkout). TAs review feedback and edit the sheet there. The scoreboard tracks CSVs and ignores the local JSON results and TOML overrides.
2. Pull the reviewed sheet into `hws/h3.csv`. Grade Helper checks `hws/.gradehelper-written.json` before regenerating a CSV. A “keeping it” message means the existing file was preserved. Use `--overwrite` only to intentionally discard those edits.
3. Run `gradehelper upload 3`. It requires a saved final run and uploads scores and comments from the current CSV. Review the assignment, changes and unmatched rows before confirming.

Each successful upload is recorded in `hws/h3.uploaded.json`. A retry skips unchanged scores and comments. Use `--force` only to post those entries again.

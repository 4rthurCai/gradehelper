# Grading rules

[Grading workflow](grading.md) · [中文](rubric.zh-CN.md)

The automatic homework score starts at 0 and deductions are capped at a total of -2.5. The course configuration sets the points, JOJ thresholds and review criteria. [Course configuration](../config/course.toml)

## Deductions

| Individual check | Points | When it applies |
| --- | --- | --- |
| `indvFailSubmit` | -1 | Missing individual branch, `hN/`, mandatory file, homework README or entry on the JOJ scoreboard |
| `indvUntidy` | -0.25 | Extra files on the individual branch |
| `noIndividualPR` | -0.5 | No PR title includes both `hN` and the student's 12-digit ID |
| `notWritingPR` | -0.25 | Even the student's best PR description looks like an unfilled template |
| `jojFailHomework` | -0.5 | Below the individual JOJ total pass threshold and the average across graded exercises is below 25% |
| `jojFailExercise` | -0.25 per exercise, up to 2 | Below the individual JOJ total pass threshold and the exercise score is below 10% of its maximum |

| Group-stage check | Points | When it applies |
| --- | --- | --- |
| `groupFailSubmit` | -2.5 | No qualifying release by the cutoff, or the tag lacks `hN/`, a mandatory file or the homework README |
| `groupUntidy` | -0.25 | Extra files in the tag |
| `groupLowCodeQuality` | -0.5 per distinct issue type | Static checks find problems in submitted files, e.g. a non-const global variable in C/C++ |
| `noReview` | -1 | The student left no qualifying review on a teammate's PR by the cutoff |
| `jojFailCompile` | -2.5 | The release workflow on the tagged commit has a failure or error status |
| `jojGroupFailHomework` | -0.5 | The release JOJ average across graded exercises is below 50% |
| `jojGroupFailExercise` | -0.25 per exercise, up to 2 | A release exercise scores below 25% of its maximum |

## Frozen individual scores

The first individual run saves reminder results in `hws/hN.indv.json`. The individual final run saves `hws/hN.indv-final.json` and records fixes since the first run in the TA-only `TA Notes` column. Later group and final runs use the saved individual findings. If the individual final result is missing, they use the first run and report the fallback. If both are missing, they generate individual results from PRs and JOJ only, skipping branch checks.

Run individual stages promptly after the cutoff. Branches and PRs reflect their state at execution time. Individual JOJ uses the latest scoreboard commit at or before the cutoff on `engr151-joj`'s `grading` branch. If there is no earlier commit for that file, the tool logs a warning and reads the branch tip.

## Release timing

A submission needs a published Gitea release with the exact tag `hN` and a creation time at or before the stage cutoff. For a group cutoff of October 2 at 23:59 and a final cutoff of October 3 at 23:59, the submission check gives these results.

| Release created | Group run | Final run | Late |
| --- | --- | --- | --- |
| By the group cutoff | Graded | Graded | No |
| After the group cutoff, by the final cutoff | -2.5 | Graded with no deduction for lateness | Yes |
| After the final cutoff | -2.5 | -2.5 | Yes |
| No release, draft only or tag only | -2.5 | -2.5 | Yes |

An unaccepted release gets one `groupFailSubmit`. Grade Helper skips the tag contents, compile status and group JOJ checks for that release. Peer reviews still count up to the stage cutoff. The final feedback reply includes a late note for teams marked `Late`.

## Peer reviews and group JOJ

- **Peer review.** One substantive PR comment, submitted review or inline comment on a teammate's individual PR suffices. The title must identify the homework and PR owner's student ID, and the reviewer must be identifiable from the roster. By default, a review needs 15 characters and at least half its text must remain after removing stock phrases such as “LGTM” and “thanks”. The log records perfunctory reviews.
- **Compile status.** The latest `Run JOJ3 on Release` status on the tagged commit determines `jojFailCompile`. Only `failure` and `error` incur the deduction. Pending or missing status is logged. A zero JOJ score with a successful workflow is evaluated under the group exercise rules.
- **Group JOJ.** Grade Helper reads the team's JOJ3 result issue for `hN-release`, preferring the run whose commit matches the tag. If none matches, it uses the latest result at or before the cutoff. If result issues exist but none match the tagged commit, a TA-only note flags the mismatch. Group JOJ deductions apply to every team member.

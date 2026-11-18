# ENGR1510J Grade Helper

[中文](README.zh-CN.md)

Grade Helper checks Gitea submissions, peer reviews and JOJ scores for ENGR1510J. It prepares a CSV for TA review, sends reminders on Mattermost and uploads reviewed grades to Canvas.

## Set up

Run these commands from the repository root with Python 3.11 or newer. For C/C++ homework, install Universal Ctags (`brew install universal-ctags` on macOS).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp -n .env.example .env
```

Fill in the course credentials in `.env`, then sync the roster and check the setup.

```bash
gradehelper roster sync
gradehelper doctor
```

## Grade a homework

Run `gradehelper run 3` at each deadline for homework 3, or open the local interface with `gradehelper ui`. The first individual run is for reminders. The second freezes the individual scores.

After the group run, `gradehelper feedback 3` opens an issue in each team repository. Run it again after the final run to post the final feedback. Push `hws/h3.csv` to hw-scoreboard for TA review, pull the edited sheet, then run `gradehelper upload 3`.

## Documentation

- [Setup and configuration](docs/setup.md) covers credentials, roster changes, homework deadlines and JOJ settings.
- [Grading workflow](docs/grading.md) covers the four runs, reruns, feedback and Canvas uploads.
- [Grading rules](docs/rubric.md) lists deductions and explains how releases, reviews and JOJ scores are evaluated.

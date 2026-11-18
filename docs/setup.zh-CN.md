# 安装与配置

[快速开始](../README.zh-CN.md) · [English](setup.md)

## 凭据与 SSH

Grade Helper 从运行命令的目录读取 `.env`。可以从示例文件开始填写，也可以沿用课程的 Joint-Teapot `.env`，两者的变量名相同。[环境变量示例](../.env.example)

| 服务 | 变量 | 用途 |
| --- | --- | --- |
| Canvas | `CANVAS_ACCESS_TOKEN`、`CANVAS_COURSE_ID` | 读取名单、作业时间，上传成绩 |
| Gitea | `GITEA_ACCESS_TOKEN`、`GITEA_ORG_NAME` | 用自己的账号读取小组仓库、PR、评审和 release |
| Gitea 课程机器人 | `GITEA_BOT_ACCESS_TOKEN` | 以 `bot-engr151` 身份创建和回复反馈 issue，需要 `write:issue` 权限 |
| Mattermost | `MATTERMOST_ACCESS_TOKEN`、`MATTERMOST_TEAM` | 给学生发送提醒 |

克隆和更新仓库使用本机的 SSH 凭据。`GIT_HOST` 默认是 `ssh://git@focs.gc.sjtu.edu.cn:2222`。仓库缓存在 `repos/`，可以通过 `REPOS_DIR` 更改目录。SSH 连接中断后会自动重试。

## 名单与分组

首次使用或有学生加课、退课、换组时，运行 `gradehelper roster sync`。它会读取 Canvas 的 hteam 分组，列出变化，确认后写入 `hteams.csv`。用 `gradehelper roster show` 可查看本地名单。未分组的学生仍保留在名单里，评分时会跳过。旧版的 `hteams.json` 和 `p3teams.json` 已由 CSV 取代。

每轮批改前都会自动核对名单。接受变化会更新 CSV 并继续，拒绝则停止。网页会跳转到名单页，点 **Update hteams.csv** 后重新运行该轮。命令行加 `--yes` 会自动接受名单更新。连不上 Canvas 时，可以加 `--skip-roster-check` 或勾选网页上的同名选项，沿用本地名单。读取截止时间仍可能需要连接 Canvas。

## 配置作业

课程配置包括扣分规则、分数下限、允许出现的文件、评审判定条件、JOJ 阈值和反馈 issue 的文字。[课程配置文件](../config/course.toml)

每份作业使用一个 `config/homeworks/hN.toml`。填写语言（`matlab`、`c` 或 `cc`）、个人 JOJ 总分通过线、必交和选交文件。下面是一份 C 作业的示例。[现有作业配置](../config/homeworks)

```toml
language = "c"
pass_threshold = 300
mandatory = ["main.c", "ex1.c"]
optional = ["ex1.h"]

[deadlines]
```

### 截止时间

通常可以将 `[deadlines]` 留空，由工具读取 Canvas 上对应的 `hN` 作业。运行 `gradehelper doctor` 可以核对每份作业的四个截止时间及其来源。

| 轮次 | 默认截止时间 |
| --- | --- |
| 个人初评 | 小组截止时间往前推 `individual_days_before_group` 天，默认 2 天 |
| 个人终评 | 个人初评截止后 `individual_grace_hours` 小时，默认 24 小时 |
| 小组初评 | Canvas 作业截止时间（due） |
| 小组终评 | Canvas 作业的可用截止时间（available until）。若留空，则用小组截止时间加 `final_grace_hours`，默认 24 小时 |

可以在 `[deadlines]` 中用 `individual`、`individual_final`、`group` 或 `final` 覆盖对应时间，值必须带时区，例如 `group = "2026-10-02T23:59:00+08:00"`。设置 `group` 后，截止时间会在本地计算，名单仍会单独核对。如果 Canvas 上有多个作业名匹配 `hN`，在作业文件顶层设置 `canvas_assignment_id`。出现 “Extra inputs are not permitted” 时，检查是否还保留了旧字段，例如 `review`。同伴评审采用本轮小组初评或终评的截止时间。

### JOJ 每题满分

工具从 `engr151-joj` 的 `master` 分支读取 JOJ3 配置，目录为 `home/tt/.config/joj/homework/hN/`。`conf-release.json` 指定计分题目和小组 release 每题的满分，`exK/conf.json` 给出个人部分该题的满分。`ex1`、`ex1-asan`、`ex1-valgrind` 等属于同一题，各阶段分数相加。

每轮运行都会显示采用的满分。如果提示 `group JOJ skipped`，检查 release 配置是否定义了计分题目。需要手动指定时，在作业文件里用 `[joj.exercises]` 覆盖个人满分，或用 `[joj.release]` 覆盖小组满分。[作业配置示例](../config/homeworks/h3.toml)

## 开发

```bash
pip install -e '.[dev]'
pytest --cov=gradehelper
```

安装 Ruff 后可运行 `ruff check src tests`。测试和示例使用虚构的姓名和学号。`pipeline.py` 编排四轮批改，`checks/` 执行检查，`grading.py` 和 `report.py` 计算成绩并生成报表。[源码](../src/gradehelper) · [PR 模板](../templates) · [测试](../tests) · [旧版实现](../legacy)

维护者的工作副本会把 Grade Helper 的 `main` 同时推到 GitHub 和 FOCS 的 `engr151/grade-helper` 镜像。推送地址属于各自的本地克隆，可用 `git remote get-url --push --all origin` 查看。存放成绩 CSV 的 hw-scoreboard 有独立的远端配置。

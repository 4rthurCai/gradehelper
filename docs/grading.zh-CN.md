# 批改一份作业

[快速开始](../README.zh-CN.md) · [English](grading.md)

## 第 3 次作业的四轮批改

在每个截止时间运行 `gradehelper run 3`。工具会对照截止时间和已保存的运行时间，说明选中了哪一轮，确认后开始。截止前运行算预览，截止后要再运行一次，才能记录该轮的正式结果。也可以直接指定轮次。

| 时间 | 命令 | 结果与后续操作 |
| --- | --- | --- |
| 个人初评截止 | `gradehelper individual 3 --warn` | 检查个人分支、PR、PR 描述和个人 JOJ 成绩。预览后可在 Mattermost 提醒学生及时修改。 |
| 个人终评截止（通常在 24 小时后） | `gradehelper individual-final 3` | 将冻结的个人结果保存到 `hws/h3.indv-final.json`，并把分数写入 `hws/h3indv.csv`。初评后修好的问题会记在 `TA Notes` 列。 |
| 小组初评截止 | `gradehelper group 3` | 检查 `h3` release、tag 内容、代码质量、JOJ 和同伴评审，合并个人结果写入 `hws/h3.csv`。用 `gradehelper feedback 3 --stage group` 创建反馈 issue。 |
| 小组终评截止 | `gradehelper final 3` | 按终评截止时间重做小组检查，更新 `hws/h3.csv`。用 `gradehelper feedback 3 --stage final` 在原 issue 回复，再把成绩表交给助教复核。 |

小组终评采用 Canvas 可用截止时间或配置的宽限期，具体日期可用 `gradehelper doctor` 查看。运行 `gradehelper ui` 可打开本地网页，默认地址为 `127.0.0.1:8151`，提交修改时需要当前会话的令牌。[截止时间配置](setup.zh-CN.md#截止时间) · [评分规则](rubric.zh-CN.md)

## 重跑或查看某一轮

各轮次随时可以单独重跑。`individual` 的简写是 `i` 或 `indv`，另外三轮分别可以用 `indv-final`、`g` 和 `f`。

```bash
gradehelper group 3 -t 5
gradehelper group 3 -t 5,12 -d 2026-10-02T23:59:59+08:00
gradehelper show 3 --all
```

- `-t/--teams` 只更新指定组的结果，保留其他组已有的结果。直接指定轮次时，可加 `-d/--deadline` 覆盖本轮截止时间，时间值要带时区。
- 个人轮次检查的是运行当时的分支和 PR。指定过去的截止时间可以读取当时的 JOJ 成绩，无法还原后来修改的分支或 PR。小组轮次按指定截止时间判定 release 和同伴评审是否有效，`Late` 始终按小组初评截止时间判断。
- `show` 默认显示有扣分的学生，并刷新 CSV。加 `--all` 显示所有人。助教改过的 CSV 会被保留，除非显式指定 `--overwrite`。
- 试跑可以用独立工作目录和 `hws/`，`.env` 和 `repos/` 可以共用。详细日志在 `gradehelper.log`，命令选项可用 `gradehelper group --help` 等查看。

## 提醒与反馈

各轮检查读取远端数据，将结果保存在本地。需要发送 Mattermost 提醒时，在批改命令后加 `--warn`，或单独运行 `gradehelper warn 3 --stage group` 从已有结果生成提醒。`warn --stage` 支持 `individual`、`individual_final`、`group` 和 `final`。

`gradehelper feedback 3` 优先使用已保存的小组终评结果，其次使用小组初评结果。可以通过 `--stage` 指定轮次。预览后由课程机器人发送。

- **小组初评。** 机器人在每个小组仓库创建 `h3 feedback` issue，指派给所有协作者。扣分表列出小组阶段发现的问题、涉及全组还是某个学生，以及分值，并提醒学生可以在终评前修改。个人初评问题通过 Mattermost 提醒。
- **小组终评。** 机器人在原 issue 回复，列出已冻结的个人扣分和每名学生的成绩。学生可在讨论串中补充申诉信息，迟交的小组会收到额外的迟交说明。助教在同一个 issue 跟进，对迟交小组说明申诉联系人。

重跑 `feedback` 会跳过已经创建的 issue 和已经发出的终评回复。可以在课程文件的 `[feedback]` 中修改措辞。发送提醒、发布反馈和上传成绩前都会显示预览，命令行的 `-y/--yes` 会跳过确认。[课程配置](../config/course.toml)

## 手动调整与上传

交表前，可以在网页的成绩行点 **adjust**，或编辑 `hws/h3.overrides.toml`。每次重跑时会重新应用这些调整，备注会出现在学生看到的评语和反馈 issue 里。

```toml
[[override]]
student = "520000000001"  # 学号或 jaccount，也可以用 team = "hteam-03"
remove = ["noReview"]
# add = { indvUntidy = 1 }
# score = -0.5
note = "Review left on Mattermost, checked by TA"
```

1. 小组终评后，在 hw-scoreboard 仓库（批改目录下的 `hws/`）提交并推送 CSV，供助教复核反馈、修改表格。成绩仓库跟踪 CSV，本地的 JSON 结果和 TOML 调整文件会被忽略。
2. 拉取复核后的表格到 `hws/h3.csv`。工具重新生成 CSV 前会检查 `hws/.gradehelper-written.json`。看到 “keeping it” 表示保留了已有文件，只有确定要舍弃手改内容时才用 `--overwrite`。
3. 运行 `gradehelper upload 3`。它要求已有终评结果，上传的是当前 CSV 中的分数和评语。确认前可核对目标作业、变动和未匹配的行。

成功上传的记录保存在 `hws/h3.uploaded.json`。再次运行会跳过分数和评语都未变的学生，确实需要重复发布时可以加 `--force`。

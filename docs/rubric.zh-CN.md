# 评分规则

[批改流程](grading.zh-CN.md) · [English](rubric.md)

每份作业的自动评分从 0 开始扣，最低为 -2.5 分。各项分值、JOJ 阈值和同伴评审标准在课程配置中设置。[课程配置](../config/course.toml)

## 扣分项

| 个人检查项 | 分值 | 触发条件 |
| --- | --- | --- |
| `indvFailSubmit` | -1 | 缺个人分支、`hN/` 目录、必交文件、作业 README，或 JOJ 成绩表里找不到该学生 |
| `indvUntidy` | -0.25 | 个人分支里有多余文件 |
| `noIndividualPR` | -0.5 | 没有标题同时包含 `hN` 和本人 12 位学号的 PR |
| `notWritingPR` | -0.25 | 写得最好的一份 PR 描述仍像未填写的模板 |
| `jojFailHomework` | -0.5 | 个人 JOJ 总分未达通过线，且各计分题平均得分率低于 25% |
| `jojFailExercise` | 每题 -0.25，最多 2 题 | 个人 JOJ 总分未达通过线，且该题得分率低于 10% |

| 小组阶段检查项 | 分值 | 触发条件 |
| --- | --- | --- |
| `groupFailSubmit` | -2.5 | 本轮截止前未提交有效 release，或 tag 中缺少 `hN/` 目录、必交文件或作业 README |
| `groupUntidy` | -0.25 | tag 中有多余文件 |
| `groupLowCodeQuality` | 每类问题 -0.5 | 提交文件未通过静态检查，例如 C/C++ 代码中存在非 const 全局变量 |
| `noReview` | -1 | 学生在本轮截止前没有给队友的 PR 留过有效评审 |
| `jojFailCompile` | -2.5 | tag 对应提交的 release workflow 状态为 failure 或 error |
| `jojGroupFailHomework` | -0.5 | 小组 release 的各计分题平均得分率低于 50% |
| `jojGroupFailExercise` | 每题 -0.25，最多 2 题 | 小组 release 的某题得分率低于 25% |

## 个人成绩何时冻结

个人初评结果保存在 `hws/hN.indv.json`，供提醒学生使用。个人终评结果保存在 `hws/hN.indv-final.json`，初评后修好的问题会写入仅助教可见的 `TA Notes` 列。小组初评和终评直接沿用个人结果。缺少个人终评时，工具会提示并退回到个人初评。两轮都缺失时，则根据 PR 和 JOJ 生成一份个人结果，跳过分支检查。

个人轮次应尽量在截止时间刚过时运行，因为分支和 PR 反映的是实际运行时的内容。个人 JOJ 读取 `engr151-joj` 的 `grading` 分支中不晚于截止时间的最近一次成绩表提交。如果该文件在截止前没有提交，工具会记录警告并使用分支最新版本。

## Release 与迟交

小组提交必须是 Gitea 上已发布的 release，tag 恰好为 `hN`，创建时间不晚于本轮截止时间。以下以 10 月 2 日 23:59 为小组初评截止、10 月 3 日 23:59 为终评截止，列出提交检查的结果。

| Release 创建时间 | 小组初评 | 小组终评 | Late |
| --- | --- | --- | --- |
| 小组初评截止前或恰好截止时 | 正常评分 | 正常评分 | No |
| 小组初评截止后，到终评截止时 | -2.5 | 正常评分，迟交本身不扣分 | Yes |
| 小组终评截止后 | -2.5 | -2.5 | Yes |
| 未创建 release、仅有 draft 或仅有 tag | -2.5 | -2.5 | Yes |

被判定为未提交时，只记一次 `groupFailSubmit`，跳过该 release 的 tag 内容、编译状态和小组 JOJ 检查。同伴评审仍按本轮截止时间计算，标为 `Late` 的小组还会在终评反馈中收到迟交说明。

## 同伴评审与小组 JOJ

- **同伴评审。** 在队友的个人 PR 上留一条有实质内容的普通评论、review 或行内评论即可。PR 标题须标明作业和作者学号，评审者须能匹配到名单。默认至少 15 个字符，去掉 “LGTM”“thanks” 等套话后，剩余内容至少占一半。被判为敷衍的评论会记入日志。
- **编译状态。** `jojFailCompile` 看的是 tag 对应提交上最新的 `Run JOJ3 on Release` 状态。只有 `failure` 或 `error` 才扣分。状态仍为 pending 或缺失时只记日志。若 workflow 成功但 JOJ 得 0 分，则按小组 JOJ 的得分规则判定。
- **小组 JOJ。** 工具读取小组仓库里 JOJ3 为 `hN-release` 创建的成绩 issue，优先选用 commit 与 tag 匹配的记录。匹配不到时，采用本轮截止前的最近一次结果。如果有成绩 issue 却没有一条匹配 tag，工具会在 `TA Notes` 给助教提示。小组 JOJ 扣分适用于全组成员。

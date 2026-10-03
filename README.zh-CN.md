# ENGR1510J Grade Helper（中文说明）

[English](README.md)

ENGR1510J 作业自动评分工具。它检查 Gitea 上的 team 仓库、Pull Request、同伴 review 和 JOJ scoreboard，为每份作业生成 CSV 成绩表，在 Mattermost 上提醒学生，并把成绩上传到 Canvas。

不再依赖 Joint-Teapot。

## 安装

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                     # 跑测试再加 '.[dev]'
brew install universal-ctags         # C/C++ 的 code quality 检查需要
cp /path/to/Joint-Teapot/.env .env   # 变量名和 Joint-Teapot 相同，见 .env.example
gradehelper roster sync              # 从 Canvas 的 hteam 分组生成 hteams.csv（首次，以及分组变动后）
gradehelper doctor                   # 检查配置、凭据、ctags 和花名册
```

- 服务器默认是 `focs.gc.sjtu.edu.cn`。git 走 SSH（`GIT_HOST`，默认 `ssh://git@focs.gc.sjtu.edu.cn:2222`）。
- 仓库缓存在 `repos/`。遇到防火墙断开连接会自动重试。

## 配置

### 课程级：`config/course.toml`

包含 rubric（分值、描述、属于哪一轮）、每份作业的封底分（-2.5）、整洁检查的白名单、review 的敷衍判定规则、JOJ 阈值，以及迟交 issue 的标题和正文。一般一学期只需要改一次。

### 每份作业：`config/homeworks/hN.toml`

```toml
language = "c"                 # matlab | c | cc
pass_threshold = 300           # 个人 JOJ 的 hN 总分达到这个值，个人 JOJ 两项直接通过
mandatory = ["main.c", "ex1.c"]
optional = ["ex1.h"]

[deadlines]                    # 一般不用填，见下文
```

**deadline 不用填。** 工具会从 Canvas 上名为 `hN` 的作业自动读取：

| deadline | 来源 |
| --- | --- |
| 小组 deadline | Canvas 作业的截止时间（due） |
| 第 3 轮截止时间 | Canvas 作业的「可用截止」时间（available until / lock）；没有设置时用小组 deadline + 24h |
| 个人 deadline | 小组 deadline 往前推 2 天（`course.toml` 的 `individual_days_before_group`） |

- 每次运行都会显示 deadline 的来源，`gradehelper doctor` 会列出所有作业的三个时间。
- 需要覆盖时，在 `[deadlines]` 里写 `individual`、`group` 或 `final`（必须带时区，例如 `"2026-10-02T23:59:00+08:00"`）。只要写了 `group`，就完全不会访问 Canvas。
- 如果 Canvas 上有多个作业名都以 `hN` 开头，在 `hN.toml` 里写 `canvas_assignment_id` 指定是哪一个。

**JOJ 每题满分不用填。** 工具会从 engr151-joj 仓库 `master` 分支的 JOJ3 配置里自动读取：

- `hN/conf-release.json`：哪些题计分，以及 release 每题的满分。
- `hN/exK/conf.json`：个人每题的满分。

同一道题的多个 stage（`ex1`、`ex1-asan`、`[run] ex1-valgrind` 等）会合并计算。每次运行都会打印用到的满分，方便核对。如果确实需要手动指定，可以在 `hN.toml` 里写 `[joj.exercises]` 或 `[joj.release]` 覆盖。

## 每份作业跑三轮

| 时间 | 命令 | 做什么 | 之后 |
| --- | --- | --- | --- |
| 个人 deadline | `gradehelper individual 3 --warn`（简写 `indv` / `i`） | 检查个人分支、个人 PR、PR 描述、个人 JOJ（scoreboard 固定在 deadline 时刻）→ `hws/h3indv.csv`；在 Mattermost 提醒有问题的学生 | – |
| 小组 deadline | `gradehelper group 3`（简写 `g`） | 检查 `h3` tag 内容和 code quality、同伴 review、release 编译状态、小组 JOJ；记录没有按时 release 的 team → `hws/h3.csv` | `gradehelper upload 3`（预评分上传 Canvas） |
| 小组 deadline + 1 天 | `gradehelper final 3`（简写 `f`） | 用更晚的截止时间把小组检查完整再跑一遍，24h 内的迟交也会被评分；Late 仍然按小组 deadline 判定 → `hws/h3.csv`。**不上传** | 见下面「第 3 轮之后」 |

- `gradehelper run 3`：根据 `h3.toml` 里的 deadline 自动判断该跑哪一轮，询问确认后执行。判断规则：

  | 现在的时间 | 跑哪一轮 |
  | --- | --- |
  | 个人 deadline 之前 | individual（预览） |
  | 个人 deadline 之后，但还没有在 deadline 之后跑过 individual | individual |
  | 小组 deadline 之前 | group（预览） |
  | 小组 deadline 到第 3 轮截止时间之间，或者还没有在小组 deadline 之后跑过 group | group |
  | 第 3 轮截止时间之后 | final |

  deadline 之前跑的只算预览，不会让 `run` 跳过正式的那一轮。没有填 deadline 时，按 individual → group → final 的顺序选第一个还没跑过的。
- `gradehelper ui`：打开本地网页，完成上面所有操作（默认 http://127.0.0.1:8151）。

**第 3 轮之后：**

1. 把 `hws/h3.csv` push 到 hw-scoreboard 仓库。
2. 运行 `gradehelper late-issues 3`，给迟交的 team 开「Late submission, no feedback provided.」issue。
3. TA 审阅并修改表格。
4. pull 回最新的表格。
5. 运行 `gradehelper upload 3`。

### 指定重跑某一轮

`run` 只是帮你自动选择轮次。要指定跑哪一轮，直接用对应的命令就行：随时可以跑，也可以重复跑，不受 deadline 限制。

```bash
gradehelper individual 3          # 重跑第 1 轮（个人）
gradehelper group 3               # 重跑第 2 轮（小组 deadline）
gradehelper final 3               # 重跑第 3 轮（+1 天）
gradehelper group 3 -t 5          # 只重跑 hteam-05，其他 team 已保存的结果保留
gradehelper group 3 -t 5,12 -d 2026-10-02T23:59:59+08:00   # 用指定的截止时间重跑
```

- 重跑会覆盖这一轮已保存的结果（加了 `-t` 时只覆盖这几个 team），然后重新生成 CSV。如果 CSV 已经被 TA 改过，工具会保留它不覆盖，需要时加 `--overwrite`。
- 重跑 individual 会重新检查分支和 PR 的**当前**状态。个人 deadline 之后再重跑，可能会把 deadline 之后才补的提交也算进去。
- 重跑只会读取 Canvas、Gitea 和 git，不会发提醒，也不会上传。

测试时建议：

- 先用 `-t` 只跑一两个 team，确认没问题再跑全部。
- 用 `-d` 指定一个过去的时间点，可以复现当时的情况：individual 会用那个时刻的 JOJ scoreboard（分支和 PR 仍然是当前状态）；group 和 final 会按那个时间判断 review 是否算数、release 是否按时提交。Late 始终和小组 deadline 比较。
- 想完全不碰正式结果，可以把仓库复制到另一个目录再跑。每个目录都有自己的 `hws/`；`.env` 和 `repos/` 可以用软链接共用。

### 其他命令

| 命令 | 作用 |
| --- | --- |
| `gradehelper warn 3 --stage group` | 根据已保存的结果预览并发送 Mattermost 提醒（`--stage` 可选 individual / group / final） |
| `gradehelper show 3 [--all]` | 在终端里显示结果表（默认只显示有扣分的人），并刷新 CSV |
| `gradehelper upload 3 [--force]` | 上传 `hws/h3.csv` 到 Canvas |
| `gradehelper late-issues 3` | 给迟交的 team 开 issue |
| `gradehelper roster show` | 查看花名册 |
| `gradehelper doctor` | 运行前自检 |

### 常用选项

| 选项 | 作用 |
| --- | --- |
| `-t "1,5"`，`--teams` | 只跑这几个 team，其他 team 已保存的结果保留 |
| `-d 2026-10-11T23:59:59+08:00`，`--deadline` | 覆盖这一轮的截止时间 |
| `-w`，`--warn` | 跑完后预览并发送 Mattermost 提醒 |
| `-y`，`--yes` | 跳过确认 |
| `-h`，`--help` | 查看帮助，例如 `gradehelper -h`、`gradehelper i -h` |
| `--overwrite` | 允许覆盖被人改过的 CSV（见下文） |

## 规则细节

### 个人部分在第 1 轮冻结

个人部分在个人 deadline 时评分，结果保存在 `hws/hN.indv.json`。第 2、3 轮直接沿用，不会重新检查分支和 JOJ，所以 deadline 之后再 push 也不会改变个人部分的扣分。

- 分支、文件、PR、PR 描述：看第 1 轮**实际运行那一刻**的状态，所以第 1 轮应该在 deadline 刚过时跑。
- 个人 JOJ：读 scoreboard 在 deadline 那一刻的版本，和什么时候运行无关。
- 如果跑第 2 轮时还没有个人结果，会自动补一份（只检查 PR 和 JOJ，不检查分支），终端里会明确提示。
- 个人迟交（deadline 后 24h 内补交并开了道歉 issue）：工具不会自动处理，需要 TA 用 override 手动撤销扣分。

### release 的时间判定

以个人 deadline 9/30、小组 deadline 10/2 23:59 为例，第 3 轮截止时间是 10/3 23:59。

只有同时满足以下条件，才算「提交」：tag 恰好是 `hN`，是已发布的 release（draft 不算），并且创建时间不晚于这一轮的截止时间。结果与这一轮实际在什么时候运行无关。

| release 创建时间 | 第 2 轮（截止 10/2 23:59，会上传） | 第 3 轮（截止 10/3 23:59） | Late |
| --- | --- | --- | --- |
| 10/2 23:59 前 | 正常评分 | 正常评分 | No |
| 10/2 23:59 至 10/3 23:59 | 视为未提交，-2.5（起提醒作用） | 正常评分，迟交本身不扣分 | Yes，开迟交 issue |
| 10/3 23:59 之后 | -2.5 | 拒收，-2.5 | Yes，开迟交 issue |
| 没有 tag、只有 draft、或只有 tag 没有 release | -2.5 | -2.5 | Yes，开迟交 issue |

- 被视为未提交的 team 只扣一次 `groupFailSubmit`，detail 里写明原因。它们的 tag 内容、编译和小组 JOJ 都不再检查。同伴 review 照常按这一轮的截止时间检查。
- 如果 `hN` tag 指向的 commit 没有对应的 JOJ release 运行（可能是 release 之后挪过 tag），会在 CSV 的 `TA Notes` 列里提示。这只是给 TA 的提示，不扣分，学生也看不到。

### 同伴 review（noReview）

满足以下所有条件的一条内容才算有效 review：

- 发在队友的个人 PR 上（PR 标题包含 `hN` 和对方的 12 位学号），不是自己的 PR。普通评论、review、行内代码评论都算。
- 作者是花名册上的学生（学号从 Gitea 的 full name 或 login 中提取）。
- 发表时间不晚于这一轮的截止时间。所以在第 3 轮，小组 deadline 之后 24h 内补的 review 也算。
- 不是敷衍内容：至少 15 个字符，去掉 "lgtm"、"thanks" 这类套话之后，有效内容至少占一半。

只要在任意一个队友的 PR 上留过一条有效 review，就不扣分。被判为敷衍的内容会记录在日志里。

### 编译（jojFailCompile）

只看 tag 所在 commit 上 `Run JOJ3 on Release` 这个 workflow 的最新状态。是红叉（failure 或 error）才扣分。

- push 触发的 workflow 不看。
- 还在运行（pending）或没有状态的，不扣分，只在日志里提示。
- JOJ 拿 0 分但 workflow 是绿勾的，不算编译失败，由小组 JOJ 那两项扣分。

### 小组 JOJ

读取 team 仓库里 JOJ3 自动开的 issue「JOJ3 Result for hN-release by @… - Score: x / y」，取 commit 和 tag 一致的那一次；对不上时，取截止时间前最新的一次。

| 条件 | 扣分 |
| --- | --- |
| 各题平均得分率 < 50% | `jojGroupFailHomework` -0.5 |
| 某题得分率 < 25% | `jojGroupFailExercise` -0.25，最多 2 次 |

两项都扣全组成员。

### 成绩表是第 3 轮之后的唯一依据

- TA 直接修改 hw-scoreboard 里的 `hws/hN.csv`。`upload` 上传的就是这个文件的内容，包括手改的部分。git 的 pull 和 push 由你自己操作。
- 工具会记录自己上一次写出的内容（`hws/.gradehelper-written.json`）。如果 CSV 之后被人改过，或者是从别处 pull 来的，工具**拒绝覆盖**并给出提示。只有确定要重新生成时才加 `--overwrite`，这样做会丢掉手改的内容。
- hw-scoreboard 的 `.gitignore` 只放行 CSV，所以工具的其他文件（`*.json`、`*.toml`）不会被提交，只保存在本机。

### 手动调整（override）

在第 3 轮把表格交给 TA 之前，可以在网页上点某一行的「adjust」，或者直接编辑 `hws/hN.overrides.toml`。重新运行时会自动套用，并以 Note 的形式写进学生看到的 comment：

```toml
[[override]]
student = "520000000001"       # 学号或 jaccount；也可以写 team = "hteam-03"
remove = ["noReview"]          # 撤销某些扣分
# add = { indvUntidy = 1 }     # 增加扣分
# score = -0.5                 # 直接指定分数
note = "review left on Mattermost, checked by TA"
```

第 3 轮之后以 TA 修改过的 CSV 为准，override 主要用于这之前的调整。

### 安全

- 评分本身只读取 Canvas、Gitea 和 git，不写入任何东西。
- 发 Mattermost 提醒、开迟交 issue、上传 Canvas 之前，都会先预览，确认后才执行（命令行加 `--yes` 可以跳过确认）。
- 上传记录保存在 `hws/hN.uploaded.json`。分数和 comment 都没变的不会重复上传，所以重复执行 upload 不会在 Canvas 上堆积重复的 comment。
- 网页只监听 127.0.0.1，并且要求每次启动时生成的随机 token，其他网站无法借你的浏览器发起操作。

## Rubric

| 扣分项 | 分值 | 轮次 | 条件 |
| --- | --- | --- | --- |
| `indvFailSubmit` | -1 | 个人 | 缺个人分支、`hN/` 目录、mandatory 文件或 README；或不在 JOJ scoreboard 上 |
| `indvUntidy` | -0.25 | 个人 | 个人分支里有多余文件 |
| `noIndividualPR` | -0.5 | 个人 | 没有标题包含 `hN` 和本人学号的 PR |
| `notWritingPR` | -0.25 | 个人 | PR 描述（取写得最好的一个）仍然像没填的模板 |
| `jojFailHomework` | -0.5 | 个人 | deadline 时刻的 scoreboard：总分没到 pass_threshold，且各题平均得分率 < 25% |
| `jojFailExercise` | -0.25 ×（最多 2 次） | 个人 | 同上，某题得分率 < 10% |
| `groupFailSubmit` | -2.5 | 小组 | 截止时间前没有提交（见上文的时间判定）；或 tag 里缺 `hN/` 目录、mandatory 文件或 README |
| `groupUntidy` | -0.25 | 小组 | tag 里有多余文件 |
| `groupLowCodeQuality` | -0.5 × 问题种类数 | 小组 | 提交文件的静态检查（MATLAB 规则，C/C++ 的 non-const 全局变量、`using namespace` 等） |
| `noReview` | -1 | 小组 | 截止时间前没有给队友留过有效 review |
| `jojFailCompile` | -2.5 | 小组 | release workflow 出现红叉 |
| `jojGroupFailHomework` | -0.5 | 小组 | release 的 JOJ 结果：各题平均得分率 < 50% |
| `jojGroupFailExercise` | -0.25 ×（最多 2 次） | 小组 | 同上，某题得分率 < 25% |

- 每份作业最低 -2.5 分。
- JOJ 阈值在 `config/course.toml` 的 `[joj.individual]` 和 `[joj.group]` 里。

## 常见问题

**终端提示「… was edited or not written by gradehelper; keeping it」**
CSV 被人改过，或者是从别处 pull 来的，工具没有覆盖它。如果确实要重新生成，加 `--overwrite`。

**提示「group JOJ skipped」**
engr151-joj 的 `master` 分支上找不到这份作业的 `conf-release.json`。要么等 JOJ 配置更新，要么在 `hN.toml` 里用 `[joj.release]` 手动指定满分。

**`ctags not found`**
运行 `brew install universal-ctags`。C/C++ 作业的 code quality 检查需要它。

**`[deadlines]` 报错 `Extra inputs are not permitted`**
`[deadlines]` 只接受 `individual`、`group`、`final` 三个字段。旧版的 `review` 已经取消：第 3 轮的 review 截止时间就是 +24h。

**改了分组**
重新运行 `gradehelper roster sync`。它会先列出变动，确认后才覆盖 `hteams.csv`。

**看详细日志**
所有运行记录都在 `gradehelper.log`。

## 目录结构

```
config/              course.toml，homeworks/hN.toml
templates/           PR 描述检查用的模板
src/gradehelper/
  clients/           canvas、gitea、mattermost、git（轻量封装，用到时才连接）
  checks/            layout、code_quality、repo、pull_requests、release、joj、joj_config、joj_release
  grading.py         rubric → 分数和 comment（唯一计算分数的地方）
  report.py          每轮的结果（JSON）和 CSV
  overrides.py       手动调整
  late_issues.py     迟交 team 的 issue
  pipeline.py        三轮评分流程
  notify.py          Mattermost 提醒
  upload.py          Canvas 上传
  cli.py, web/       命令行和本地网页
tests/               pytest，只使用虚构数据
legacy/              旧版代码，仅供对照，切换后删除
```

## 开发

```bash
pip install -e '.[dev]'
pytest --cov=gradehelper
ruff check src tests
```

测试和示例里只能使用虚构的姓名和学号，不要放真实学生的信息。

# ENGR1510J Grade Helper

[English](README.md)

Grade Helper 检查 ENGR1510J 作业的 Gitea 提交、同伴评审和 JOJ 成绩，生成供助教复核的 CSV，也可以通过 Mattermost 提醒学生，并把复核后的成绩上传到 Canvas。

## 安装

在仓库根目录运行以下命令，需要 Python 3.11 或更高版本。批改 C/C++ 作业还需要 Universal Ctags，macOS 可用 `brew install universal-ctags` 安装。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp -n .env.example .env
```

在 `.env` 中填好课程凭据，再同步名单并检查配置。

```bash
gradehelper roster sync
gradehelper doctor
```

## 批改一份作业

以第 3 次作业为例，在每个截止时间运行 `gradehelper run 3`，工具会根据时间和已有结果选择轮次。也可以用 `gradehelper ui` 打开本地网页。个人初评用于提醒学生，个人终评确定个人分数。

小组初评后，运行 `gradehelper feedback 3` 在各组仓库创建反馈 issue。小组终评后再运行一次，在原 issue 中回复最终结果。将 `hws/h3.csv` 提交到 hw-scoreboard 供助教复核，拉取修改后的表格，再用 `gradehelper upload 3` 上传到 Canvas。

## 详细文档

- [安装与配置](docs/setup.zh-CN.md)涵盖凭据、名单变动、作业截止时间和 JOJ 配置。
- [批改流程](docs/grading.zh-CN.md)说明四轮批改、重跑、反馈和成绩上传的操作步骤。
- [评分规则](docs/rubric.zh-CN.md)列出扣分项，以及迟交、同伴评审和 JOJ 成绩的判定方式。

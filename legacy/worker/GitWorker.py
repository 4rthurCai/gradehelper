from util import Logger, getProjRepoName, passCodeQuality, getAllFiles
from shutil import ignore_patterns, copytree, rmtree
import multiprocessing
import traceback
import git
import os

# Constants
_GITEA_DIR = ".gitea"
_ORIGIN_MASTER = "origin/master"


class GitWorker():
    def __init__(self,
                 args,
                 hgroups,
                 pgroups,
                 language,
                 mandatoryFiles,
                 optionalFiles,
                 course_org="",
                 git_base_url="",
                 repos_base_dir="",
                 logger=Logger(),
                 processCount=4):
        self.args = args
        self.hgroups = hgroups
        self.pgroups = pgroups
        self.language = language
        self.logger = logger
        self.processCount = processCount
        self.mandatoryFiles = mandatoryFiles
        self.optionalFiles = optionalFiles
        self.moss = None

        # Import settings module to get defaults
        import sys
        import os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        try:
            from gradehelper.settings import COURSE_ORG, GIT_BASE_URL, REPOS_BASE_DIR
            self.course_org = course_org or COURSE_ORG
            self.git_base_url = git_base_url or GIT_BASE_URL
            self.repos_base_dir = repos_base_dir or REPOS_BASE_DIR
        except ImportError:
            self.course_org = course_org or "engr151-24fa"
            self.git_base_url = git_base_url or "https://focs.ji.sjtu.edu.cn/git"
            self.repos_base_dir = repos_base_dir or "repos"

    @classmethod
    def isREADME(cls, fn):
        fn = fn.lower()
        if len(fn) < 6: return False
        if len(fn) == 6: return fn == "readme"
        return fn.startswith("readme.")

    def _check_file_quality(self, file_path, file_name):
        """Check if a file meets code quality standards.

        Returns:
            tuple: (issue_count, issue_list) or None if file type not checked
        """
        # C/C++ files
        cpp_extensions = [".c", ".cc", ".cpp", ".hpp", ".h", ".cxx"]
        if any(file_name.endswith(suf) for suf in cpp_extensions):
            return passCodeQuality(file_path, self.language)

        # MATLAB files
        matlab_extensions = [".m"]
        if self.language == "matlab" and any(file_name.endswith(suf) for suf in matlab_extensions):
            return passCodeQuality(file_path, self.language)

        return (0, [])  # No issues for unchecked file types

    def _check_indv_files(self, hwDir, repoName, stuID, stuName, hwNum, scores):
        """Check individual files for a student's homework."""
        for fn, path in [(fn, os.path.join(hwDir, fn)) for fn in [*self.mandatoryFiles, *self.optionalFiles]]:
            if os.path.exists(path):
                # Skip code quality check for individual submissions
                continue

            if fn in self.mandatoryFiles:
                self.logger.warning(f"{repoName} {stuID} {stuName} h{hwNum}/{fn} file missing")
                scores[stuName]["indvFailSubmit"] = 1
                scores[stuName]["indvComment"].append(f"individual branch h{hwNum}/{fn} file missing")

    def _check_indv_readme(self, hwDir, repoName, stuID, stuName, hwNum, scores):
        """Check if README exists in homework directory."""
        if not list(filter(GitWorker.isREADME, os.listdir(hwDir))):
            self.logger.warning(f"{repoName} {stuID} {stuName} h{hwNum}/README file missing")
            scores[stuName]["indvFailSubmit"] = 1
            scores[stuName]["indvComment"].append(f"individual branch h{hwNum}/README file missing")

    def _check_indv_tidy(self, repoDir, hwDir, repoName, stuID, stuName, hwNum, scores):
        """Check if directories are tidy (no extra files)."""
        # Check repo root
        dirList = list(filter(
            lambda x: x not in [".gitignore", ".git", _GITEA_DIR, ".gitattributes","issue_template", "workflows", "PULL_REQUEST_TEMPLATE.md", *[f"h{n}" for n in range(20)]]
            and not GitWorker.isREADME(x), os.listdir(repoDir)))
        if dirList:
            self.logger.warning(f"{repoName} {stuID} {stuName} untidy {', '.join(dirList)}")
            scores[stuName]["indvUntidy"] = 1
            scores[stuName]["indvComment"].append(f"individual branch redundant files: {', '.join(dirList)}")

        # Check homework directory
        if os.path.exists(hwDir):
            dirList = list(filter(
                lambda x: x not in self.mandatoryFiles and x not in self.optionalFiles and not GitWorker.isREADME(x),
                os.listdir(hwDir)))
            if dirList:
                self.logger.warning(f"{repoName} {stuID} {stuName} h{hwNum}/ untidy {', '.join(dirList)}")
                scores[stuName]["indvUntidy"] = 1
                scores[stuName]["indvComment"].append(f"individual branch redundant files: {', '.join(dirList)}")

    def _process_individual_student(self, repo, remoteBranches, repoName, repoDir, hwDir, hwNum, stuID, stuName, scores, tidy):
        """Process a single student's individual branch."""
        if f"origin/{stuID}" not in remoteBranches:
            self.logger.warning(f"{repoName} {stuID} {stuName} branch missing")
            scores[stuName]["indvFailSubmit"] = 1
            scores[stuName]["indvComment"].append("individual branch individual branch missing")
            return

        # Force clean and reset before checkout
        repo.git.reset("--hard", "HEAD")
        repo.git.clean("-d", "-f", "-x")

        # Checkout to the remote student branch directly
        repo.git.checkout(f"origin/{stuID}", "-f")

        if self.args.dir:
            copytree(repoDir, os.path.join("indv", f"{repoName} {stuID} {stuName}"), ignore=ignore_patterns(".git"))

        if not os.path.exists(hwDir):
            self.logger.warning(f"{repoName} {stuID} {stuName} h{hwNum} dir missing")
            scores[stuName]["indvFailSubmit"] = 1
            scores[stuName]["indvComment"].append(f"individual branch h{hwNum} dir missing")
        else:
            self._check_indv_files(hwDir, repoName, stuID, stuName, hwNum, scores)
            self._check_indv_readme(hwDir, repoName, stuID, stuName, hwNum, scores)

        if tidy:
            self._check_indv_tidy(repoDir, hwDir, repoName, stuID, stuName, hwNum, scores)

    def checkIndvProcess(self, groupNum, hwNum):
        tidy = True  # Always check tidy
        teamKey = list(self.hgroups.keys())[groupNum]
        repoName = teamKey.replace("-", "")
        repoDir = os.path.join(self.repos_base_dir, teamKey)
        hwDir = os.path.join(repoDir, f"h{hwNum}")

        if not os.path.exists(repoDir):
            repo = git.Repo.clone_from(
                f"{self.git_base_url}/{self.course_org}/{repoName}",
                repoDir,
                branch="master")
        else:
            repo = git.Repo(repoDir)

        repo.git.fetch("--all", "-f")
        remoteBranches = [ref.name for ref in repo.remote().refs]
        scores = {
            stuName: {
                "indvFailSubmit": 0,
                "indvUntidy": 0,
                "indvComment": [],
            }
            for _, stuName in self.hgroups[teamKey]
        }

        for stuID, stuName in self.hgroups[teamKey]:
            try:
                self._process_individual_student(repo, remoteBranches, repoName, repoDir, hwDir, hwNum, stuID, stuName, scores, tidy)
            except Exception:
                self.logger.error(f"{repoName} {stuID} {stuName} error")
                self.logger.error(traceback.format_exc())

        return scores

    def _mark_all_team_members(self, teamKey, scores, field, value, comment=None):
        """Mark all team members with a specific score/comment."""
        for _, stuName in self.hgroups[teamKey]:
            scores[stuName][field] = value
            if comment:
                scores[stuName]["groupComment"].append(comment)

    def _check_group_files(self, hwDir, repoName, hwNum, teamKey, scores):
        """Check group files for homework."""
        # Track unique issue types across all files
        unique_issues = set()
        all_issue_details = []

        for fn, path in [(fn, os.path.join(hwDir, fn)) for fn in [*self.mandatoryFiles, *self.optionalFiles]]:
            if os.path.exists(path):
                issue_count, issues = self._check_file_quality(path, fn)
                if issue_count > 0:
                    # Add unique issue types to set
                    unique_issues.update(issues)
                    # Collect detailed issue info
                    for issue in issues:
                        all_issue_details.append(f"group {fn}: {issue}")
                    self.logger.warning(f"{repoName} {fn} has {issue_count} quality issue(s)")
                continue

            if fn in self.mandatoryFiles:
                self.logger.warning(f"{repoName} h{hwNum}/{fn} file missing")
                self._mark_all_team_members(teamKey, scores, "groupFailSubmit", 1, f"tags/h{hwNum} h{hwNum}/{fn} missing")

        # Apply deduction based on unique issue types (each type = 0.5 points)
        if unique_issues:
            unique_issue_count = len(unique_issues)
            self._mark_all_team_members(teamKey, scores, "groupLowCodeQuality", unique_issue_count)
            # Add all detailed issues to comments
            for detail in all_issue_details:
                for _, stuName in self.hgroups[teamKey]:
                    scores[stuName]["groupComment"].append(detail)

    def _check_group_readme(self, hwDir, repoName, hwNum, teamKey, scores):
        """Check if README exists in group homework directory."""
        if not list(filter(GitWorker.isREADME, os.listdir(hwDir))):
            self.logger.warning(f"{repoName} h{hwNum}/README file missing")
            self._mark_all_team_members(teamKey, scores, "groupFailSubmit", 1, f"tags/h{hwNum} h{hwNum}/README file missing")

    def _check_group_tidy(self, repoDir, hwDir, repoName, hwNum, teamKey, scores):
        """Check if group directories are tidy (no extra files)."""
        # Check repo root (including .gitea directory)
        dirList = os.listdir(repoDir)
        if os.path.exists(os.path.join(repoDir, _GITEA_DIR)):
            dirList.extend([
                fn for fn in os.listdir(os.path.join(repoDir, _GITEA_DIR))
                if fn != "pull_request_template.md"
            ])
        dirList = list(filter(
            lambda x: x not in [
                ".gitignore", ".git", f"{_GITEA_DIR}/.gitignore", _GITEA_DIR, ".gitattributes", "issue_template", "workflows", "PULL_REQUEST_TEMPLATE.md",
                *[f"h{n}" for n in range(20)]
            ] and not GitWorker.isREADME(x), dirList))

        if dirList:
            self.logger.warning(f"{repoName} untidy {', '.join(dirList)}")
            self._mark_all_team_members(teamKey, scores, "groupUntidy", 1, f"tags/h{hwNum} redundant files: {', '.join(dirList)}")

        # Check homework directory
        if os.path.exists(hwDir):
            dirList = list(filter(
                lambda x: x not in self.mandatoryFiles and x not in self.optionalFiles and not GitWorker.isREADME(x),
                os.listdir(hwDir)))
            if dirList:
                self.logger.warning(f"{repoName} h{hwNum} untidy {', '.join(dirList)}")
                self._mark_all_team_members(teamKey, scores, "groupUntidy", 1, f"tags/h{hwNum} redundant files: {', '.join(dirList)}")

    def checkGroupProcess(self, groupNum, hwNum):
        tidy = True  # Always check tidy
        teamKey = list(self.hgroups.keys())[groupNum]
        repoName = teamKey.replace("-", "")
        repoDir = os.path.join(self.repos_base_dir, teamKey)
        hwDir = os.path.join(repoDir, f"h{hwNum}")

        if not os.path.exists(repoDir):
            repo = git.Repo.clone_from(
                f"{self.git_base_url}/{self.course_org}/{repoName}",
                repoDir,
                branch="master")
        else:
            repo = git.Repo(repoDir)

        repo.git.fetch("--tags", "--all", "-f")
        tagNames = [tag.name for tag in repo.tags]
        scores = {
            stuName: {
                "groupFailSubmit": 0,
                "groupUntidy": 0,
                "groupLowCodeQuality": 0,
                "groupComment": [],
            }
            for _, stuName in self.hgroups[teamKey]
        }

        if f"h{hwNum}" not in tagNames:
            self.logger.warning(f"{repoName} tags/h{hwNum} missing")
            self._mark_all_team_members(teamKey, scores, "groupFailSubmit", 1, f"tags/h{hwNum} missing")
            return scores

        # Force clean the working directory before any checkout
        # Discard all local changes (staged and unstaged) in current HEAD state
        repo.git.reset("--hard", "HEAD")
        # Clean all untracked files and directories
        repo.git.clean("-d", "-f", "-x")

        # Now checkout to the tag with force flag
        repo.git.checkout(f"tags/h{hwNum}", "-f")

        if not os.path.exists(hwDir):
            self.logger.warning(f"{repoName} h{hwNum} dir missing")
            self._mark_all_team_members(teamKey, scores, "groupFailSubmit", 1, f"tags/h{hwNum} h{hwNum} dir missing")
        else:
            self._check_group_files(hwDir, repoName, hwNum, teamKey, scores)
            self._check_group_readme(hwDir, repoName, hwNum, teamKey, scores)

        if tidy:
            self._check_group_tidy(repoDir, hwDir, repoName, hwNum, teamKey, scores)

        return scores

    def _check_proj_code_quality(self, repoDir, repoName, projNum, stuName, scores):
        """Check code quality for project files."""
        language = ["matlab", "c"]

        if projNum == 1:
            for fn in getAllFiles(repoDir):
                if fn.endswith(".m") and not passCodeQuality(os.path.join(repoDir, fn), language[projNum - 1]):
                    self.logger.warning(f"{repoName} {fn} low quality")
                    scores[stuName]["projComment"].append(f"{fn} low quality")
        elif projNum == 2:
            for fn in getAllFiles(repoDir):
                if (fn.endswith(".c") or fn.endswith(".h")) and not passCodeQuality(os.path.join(repoDir, fn), language[projNum - 1]):
                    self.logger.warning(f"{repoName} {fn} low quality")
                    scores[stuName]["projComment"].append(f"{fn} low quality")

    def _check_and_checkout_milestone(self, repo, repoName, repoDir, milestoneNum, stuName, scores):
        """Check milestone tag and checkout to it."""
        repo.git.fetch("--tags", "--all", "-f")
        tagNames = [tag.name for tag in repo.tags]

        if f"m{milestoneNum}" not in tagNames:
            self.logger.warning(f"{repoName} tags/m{milestoneNum} missing")
            scores[stuName]["projComment"].append(f"tags/m{milestoneNum} missing")
            return False

        # Force reset and clean before checkout
        repo.git.reset("--hard")
        repo.git.clean("-d", "-f", "-x")
        repo.git.checkout(f"tags/m{milestoneNum}", "-f")

        if not list(filter(GitWorker.isREADME, os.listdir(repoDir))):
            self.logger.warning(f"{repoName} README file missing")
            scores[stuName]["projComment"].append("README file missing")

        return True

    def checkProjProcess(self, id_, name, projNum, milestoneNum):
        stuName = name
        repoName = getProjRepoName([id_, name, projNum, milestoneNum])
        repoDir = os.path.join("projrepos", f"p{projNum}", repoName)
        scores = {stuName: {"projComment": []}}

        if not os.path.exists(repoDir):
            repo = git.Repo.clone_from(f"{self.git_base_url}/{self.course_org}/{repoName}", repoDir)
        else:
            repo = git.Repo(repoDir)
            repo.git.fetch("--tags", "--all", "-f")
            remoteBranches = [ref.name for ref in repo.remote().refs]

            if _ORIGIN_MASTER not in remoteBranches:
                self.logger.warning(f"{repoName} master branch missing")
                scores[stuName]["projComment"].append("master branch missing")
                return scores

            repo.git.reset("--hard", _ORIGIN_MASTER)
            repo.git.clean("-d", "-f", "-x")

        if milestoneNum:
            if self._check_and_checkout_milestone(repo, repoName, repoDir, milestoneNum, stuName, scores):
                self._check_proj_code_quality(repoDir, repoName, projNum, stuName, scores)

        return scores

    def _mark_all_students_with_comment(self, students, scores, comment):
        """Mark all students in the team with a specific comment."""
        for stuInfo in students:
            scores[stuInfo[1]]["projComment"].append(comment)

    def _check_proj3_milestone(self, repo, repoName, repoDir, milestoneNum, students, scores):
        """Check milestone tag and checkout to it for project 3."""
        repo.git.fetch("--tags", "--all", "-f")
        tagNames = [tag.name for tag in repo.tags]

        if f"m{milestoneNum}" not in tagNames:
            self.logger.warning(f"{repoName} tags/m{milestoneNum} missing")
            self._mark_all_students_with_comment(students, scores, f"tags/m{milestoneNum} missing")
            return False

        # Force reset and clean before checkout
        repo.git.reset("--hard")
        repo.git.clean("-d", "-f", "-x")
        repo.git.checkout(f"tags/m{milestoneNum}", "-f")

        if not list(filter(GitWorker.isREADME, os.listdir(repoDir))):
            self.logger.warning(f"{repoName} README file missing")
            self._mark_all_students_with_comment(students, scores, "README file missing")

        return True

    def _check_proj3_code_quality(self, repoDir, repoName, students, scores):
        """Check code quality for project 3 C++ files."""
        for fn in getAllFiles(repoDir):
            if (any(
                (fn.endswith(suf)
                 for suf in [".c", ".cc", ".cpp", ".hpp", ".h", ".cxx"]))
                ) and not passCodeQuality(os.path.join(repoDir, fn), "cc"):
                self.logger.warning(f"{repoName} {fn} low quality")
                self._mark_all_students_with_comment(students, scores, f"{fn} low quality")

    def _setup_proj3_repo(self, repoName, repoDir, students, scores):
        """Setup project 3 repository (clone or fetch)."""
        if not os.path.exists(repoDir):
            return git.Repo.clone_from(f"{self.git_base_url}/{self.course_org}/{repoName}", repoDir)

        repo = git.Repo(repoDir)
        repo.git.fetch("--tags", "--all", "-f")
        remoteBranches = [ref.name for ref in repo.remote().refs]

        if _ORIGIN_MASTER not in remoteBranches:
            self.logger.warning(f"{repoName} master branch missing")
            self._mark_all_students_with_comment(students, scores, "master branch missing")
            return None

        repo.git.reset("--hard", _ORIGIN_MASTER)
        repo.git.clean("-d", "-f", "-x")
        return repo

    def checkProj3Process(self, groupNum, milestoneNum):
        teamKey = list(self.pgroups.keys())[groupNum]
        repoName = teamKey.replace("-", "")
        repoDir = os.path.join("projrepos", "p3", teamKey)
        students = self.pgroups[teamKey]
        scores = {stuInfo[1]: {"projComment": []} for stuInfo in students}

        repo = self._setup_proj3_repo(repoName, repoDir, students, scores)
        if repo is None:
            return scores

        if milestoneNum:
            if self._check_proj3_milestone(repo, repoName, repoDir, milestoneNum, students, scores):
                self._check_proj3_code_quality(repoDir, repoName, students, scores)

        return scores

    def checkIndv(self):
        if self.args.dir and os.path.exists(os.path.join("indv")):
            rmtree(os.path.join("indv"))
        hwNum = self.args.hw
        if self.args.rejudge < 0:
            with multiprocessing.Pool(self.processCount) as p:
                res = p.starmap(self.checkIndvProcess,
                                [(i, hwNum)
                                 for i in range(len(self.hgroups.keys()))])
        else:
            res = [self.checkIndvProcess(self.args.rejudge, hwNum)]
        return {k: v for d in res for k, v in d.items()}

    def checkGroup(self):
        hwNum = self.args.hw
        if self.args.rejudge < 0:
            with multiprocessing.Pool(self.processCount) as p:
                res = p.starmap(self.checkGroupProcess,
                                [(i, hwNum)
                                 for i in range(len(self.hgroups.keys()))])
        else:
            res = [self.checkGroupProcess(self.args.rejudge, hwNum)]
        return {k: v for d in res for k, v in d.items()}

    def checkProj(self, projNum, milestoneNum):
        milestoneNum = 0 if milestoneNum is None else milestoneNum
        res = {}
        if projNum in [1, 2]:
            infos = [[*info, projNum, milestoneNum]
                     for hgroup in self.hgroups.values() for info in hgroup]
            with multiprocessing.Pool(self.processCount) as p:
                res = p.starmap(self.checkProjProcess, infos)
        elif projNum in [3]:
            infos = [[i, milestoneNum] for i in range(len(self.pgroups.keys()))]
            with multiprocessing.Pool(self.processCount) as p:
                res = p.starmap(self.checkProj3Process, infos)
        return {k: v for d in res for k, v in d.items()}
import re
import sys
import os
from difflib import SequenceMatcher
from util import Logger, getProjRepoName

# Add Joint-Teapot to path - support custom location via environment variable
joint_teapot_path = os.environ.get('JOINT_TEAPOT_PATH',
                                    os.path.join(os.path.dirname(__file__), '..', '..', 'Joint-Teapot'))
sys.path.insert(0, joint_teapot_path)

from joint_teapot import logger

# Constants
_STUDENT_ID_PATTERN = r'\b(\d{12})\b'


class GiteaWorker():
    def __init__(self, args, teapot, hgroups, logger=Logger()):
        self.args = args
        self.logger = logger
        self.teapot = teapot
        self.names = {
            item[1]: item[0]
            for items in hgroups.values() for item in items
        }
        self.ids = {
            item[0]: item[1]
            for items in hgroups.values() for item in items
        }
        self.hgroups = hgroups

        # Parse deadline if provided
        self.deadline = None
        if hasattr(args, 'time') and args.time:
            from datetime import datetime
            try:
                self.deadline = datetime.fromisoformat(args.time.replace('Z', '+00:00'))
                self.logger.info(f"Review deadline set to: {self.deadline}")
            except Exception as e:
                self.logger.error(f"Failed to parse deadline '{args.time}': {e}")

    def raiseIssues(self, scores):
        for key, value in scores.items():
            if not value.get('projComment'):
                value['projComment'] = ['no problem detected by the auto-grader']
            if not value.get('jojComment'):
                value['jojComment'] = ['']
            id_ = self.names[key]
            repoName = getProjRepoName([id_, key, self.args.proj])

            # Use teapot to create issue
            issue_body = '\n'.join([*value['projComment'], *value['jojComment']])
            try:
                self.teapot.gitea.create_issue(
                    repoName,
                    f"m{self.args.ms} feedback",
                    issue_body
                )
                self.logger.debug(f"{repoName} issue created successfully")
            except Exception as e:
                self.logger.error(f"{repoName} issue creation failed: {e}")

    def _extract_student_id(self, source_text):
        """Extract 12-digit student ID from text."""
        if not source_text:
            return None

        match = re.search(_STUDENT_ID_PATTERN, source_text)
        if match:
            return match.group(1)

        # Fallback: extract all digits and take first 12
        all_digits = ''.join([s for s in source_text if s.isdigit()])
        return all_digits[:12] if len(all_digits) >= 12 else None

    def _is_before_deadline(self, timestamp):
        """Check if a timestamp is before the deadline.

        Returns True if no deadline is set, or if timestamp is before deadline.
        """
        if not self.deadline:
            return True  # No deadline set, accept all

        from datetime import datetime

        # Handle both datetime objects and strings
        if isinstance(timestamp, str):
            try:
                time_obj = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            except Exception as e:
                self.logger.warning(f"Failed to parse timestamp '{timestamp}': {e}")
                return False
        else:
            time_obj = timestamp

        return time_obj <= self.deadline

    def _is_low_quality_review(self, text):
        """Check if review/comment content is low quality (perfunctory or too short).

        Returns True if:
        - Text is empty or too short (< 15 chars)
        - Perfunctory phrases make up >= 80% of the text content
        """
        if not text or not text.strip():
            return True

        original_text = text.strip()
        text_lower = original_text.lower()

        # Check length - must be at least 15 characters
        if len(text_lower) < 15:
            return True

        # List of perfunctory phrases (case-insensitive)
        perfunctory_phrases = [
            "lgtm",
            "looks good to me",
            "looks good",
            "ok",
            "okay",
            "good",
            "fine",
            "approved",
            "approve",
            "agree",
            "nice job",
            "well done",
            "great work",
            "no comments",
            "good job",
            "all good",
            "nothing to add",
            "no issues",
            "no problem",
            "seems fine",
            "seems good",
            "i agree",
            "i approve",
            "i like it",
            "thank you",
            "thanks",
            "good code",
            "great code",
            "excellent",
            "perfect",
            "good work",
            "nice",
            "excellent work",
            "looks great",
            "no suggestions",
            "all set",
            "sounds good",
            "works for me",
            "i like this",
            "well implemented",
            "good implementation",
            "solid work",
            "clean code",
            "great job",
            "very good",
            "fantastic",
            "superb",
            "brilliant",
            "outstanding",
            "impressive",
            "keep it up",
            "good effort",
            "nice effort",
            "good to go",
            "ready to merge",
            "looks perfect",
            "nothing else to add",
            "no further comments",
            "all looks good",
            "everything looks good",
            "everything looks fine",
            "everything looks great",
            "great effort"
        ]

        # Remove all perfunctory phrases from text and see what's left
        remaining_text = text_lower
        for phrase in perfunctory_phrases:
            # Remove all occurrences of the phrase (with optional punctuation)
            import re
            # Match phrase with optional surrounding punctuation/whitespace
            pattern = r'\b' + re.escape(phrase) + r'[.!,]*\b'
            remaining_text = re.sub(pattern, '', remaining_text, flags=re.IGNORECASE)

        # Remove extra whitespace and punctuation
        remaining_text = re.sub(r'[.,!?\s]+', ' ', remaining_text).strip()

        # Calculate what percentage of content is NOT perfunctory
        original_length = len(text_lower)
        remaining_length = len(remaining_text)
        substantive_ratio = remaining_length / original_length if original_length > 0 else 0

        # If less than 50% of the text is substantive content, it's low quality
        # (i.e., if >= 50% is perfunctory phrases, it's low quality)
        return substantive_ratio < 0.50

    def _process_pr_comments(self, repoName, pull, pr_owner_id, res):
        """Process comments on a PR and update review status."""
        try:
            comments = self.teapot.gitea.issue_api.issue_get_comments(
                self.teapot.gitea.org_name, repoName, pull.number)
            self.logger.info(f"{repoName} h{self.args.hw} get pr comments for #{pull.number}, found {len(comments)} comments")

            for comment in comments:
                if comment.user is None:
                    continue

                # Check if comment was created before deadline
                if hasattr(comment, 'created_at') and not self._is_before_deadline(comment.created_at):
                    self.logger.info(f"Skipping comment from {comment.user.login} - created after deadline ({comment.created_at})")
                    continue

                commenter_source = comment.user.full_name if comment.user.full_name else comment.user.login
                commenter_stuID = self._extract_student_id(commenter_source)

                if not commenter_stuID:
                    self.logger.info(f"Could not extract student ID from: {commenter_source}")
                    continue

                self.logger.info(f"Comment from user {comment.user.login}, full_name: '{comment.user.full_name}', extracted ID: '{commenter_stuID}'")

                # Only count if commenter is different from PR owner
                if commenter_stuID != pr_owner_id and self.ids.get(commenter_stuID):
                    commenter_name = self.ids[commenter_stuID]

                    # Check if comment is low quality
                    comment_body = comment.body or ""
                    if self._is_low_quality_review(comment_body):
                        self.logger.warning(f"{repoName} h{self.args.hw} {commenter_stuID} {commenter_name} left low-quality comment on {pr_owner_id}'s PR: '{comment_body[:50]}'")
                    else:
                        res[commenter_name]["noReview"] = 0
                        self.logger.info(f"{repoName} h{self.args.hw} {commenter_stuID} {commenter_name} commented on {pr_owner_id}'s PR")
                elif commenter_stuID == pr_owner_id:
                    self.logger.info(f"Skipping comment from PR owner {pr_owner_id}")
                else:
                    self.logger.info(f"Comment from unknown student ID: {commenter_stuID}")
        except Exception as e:
            self.logger.error(f"Failed to get comments for {repoName} PR #{pull.number}: {e}")

    def _process_pr_reviews(self, repoName, pull, pr_owner_id, res):
        """Process reviews on a PR and update review status."""
        try:
            reviews = self.teapot.gitea.repository_api.repo_list_pull_reviews(
                self.teapot.gitea.org_name, repoName, pull.number)
            self.logger.info(f"{repoName} h{self.args.hw} get pr reviews for #{pull.number}, found {len(reviews)} reviews")

            for review in reviews:
                if review.user is None:
                    continue

                # Check if review was created before deadline
                if hasattr(review, 'submitted_at') and not self._is_before_deadline(review.submitted_at):
                    self.logger.info(f"Skipping review from {review.user.login} - submitted after deadline ({review.submitted_at})")
                    continue

                # Extract student ID from full_name or login
                source_text = review.user.full_name or review.user.login
                reviewer_stuID = self._extract_student_id(source_text)

                if not reviewer_stuID:
                    self.logger.info(f"Could not extract student ID from: {source_text}")
                    continue

                has_body = review.body and review.body.strip() != ""
                self.logger.info(f"Review from user {review.user.login}, full_name: '{review.user.full_name}', extracted ID: '{reviewer_stuID}', has_body: {has_body}")

                # Only count if reviewer is different from PR owner
                if reviewer_stuID != pr_owner_id and self.ids.get(reviewer_stuID):
                    reviewer_name = self.ids[reviewer_stuID]

                    # Check if review is low quality
                    review_body = review.body or ""
                    if self._is_low_quality_review(review_body):
                        self.logger.warning(f"{repoName} h{self.args.hw} {reviewer_stuID} {reviewer_name} left low-quality review on {pr_owner_id}'s PR: '{review_body[:50]}'")
                    else:
                        res[reviewer_name]["noReview"] = 0
                        self.logger.info(f"{repoName} h{self.args.hw} {reviewer_stuID} {reviewer_name} reviewed {pr_owner_id}'s PR")
                elif reviewer_stuID == pr_owner_id:
                    self.logger.info(f"Skipping review from PR owner {pr_owner_id}")
                else:
                    self.logger.info(f"Review from unknown student ID: {reviewer_stuID}")
        except Exception as e:
            self.logger.error(f"Failed to get reviews for {repoName} PR #{pull.number}: {e}")

    def _process_pr_review_comments(self, repoName, pull, pr_owner_id, res):
        """Process code review comments (line comments) on a PR."""
        try:
            # First get all reviews to get review IDs
            reviews = self.teapot.gitea.repository_api.repo_list_pull_reviews(
                self.teapot.gitea.org_name, repoName, pull.number)

            total_review_comments = 0
            for review in reviews:
                if review.id is None:
                    continue

                # Get review comments for this specific review
                review_comments = self.teapot.gitea.repository_api.repo_get_pull_review_comments(
                    self.teapot.gitea.org_name, repoName, pull.number, review.id)
                total_review_comments += len(review_comments)

                for comment in review_comments:
                    if comment.user is None:
                        continue

                    # Check if review comment was created before deadline
                    if hasattr(comment, 'created_at') and not self._is_before_deadline(comment.created_at):
                        self.logger.info(f"Skipping review comment from {comment.user.login} - created after deadline ({comment.created_at})")
                        continue

                    # Extract student ID from full_name or login
                    source_text = comment.user.full_name or comment.user.login
                    commenter_stuID = self._extract_student_id(source_text)

                    if not commenter_stuID:
                        self.logger.info(f"Could not extract student ID from: {source_text}")
                        continue

                    self.logger.info(f"Review comment from user {comment.user.login}, full_name: '{comment.user.full_name}', extracted ID: '{commenter_stuID}'")

                    # Only count if commenter is different from PR owner
                    if commenter_stuID != pr_owner_id and self.ids.get(commenter_stuID):
                        commenter_name = self.ids[commenter_stuID]

                        # Check if review comment is low quality
                        comment_body = comment.body or ""
                        if self._is_low_quality_review(comment_body):
                            self.logger.warning(f"{repoName} h{self.args.hw} {commenter_stuID} {commenter_name} left low-quality code comment on {pr_owner_id}'s PR: '{comment_body[:50]}'")
                        else:
                            res[commenter_name]["noReview"] = 0
                            self.logger.info(f"{repoName} h{self.args.hw} {commenter_stuID} {commenter_name} left code comment on {pr_owner_id}'s PR")
                    elif commenter_stuID == pr_owner_id:
                        self.logger.info(f"Skipping review comment from PR owner {pr_owner_id}")
                    else:
                        self.logger.info(f"Review comment from unknown student ID: {commenter_stuID}")

            self.logger.info(f"{repoName} h{self.args.hw} get pr review comments for #{pull.number}, found {total_review_comments} review comments total")
        except Exception as e:
            self.logger.error(f"Failed to get review comments for {repoName} PR #{pull.number}: {e}")


    def _process_single_pr(self, pull, repoName, hwNum, res):
        """Process a single pull request to check for reviews."""
        title = pull.title
        if not re.search(rf'\bh{hwNum}\b', title):
            return

        # Extract student ID (12-digit number) to check if it's an individual PR
        match = re.search(_STUDENT_ID_PATTERN, title)
        if not match:
            return  # Not an individual PR, skip

        stuID = match.group(1)
        self.logger.info(f"Found PR '{title}' with student ID {stuID}")

        if not self.ids.get(stuID):
            self.logger.warning(f"Student ID {stuID} not found in ids mapping")
            return

        name = self.ids[stuID]
        res[name]["noIndividualPR"] = 0
        self.logger.info(f"Found individual PR for {name} (ID: {stuID}) in {repoName} h{hwNum}")

        # Check comments, reviews, and review comments (code line comments)
        self._process_pr_comments(repoName, pull, stuID, res)
        self._process_pr_reviews(repoName, pull, stuID, res)
        self._process_pr_review_comments(repoName, pull, stuID, res)

    def checkIndividualPR(self):
        """Check only if individual PRs exist (no review checking)"""
        hwNum = self.args.hw
        res = {key: {"noIndividualPR": 1} for key in self.names.keys()}

        for teamKey, users in self.hgroups.items():
            # Convert team key to Gitea repo name: "hteam-10" -> "hteam10"
            repoName = teamKey.replace("-", "")

            # Use teapot to get pull requests
            try:
                pulls = self.teapot.gitea.repository_api.repo_list_pull_requests(
                    self.teapot.gitea.org_name, repoName, state="all")
            except Exception as e:
                self.logger.error(f"Failed to get PRs for {teamKey} (repo: {repoName}): {e}")
                continue

            for pull in pulls:
                title = pull.title
                if not re.search(rf'\bh{hwNum}\b', title):
                    continue

                # Extract student ID (12-digit number) to check if it's an individual PR
                match = re.search(_STUDENT_ID_PATTERN, title)
                if not match:
                    continue  # Not an individual PR, skip

                stuID = match.group(1)
                self.logger.info(f"Found PR '{title}' with student ID {stuID}")

                if not self.ids.get(stuID):
                    self.logger.warning(f"Student ID {stuID} not found in ids mapping")
                    continue

                name = self.ids[stuID]
                res[name]["noIndividualPR"] = 0
                self.logger.info(f"Found individual PR for {name} (ID: {stuID}) in {repoName} h{hwNum}")

        return res

    def checkReview(self):
        hwNum = self.args.hw
        res = {key: {"noReview": 1, "noIndividualPR": 1} for key in self.names.keys()}

        for teamKey, users in self.hgroups.items():
            # Convert team key to Gitea repo name: "hteam-10" -> "hteam10"
            repoName = teamKey.replace("-", "")

            # Use teapot to get pull requests
            try:
                pulls = self.teapot.gitea.repository_api.repo_list_pull_requests(
                    self.teapot.gitea.org_name, repoName, state="all")
            except Exception as e:
                self.logger.error(f"Failed to get PRs for {teamKey} (repo: {repoName}): {e}")
                continue

            for pull in pulls:
                self._process_single_pr(pull, repoName, hwNum, res)

        return res

    def _mark_team_compile_status(self, users, res, failed):
        """Mark all team members with the given compile status."""
        for _, user_name in users:
            res[user_name]["jojFailCompile"] = 1 if failed else 0

    def _find_release(self, releases, hwNum):
        """Find the release for the given homework number."""
        for release in releases:
            if release.name == f"h{hwNum}":
                return release
        return None

    def _check_commit_status(self, repoName, commit_sha, hwNum):
        """Check if commit has successful status. Returns True if success, False otherwise."""
        try:
            statuses = self.teapot.gitea.repository_api.repo_list_statuses(
                self.teapot.gitea.org_name, repoName, commit_sha)

            if statuses and len(statuses) > 0:
                latest_status = statuses[0]
                status_value = getattr(latest_status, 'status', None)

                if status_value == "success":
                    self.logger.info(f"{repoName} h{hwNum}: Release has successful status ✓")
                    return True
                else:
                    self.logger.warning(f"{repoName} h{hwNum}: Release status is '{status_value}' (not success)")
                    return False
            else:
                self.logger.warning(f"{repoName} h{hwNum}: No status found for release")
                return False

        except Exception as e:
            self.logger.error(f"Failed to get status for {repoName} h{hwNum}: {e}")
            return False

    def _process_team_release(self, teamKey, users, hwNum, res):
        """Process release status for a single team."""
        repoName = teamKey.replace("-", "")

        try:
            # Get releases for this repo
            releases = self.teapot.gitea.get_repo_releases(repoName)
            hw_release = self._find_release(releases, hwNum)

            if not hw_release:
                self.logger.warning(f"{repoName}: No release found for h{hwNum}")
                self._mark_team_compile_status(users, res, failed=True)
                return

            # Check release status via commit status
            commit_sha = hw_release.target_commitish
            success = self._check_commit_status(repoName, commit_sha, hwNum)
            self._mark_team_compile_status(users, res, failed=not success)

        except Exception as e:
            self.logger.error(f"Failed to check release for {repoName}: {e}")
            self._mark_team_compile_status(users, res, failed=True)

    def checkJOJCompile(self):
        """Check if h{hw} release has successful status (green checkmark)"""
        hwNum = self.args.hw
        res = {key: {"jojFailCompile": 0} for key in self.names.keys()}

        for teamKey, users in self.hgroups.items():
            self._process_team_release(teamKey, users, hwNum, res)

        return res

    def checkLateSubmissions(self, deadline_str):
        """Check if h{hw} release was created after the deadline.

        Args:
            deadline_str: Deadline in RFC 3339 format (e.g., 2025-10-10T23:59:59+08:00)

        Returns:
            dict: {student_name: {"lateSubmission": 1 or 0, "releaseTime": "..."}}
        """
        from datetime import datetime

        hwNum = self.args.hw
        res = {key: {"lateSubmission": 0, "releaseTime": ""} for key in self.names.keys()}

        # Parse deadline
        try:
            deadline = datetime.fromisoformat(deadline_str)
        except ValueError as e:
            self.logger.error(f"Invalid deadline format '{deadline_str}': {e}")
            return res

        for teamKey, users in self.hgroups.items():
            repoName = teamKey.replace("-", "")

            try:
                # Get releases for this repo
                releases = self.teapot.gitea.get_repo_releases(repoName)
                hw_release = self._find_release(releases, hwNum)

                if not hw_release:
                    self.logger.warning(f"{repoName}: No release found for h{hwNum}")
                    for _, user_name in users:
                        res[user_name]["lateSubmission"] = 1
                        res[user_name]["releaseTime"] = ""
                    continue

                # Parse release time
                release_time_str = hw_release.created_at

                # Handle both datetime objects and strings
                if isinstance(release_time_str, str):
                    release_time = datetime.fromisoformat(release_time_str.replace('Z', '+00:00'))
                else:
                    # Already a datetime object
                    release_time = release_time_str
                    release_time_str = release_time.isoformat()

                # Check if late
                is_late = release_time > deadline

                # Mark all team members
                for _, user_name in users:
                    res[user_name]["lateSubmission"] = 1 if is_late else 0
                    res[user_name]["releaseTime"] = release_time_str

                if is_late:
                    self.logger.warning(f"{repoName} h{hwNum}: Late submission - released at {release_time_str} (deadline: {deadline_str})")
                else:
                    self.logger.info(f"{repoName} h{hwNum}: On-time submission - released at {release_time_str}")

            except Exception as e:
                import traceback
                self.logger.error(f"Failed to check late submission for {repoName}: {e}")
                self.logger.error(traceback.format_exc())
                for _, user_name in users:
                    res[user_name]["lateSubmission"] = 0
                    res[user_name]["releaseTime"] = "Error"

        return res

    def _calculate_similarity(self, text1, text2):
        """Calculate similarity ratio between two texts (0-1)."""
        return SequenceMatcher(None, text1, text2).ratio()

    def _normalize_text_for_comparison(self, text):
        """Normalize text by removing variable content but keeping structure."""
        import re

        # Convert to lowercase
        text = text.lower()

        # Remove numbers (student IDs, dates, etc.)
        text = re.sub(r'\d+', '', text)

        # Remove URLs
        text = re.sub(r'https?://\S+', '', text)

        # Normalize whitespace (multiple spaces/newlines to single space)
        text = re.sub(r'\s+', ' ', text)

        # Remove placeholder markers like < >
        text = re.sub(r'<[^>]*>', '', text)

        return text.strip()

    def _check_template_markers(self, pr_body):
        """Check if PR body contains unfilled template markers."""
        import re

        # Look for common unfilled template markers
        markers = [
            r'<\s*\d+\s*>',  # <1>, <2>, etc.
            r'<\s*[^>]+\s*>',  # <anything>
            r'\[\s*\]',  # Empty checkboxes [ ]
            r'- \s*$',  # Empty list items
            r'input:\s*$',  # Unfilled input fields
            r'expected output:\s*$',  # Unfilled output fields
            r'observed output:\s*$',  # Unfilled output fields
        ]

        unfilled_count = 0
        for marker in markers:
            unfilled_count += len(re.findall(marker, pr_body, re.MULTILINE))

        return unfilled_count

    def _load_template_content(self, template_path):
        """Load and return template content."""
        try:
            full_path = os.path.join(os.path.dirname(__file__), '..', template_path)
            with open(full_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            self.logger.error(f"Failed to load template {template_path}: {e}")
            return ""

    def _check_pr_description_similarity(self, pr_body, templates):
        """Check if PR body is too similar to any template.

        Returns:
            float: Score from 0-1, where 1 means definitely unfilled template
        """
        if not pr_body or len(pr_body.strip()) < 50:
            return 1.0  # Empty or very short body = unfilled

        # Check for unfilled template markers
        unfilled_markers = self._check_template_markers(pr_body)

        # Normalize and calculate structural similarity
        normalized_body = self._normalize_text_for_comparison(pr_body)

        similarities = []
        for template_content in templates:
            if template_content:
                normalized_template = self._normalize_text_for_comparison(template_content)
                similarity = self._calculate_similarity(normalized_body, normalized_template)
                similarities.append(similarity)

        structural_similarity = max(similarities) if similarities else 0.0

        # Combined score: weighted average of structural similarity and unfilled markers
        # If there are many unfilled markers (>10), it's likely an unfilled template
        marker_score = min(unfilled_markers / 10.0, 1.0)

        # Weight: 60% structural similarity, 40% unfilled markers
        combined_score = (structural_similarity * 0.6) + (marker_score * 0.4)

        return combined_score

    def checkPRDescriptions(self):
        """Check if individual PR descriptions are too similar to templates."""
        hwNum = self.args.hw
        res = {key: {"notWritingPR": 0} for key in self.names.keys()}

        # Load templates
        individual_template = self._load_template_content('individual_submission.md')
        whole_template = self._load_template_content('whole_submission.md')
        templates = [individual_template, whole_template]

        for teamKey, users in self.hgroups.items():
            repoName = teamKey.replace("-", "")

            try:
                pulls = self.teapot.gitea.repository_api.repo_list_pull_requests(
                    self.teapot.gitea.org_name, repoName, state="all")
            except Exception as e:
                self.logger.error(f"Failed to get PRs for {teamKey} (repo: {repoName}): {e}")
                continue

            # Track minimum similarity for each student (across all their PRs)
            student_min_similarity = {}

            for pull in pulls:
                title = pull.title
                if not re.search(rf'\bh{hwNum}\b', title):
                    continue

                # Extract student ID to check if it's an individual PR
                match = re.search(_STUDENT_ID_PATTERN, title)
                if not match:
                    continue

                stuID = match.group(1)
                if not self.ids.get(stuID):
                    continue

                name = self.ids[stuID]
                pr_body = pull.body or ""

                # Calculate similarity
                similarity = self._check_pr_description_similarity(pr_body, templates)

                # Track the minimum similarity (best case) for this student
                if name not in student_min_similarity or similarity < student_min_similarity[name]:
                    student_min_similarity[name] = similarity
                    unfilled = self._check_template_markers(pr_body)
                    self.logger.info(f"{name}: PR #{pull.number} score = {similarity:.2%} (unfilled markers: {unfilled})")

            # Mark students with high similarity (>50%)
            for name, min_sim in student_min_similarity.items():
                if min_sim > 0.6:
                    res[name]["notWritingPR"] = 1
                    self.logger.warning(f"{name}: PR description likely unfilled template (score: {min_sim:.2%})")
                else:
                    self.logger.info(f"{name}: PR description acceptable (score: {min_sim:.2%})")

        return res
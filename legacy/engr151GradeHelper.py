#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import json
import os
import csv
import sys

# Add Joint-Teapot to path - support custom location via environment variable
joint_teapot_path = os.environ.get('JOINT_TEAPOT_PATH',
                                    os.path.join(os.path.dirname(__file__), '..', 'Joint-Teapot'))
sys.path.insert(0, joint_teapot_path)

from joint_teapot import Teapot, logger
from joint_teapot.config import settings as teapot_settings
from util import Logger
import worker
from settings import (
    COURSE_ORG,
    EX_MAX_SCORES,
    HW_PASS_THRESHOLD,
    HW_LANGUAGE,
    LANGUAGE,
    MANDATORY_FILES,
    OPTIONAL_FILES,
    REPOS_BASE_DIR,
    RUBRIC,
)
from worker.CanvasWorker import load_email_to_name_mapping

# File name constants
HTEAMS_CSV_FILE = "hteams.csv"
HTEAMS_JSON_FILE = "hteams.json"
P3TEAMS_JSON_FILE = "p3teams.json"

def parse():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--help',
                        action='store_true',
                        help='show this help message and exit')
    parser.add_argument('-h', '--hw', type=int, help='# homework')
    parser.add_argument('-p', '--proj', type=int, help='# project')
    parser.add_argument('-f',
                        '--feedback',
                        action='store_true',
                        help='give feedback to project')
    parser.add_argument('-m', '--ms', type=int, help='# milestone')
    parser.add_argument('-r',
                        '--rejudge',
                        type=int,
                        default=-1,
                        help='rejudge group num or stu ID')
    parser.add_argument('-a', '--all', action='store_true', help='check all')
    parser.add_argument('-s',
                        '--score',
                        action='store_true',
                        help='generate score')
    parser.add_argument('-t',
                        '--time',
                        type=str,
                        help='deadline in RFC 3339 format (e.g., 2025-10-10T23:59:59+08:00) to use JOJ scoreboard at that time')
    parser.add_argument('-o', '--moss', action='store_true', help='check moss')
    parser.add_argument('-d',
                        '--dir',
                        action='store_true',
                        help='create dir for individual submission')
    parser.add_argument('-i',
                        '--indv',
                        action='store_true',
                        help='check individual submission')
    parser.add_argument('-g',
                        '--group',
                        action='store_true',
                        help='check group submission')
    parser.add_argument('-j',
                        '--joj',
                        action='store_true',
                        help='check joj score')
    parser.add_argument('-u',
                        '--upload',
                        action='store_true',
                        help='upload score to canvas')
    parser.add_argument('-l',
                        '--late',
                        type=str,
                        help='deadline in RFC 3339 format to check for late submissions')
    parser.add_argument('-w',
                        '--warning',
                        action='store_true',
                        help='send Mattermost warnings')
    parser.add_argument('--teams',
                        type=str,
                        default='',
                        help='specific teams to grade (comma-separated, e.g., "hteam-01,hteam-05" or "1,5")')
    args = parser.parse_args()
    if args.help:
        parser.print_help()
        exit(0)
    if args.all:
        args.indv = True
        args.dir = True
        args.group = True
        args.joj = True
        args.score = True
    return args


def filter_teams_by_selection(hgroups, team_selection):
    """
    Filter hgroups based on team selection argument.

    Args:
        hgroups: Dictionary of all teams
        team_selection: String with comma-separated team identifiers (e.g., "1,5" or "hteam-01,hteam-05")

    Returns:
        Filtered dictionary of teams
    """
    if not team_selection:
        return hgroups

    # Parse team selection
    selected_teams = [t.strip() for t in team_selection.split(',')]
    filtered_groups = {}

    for team_spec in selected_teams:
        # Try to parse as number first
        if team_spec.isdigit():
            team_name = f"hteam-{int(team_spec):02}"
        else:
            team_name = team_spec

        if team_name in hgroups:
            filtered_groups[team_name] = hgroups[team_name]
            logger.info(f"Selected team: {team_name}")
        else:
            logger.warning(f"Team {team_name} not found in hgroups")

    if not filtered_groups:
        logger.error("No valid teams selected. Grading all teams.")
        return hgroups

    return filtered_groups


def _check_homework_score(row, hw_num, student_id, scores, max_score, joj_failures, hw_threshold):
    """Check if student passes via homework score >= threshold.

    Args:
        hw_threshold: The minimum score required to pass (from HW_PASS_THRESHOLD)
    """
    h_key = f"h{hw_num}"
    h_score_str = row.get(h_key, '').strip()
    if not h_score_str:
        return False, max_score

    h_score = int(h_score_str)
    if h_score >= hw_threshold:
        scores[student_id] = h_score
        new_max = max(max_score, h_score)
        joj_failures[student_id] = {
            "jojFailHomework": 0,
            "jojFailExercise": 0
        }
        return True, new_max
    return False, max_score


def _calculate_exercise_scores(row, hw_num, valid_exercises):
    """Calculate total normalized exercise score."""
    total_score = 0
    for ex_num, ex_max in valid_exercises.items():
        ex_key = f"h{hw_num}/ex{ex_num}"
        ex_score_str = row.get(ex_key, '').strip()
        ex_score = int(ex_score_str) if ex_score_str else 0

        if ex_max > 0:
            normalized_ex_score = (ex_score / ex_max) * 100
        else:
            normalized_ex_score = 0
        total_score += normalized_ex_score

    return total_score


def _check_homework_failure(student_id, total_score, valid_exercises, selected_students):
    """Check if homework total score is too low."""
    num_valid_exercises = len(valid_exercises)
    max_possible_score = num_valid_exercises * 100

    if max_possible_score == 0:
        return 0

    score_percentage = (total_score / max_possible_score) * 100
    if score_percentage < 50:
        if selected_students is None or student_id in selected_students:
            logger.warning(f"{student_id}: total score {total_score:.1f}/{max_possible_score} ({score_percentage:.1f}%) < 50% (jojFailHomework)")
        return 1
    return 0


def _check_exercise_failures(row, hw_num, valid_exercises, student_id, selected_students):
    """Check if any exercise score is too low and return count of failures (capped at 2)."""
    failure_count = 0
    for ex_num, ex_max in valid_exercises.items():
        ex_key = f"h{hw_num}/ex{ex_num}"
        ex_score_str = row.get(ex_key, '').strip()
        ex_score = int(ex_score_str) if ex_score_str else 0

        ex_percentage = (ex_score / ex_max) * 100
        if ex_percentage < 25:
            if selected_students is None or student_id in selected_students:
                logger.warning(f"{student_id}: {ex_key} score {ex_score}/{ex_max} ({ex_percentage:.1f}%) < 25% (jojFailExercise)")
            failure_count += 1
    # Cap at 2 failures (0.25 * 2 = 0.5 max deduction)
    return min(failure_count, 2)


def read_scores_from_csv(csv_file, email_to_name, hw_num, selected_students=None):
    """
    Read JOJ scores from CSV and detect failures.

    Args:
        csv_file: Path to the CSV file
        email_to_name: Mapping from email to student name
        hw_num: Homework number
        selected_students: Set of student names to show warnings for (None = show all)

    Returns:
        tuple: (scores, max_score, joj_failures)
        - scores: {student_id: total_score}
        - max_score: maximum total score
        - joj_failures: {student_id: {"jojFailHomework": 0/1, "jojFailExercise": 0/1}}
    """
    scores = {}
    max_score = 0
    joj_failures = {}

    # Get exercise max scores configuration for this homework
    ex_max_scores = EX_MAX_SCORES.get(hw_num, {})

    # Get homework pass threshold (default 50 if not configured)
    hw_threshold = HW_PASS_THRESHOLD.get(hw_num, 50)

    # Filter out exercises with max score 0
    valid_exercises = {ex_num: max_s for ex_num, max_s in ex_max_scores.items() if max_s > 0}

    with open(csv_file, mode='r', encoding='utf-8') as file:
        csv_reader = csv.DictReader(file)

        for row in csv_reader:
            email = row.get('', '')  # First column (unnamed) is email
            if not email:
                continue

            student_id = email_to_name.get(email)
            if student_id is None:
                continue  # Skip if email not found in hteams.csv

            # Check h{hw_num} score first - if >= threshold, pass both checks
            passed, max_score = _check_homework_score(row, hw_num, student_id, scores, max_score, joj_failures, hw_threshold)
            if passed:
                continue

            # Calculate total score by normalizing each ex to 100
            total_score = _calculate_exercise_scores(row, hw_num, valid_exercises)
            scores[student_id] = total_score

            if total_score > max_score:
                max_score = total_score

            # Initialize failure flags
            joj_failures[student_id] = {
                "jojFailHomework": _check_homework_failure(student_id, total_score, valid_exercises, selected_students),
                "jojFailExercise": _check_exercise_failures(row, hw_num, valid_exercises, student_id, selected_students)
            }

    return scores, max_score, joj_failures


def send_mattermost_message(teapot, student_id, message):
    """Helper function to send Mattermost message to a student using Teapot"""
    try:
        channel = teapot.mattermost.endpoint.channels.get_channel_by_name_and_team_name(
            teapot.mattermost.team["name"], student_id)
        teapot.mattermost.endpoint.posts.create_post({
            'channel_id': channel['id'],
            'message': message
        })
        return True
    except Exception as e:
        logger.error(f"Failed to send message to {student_id}: {e}")
        return False


def _get_username_from_email(name_to_email, id_to_jaccount, student_name, student_id):
    """Extract username from email or use jaccount from ID mapping.

    Args:
        name_to_email: Dictionary mapping student names to emails
        id_to_jaccount: Dictionary mapping student IDs to jaccounts
        student_name: Student's name
        student_id: Student's ID number

    Returns:
        The jaccount (email username) to use for @ mentions
    """
    # First try to get email from student name
    email = name_to_email.get(student_name, "")
    if email and '@' in email:
        return email.split('@')[0]

    # If not found by name, try to get jaccount from student ID
    if student_id and student_id in id_to_jaccount:
        return id_to_jaccount[student_id]

    # If still not found, log a warning and return a placeholder
    # This should not happen if hteams.csv is up to date
    logger.warning(f"Could not find jaccount for student {student_name} ({student_id})")
    return f"unknown_user_{student_id}"


def _send_warning_to_student(teapot, student_name, student_id, message, name_to_email, id_to_jaccount):
    """Send a warning message to a student via Mattermost."""
    username = _get_username_from_email(name_to_email, id_to_jaccount, student_name, student_id)
    full_message = f"{message} @{username}"
    logger.info(f"Sending to {student_name} ({student_id}): {full_message}")
    return send_mattermost_message(teapot, student_id, full_message)


def _check_and_warn_joj_failures(teapot, joj_scores, ids, name_to_email, id_to_jaccount):
    """Check for non-compiling students and send warnings."""
    warnings_sent = 0
    for student_name, joj_score in joj_scores.items():
        if joj_score <= 0:
            student_id = ids.get(student_name)
            if student_id:
                message = (
                    "Warning: Your JOJ submission did not compile or has a score of 0 or less. "
                    "Please check your code and resubmit."
                )
                if _send_warning_to_student(teapot, student_name, student_id, message, name_to_email, id_to_jaccount):
                    warnings_sent += 1
    return warnings_sent


def _check_and_warn_no_pr(teapot, indv_scores, group_scores, ids, name_to_email, id_to_jaccount):
    """Check for students with no individual PR and send warnings."""
    warnings_sent = 0
    all_students = set(indv_scores.keys()) | set(group_scores.keys())

    for student_name in all_students:
        has_no_pr = (indv_scores.get(student_name, {}).get("noIndividualPR", 0) == 1 or
                     group_scores.get(student_name, {}).get("noIndividualPR", 0) == 1)
        if has_no_pr:
            student_id = ids.get(student_name)
            if student_id:
                message = (
                    "Warning: No individual PR found. "
                    "Please ensure you have opened a pull request following the expected format."
                )
                if _send_warning_to_student(teapot, student_name, student_id, message, name_to_email, id_to_jaccount):
                    warnings_sent += 1
    return warnings_sent


def _check_and_warn_indv_fail_submit(teapot, indv_scores, ids, name_to_email, id_to_jaccount):
    """Check for students with individual submission missing and send warnings."""
    warnings_sent = 0
    for student_name, scores in indv_scores.items():
        if scores.get("indvFailSubmit", 0) == 1:
            student_id = ids.get(student_name)
            if student_id:
                # Build message based on indvComment
                comments = scores.get("indvComment", [])
                if comments:
                    detail = " Details: " + ", ".join(comments)
                else:
                    detail = ""

                message = (
                    "Warning: Your individual submission is missing or incomplete." + detail + " "
                    "Please ensure you have committed and pushed your work to the correct individual branch."
                )
                if _send_warning_to_student(teapot, student_name, student_id, message, name_to_email, id_to_jaccount):
                    warnings_sent += 1
    return warnings_sent


def _check_and_warn_indv_untidy(teapot, indv_scores, ids, name_to_email, id_to_jaccount):
    """Check for students with untidy individual submissions and send warnings."""
    warnings_sent = 0
    for student_name, scores in indv_scores.items():
        if scores.get("indvUntidy", 0) == 1:
            student_id = ids.get(student_name)
            if student_id:
                # Build message based on indvComment
                comments = scores.get("indvComment", [])
                untidy_details = [c for c in comments if "redundant" in c.lower() or "untidy" in c.lower()]
                if untidy_details:
                    detail = " Issues: " + ", ".join(untidy_details)
                else:
                    detail = ""

                message = (
                    "Warning: Your individual submission contains extra or redundant files." + detail + " "
                    "Please remove unnecessary files and keep your submission organized."
                )
                if _send_warning_to_student(teapot, student_name, student_id, message, name_to_email, id_to_jaccount):
                    warnings_sent += 1
    return warnings_sent


def _check_and_warn_not_writing_pr(teapot, indv_scores, ids, name_to_email, id_to_jaccount):
    """Check for students not writing PR descriptions and send warnings."""
    warnings_sent = 0
    for student_name, scores in indv_scores.items():
        if scores.get("notWritingPR", 0) == 1:
            student_id = ids.get(student_name)
            if student_id:
                message = (
                    "Warning: Your individual PR description appears to be an unfilled or minimally-filled template. "
                    "Please provide meaningful descriptions of your work, including what you implemented, "
                    "any bugs you encountered, and your self-evaluation."
                )
                if _send_warning_to_student(teapot, student_name, student_id, message, name_to_email, id_to_jaccount):
                    warnings_sent += 1
    return warnings_sent


def _check_and_warn_no_review(teapot, group_scores, ids, name_to_email, id_to_jaccount):
    """Check for students with no review/comment and send warnings."""
    warnings_sent = 0
    for student_name, scores in group_scores.items():
        if scores.get("noReview", 0) == 1:
            student_id = ids.get(student_name)
            if student_id:
                message = (
                    "Warning: You have not reviewed or commented on others' PRs. "
                    "Please participate in code review to help your teammates."
                )
                if _send_warning_to_student(teapot, student_name, student_id, message, name_to_email, id_to_jaccount):
                    warnings_sent += 1
    return warnings_sent


def _find_student_team(student_name, hgroups):
    """Find which team a student belongs to."""
    for team, members in hgroups.items():
        if any(member[1] == student_name for member in members):
            return team
    return None


def _send_team_release_warning(teapot, student_name, student_id, team_name, hw_num, name_to_email, id_to_jaccount):
    """Send release warning to a team representative."""
    message = (
        f"Warning: Your team ({team_name}) has not released h{hw_num}. "
        f"Please create the release tag as soon as possible."
    )
    if _send_warning_to_student(teapot, student_name, student_id, message, name_to_email, id_to_jaccount):
        logger.info(f"Sent release warning to team {team_name} via {student_name}")
        return True
    return False


def _check_and_warn_no_release(teapot, group_scores, ids, name_to_email, id_to_jaccount, hgroups, hw_num):
    """Check for teams with no release and send warnings."""
    warnings_sent = 0
    warned_teams = set()

    for student_name, scores in group_scores.items():
        if scores.get("groupFailSubmit", 0) <= 0:
            continue

        student_id = ids.get(student_name)
        if not student_id:
            continue

        team_name = _find_student_team(student_name, hgroups)
        if not team_name or team_name in warned_teams:
            continue

        warned_teams.add(team_name)
        if _send_team_release_warning(teapot, student_name, student_id, team_name, hw_num, name_to_email, id_to_jaccount):
            warnings_sent += 1

    return warnings_sent


def _build_joj_warning_message(scores):
    """Build warning message for JOJ homework and exercise failures."""
    messages = []
    if scores.get("jojFailHomework", 0) > 0:
        messages.append("Your JOJ homework total score is below 50%")

    exercise_fail_count = scores.get("jojFailExercise", 0)
    if exercise_fail_count > 0:
        if exercise_fail_count == 1:
            messages.append("One JOJ exercise score is below 25%")
        else:
            messages.append(f"{exercise_fail_count} JOJ exercise scores are below 25%")

    if messages:
        return "Warning: " + ". ".join(messages) + ". Please review and improve your submissions."
    return None


def _check_and_warn_joj_homework_exercise(teapot, indv_scores, ids, name_to_email, id_to_jaccount):
    """Check for jojFailHomework and jojFailExercise and send warnings."""
    warnings_sent = 0
    for student_name, scores in indv_scores.items():
        student_id = ids.get(student_name)
        if not student_id:
            continue

        message = _build_joj_warning_message(scores)
        if message and _send_warning_to_student(teapot, student_name, student_id, message, name_to_email, id_to_jaccount):
            warnings_sent += 1

    return warnings_sent


def send_mattermost_warnings(teapot, joj_scores, indv_scores, group_scores, ids, hgroups=None, hw_num=None, indv_only=False):
    """Send warnings to students via Mattermost using Teapot

    Args:
        indv_only: If True, only send warnings related to noIndividualPR, jojFailHomework, and jojFailExercise.
                   If False (with group mode), skip these warnings and only send group-related warnings.
    """
    # Load email to name mapping and ID to jaccount mapping
    email_to_name, name_to_email, id_to_jaccount = load_email_to_name_mapping(HTEAMS_CSV_FILE)

    # Check for various failure conditions
    warnings_sent = 0

    if indv_only:
        # Individual mode: send all individual-related warnings
        warnings_sent += _check_and_warn_indv_fail_submit(teapot, indv_scores, ids, name_to_email, id_to_jaccount)
        warnings_sent += _check_and_warn_indv_untidy(teapot, indv_scores, ids, name_to_email, id_to_jaccount)
        warnings_sent += _check_and_warn_no_pr(teapot, indv_scores, group_scores, ids, name_to_email, id_to_jaccount)
        warnings_sent += _check_and_warn_not_writing_pr(teapot, indv_scores, ids, name_to_email, id_to_jaccount)
        warnings_sent += _check_and_warn_joj_homework_exercise(teapot, indv_scores, ids, name_to_email, id_to_jaccount)
    else:
        # Group mode: send joj_failures (compile issues), no release warnings, and no review warnings
        warnings_sent += _check_and_warn_joj_failures(teapot, joj_scores, ids, name_to_email, id_to_jaccount)

        # Check for teams with no release and students with no review
        if group_scores and hgroups and hw_num:
            warnings_sent += _check_and_warn_no_release(teapot, group_scores, ids, name_to_email, id_to_jaccount, hgroups, hw_num)
        if group_scores:
            warnings_sent += _check_and_warn_no_review(teapot, group_scores, ids, name_to_email, id_to_jaccount)

    if warnings_sent == 0:
        logger.info("No Mattermost warnings to send - all students passed checks")
    else:
        logger.info(f"Sent {warnings_sent} Mattermost warning(s)")


def _team_has_deductions(members, group_scores):
    """Check if any team member has deductions."""
    deduction_keys = ["groupFailSubmit", "groupUntidy", "groupLowCodeQuality", "noReview"]
    for _, member_name in members:
        if member_name in group_scores:
            scores = group_scores[member_name]
            if any(scores.get(key, 0) > 0 for key in deduction_keys):
                return True
    return False


def _check_pr_reviews(teapot, repo_name, pull, members, issues_found):
    """Check PR for sufficient and quality reviews."""
    reviews = teapot.gitea.repository_api.repo_list_pull_reviews(
        teapot.gitea.org_name, repo_name, pull.number)

    if len(reviews) < len(members) - 1:
        issues_found.append(
            f"PR #{pull.number} has insufficient reviews "
            f"({len(reviews)} reviews for {len(members)} members)"
        )

    for review in reviews:
        if not review.body or len(review.body) < 50:
            issues_found.append(
                f"PR #{pull.number} has low-quality review from {review.user.login}"
            )


def _review_team_prs(teapot, repo_name, hw_num, members):
    """Review all PRs for a team and return list of issues."""
    issues_found = []
    try:
        pulls = teapot.gitea.repository_api.repo_list_pull_requests(
            teapot.gitea.org_name, repo_name, state="all")

        hw_pulls = [p for p in pulls if p.title.startswith(f"h{hw_num}")]

        for pull in hw_pulls:
            _check_pr_reviews(teapot, repo_name, pull, members, issues_found)

    except Exception as e:
        logger.error(f"Failed to review PRs for {repo_name}: {e}")

    return issues_found


def _create_review_issue(teapot, repo_name, hw_num, issues_found):
    """Create a review feedback issue for the team."""
    if issues_found:
        issue_body = "## Code Review Feedback\n\n"
        issue_body += "The following issues were found during the review:\n\n"
        for issue in issues_found:
            issue_body += f"- {issue}\n"
        issue_body += "\nPlease address these issues in future submissions."
    else:
        issue_body = "## Code Review Feedback\n\nGreat work! No major issues found. Keep up the good work!"

    teapot.gitea.create_issue(
        repo_name,
        f"h{hw_num} Review Feedback",
        issue_body,
        assign_every_collaborators=True
    )


def review_teams_with_no_deduction(teapot, hgroups, group_scores, args):
    """Review teams with no deduction and open issues with feedback"""
    logger.info("Reviewing teams with no deduction...")

    for team_name, members in hgroups.items():
        if _team_has_deductions(members, group_scores):
            continue

        logger.info(f"Team {team_name} has no deductions - reviewing...")

        # Convert team name to Gitea repo name: "hteam-10" -> "hteam10"
        repo_name = team_name.replace("-", "")

        try:
            issues_found = _review_team_prs(teapot, repo_name, args.hw, members)
            _create_review_issue(teapot, repo_name, args.hw, issues_found)
            logger.info(f"Created review issue for team {team_name}")
        except Exception as e:
            logger.error(f"Failed to review team {team_name}: {e}")


def _find_hw_release(releases, hw_num):
    """Find the release for a specific homework."""
    for release in releases:
        if release.name == f"h{hw_num}":
            return release
    return None


def _review_team_pr_quality(teapot, repo_name, hw_num, members):
    """Review PR quality for a team and return list of issues."""
    pr_issues = []
    try:
        pulls = teapot.gitea.repository_api.repo_list_pull_requests(
            teapot.gitea.org_name, repo_name, state="all")

        hw_pulls = [p for p in pulls if p.title.startswith(f"h{hw_num}")]

        for pull in hw_pulls:
            reviews = teapot.gitea.repository_api.repo_list_pull_reviews(
                teapot.gitea.org_name, repo_name, pull.number)

            if len(reviews) < len(members) - 1:
                pr_issues.append(f"PR #{pull.number} has insufficient reviews")

            for review in reviews:
                if not review.body or len(review.body) < 50:
                    pr_issues.append(f"PR #{pull.number} has low-quality review")
    except Exception as e:
        logger.error(f"Failed to review PRs for {repo_name}: {e}")

    return pr_issues


def review_all_submissions_for_last_mode(teapot, hgroups, args):
    """Review all submissions for last mode and mark late submissions"""
    logger.info("Reviewing all submissions for last mode...")

    for team_name, members in hgroups.items():
        repo_name = team_name.replace("-", "")

        try:
            releases = teapot.gitea.get_repo_releases(repo_name)
            hw_release = _find_hw_release(releases, args.hw)

            if hw_release:
                logger.info(f"Team {team_name} released h{args.hw} at {hw_release.created_at}")

                # Review PR quality
                pr_issues = _review_team_pr_quality(teapot, repo_name, args.hw, members)
                if pr_issues:
                    logger.warning(f"Team {team_name} has PR issues: {', '.join(pr_issues)}")
            else:
                logger.warning(f"Team {team_name} has no release for h{args.hw}")

        except Exception as e:
            logger.error(f"Failed to review team {team_name}: {e}")



def _extract_team_number(group_name):
    """Extract team number from group name."""
    try:
        return int(''.join(filter(str.isdigit, group_name)))
    except ValueError:
        return None


def _normalize_name(text):
    """Remove Chinese characters and format name to title case."""
    import re
    # Remove Chinese characters (Unicode range for CJK Unified Ideographs)
    text = re.sub(r'[\u4e00-\u9fff]+', '', text).strip()
    # Convert to title case (first letter of each word capitalized)
    return text.title()


def _get_student_info(student):
    """Extract student ID and name from student object."""
    student_id = getattr(student, 'sis_user_id', None) or getattr(student, 'login_id', str(student.id))
    student_name = student.name.strip()
    # Remove Chinese characters and normalize to title case
    student_name = _normalize_name(student_name)
    return student_id, student_name


def _process_group_members(group, students):
    """Process group members and return list of [student_id, student_name]."""
    members = []
    for membership in group.get_memberships():
        student = next((s for s in students if s.id == membership.user_id), None)
        if student:
            student_id, student_name = _get_student_info(student)
            members.append([student_id, student_name])
    return members


def _fetch_homework_teams(groups, students):
    """Fetch homework teams from Canvas groups."""
    hgroups = {}
    for group in groups:
        if not group.name.lower().startswith("hteam"):
            continue

        team_num = _extract_team_number(group.name)
        if team_num is None:
            logger.warning(f"Skipping '{group.name}' - cannot parse team number")
            continue

        team_name = f"hteam-{team_num:02d}"
        members = _process_group_members(group, students)

        if members:
            hgroups[team_name] = members
            logger.info(f"Added {team_name} with {len(members)} members")

    return hgroups


def _fetch_project_teams(groups, students):
    """Fetch project teams from Canvas groups."""
    pgroups = {}
    for group in groups:
        if not group.name.lower().startswith(("pteam", "p3team", "project team")):
            continue

        team_num = _extract_team_number(group.name)
        if team_num is None:
            logger.warning(f"Skipping '{group.name}' - cannot parse team number")
            continue

        team_name = f"p3group-{team_num:02d}"
        members = _process_group_members(group, students)

        if members:
            pgroups[team_name] = members
            logger.info(f"Added {team_name} with {len(members)} members")

    return pgroups


def _save_groups_to_files(hgroups, pgroups, teapot, students):
    """Save groups to JSON files."""
    logger.info("Saving groups to JSON files...")

    with open(HTEAMS_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(hgroups, f, indent=2, ensure_ascii=False)
    with open(P3TEAMS_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(pgroups, f, indent=2, ensure_ascii=False)

    logger.info(f"Saved {len(hgroups)} homework teams and {len(pgroups)} project teams")

    # Also generate hteams.csv for convenience
    generate_hteams_csv(teapot, hgroups, students)


def load_or_fetch_groups(teapot):
    """Load groups from JSON files, or fetch from Canvas if not found"""
    # Try to load existing files
    hteams_exists = os.path.exists(HTEAMS_JSON_FILE)
    p3teams_exists = os.path.exists(P3TEAMS_JSON_FILE)

    if hteams_exists and p3teams_exists:
        logger.info("Loading groups from existing JSON files...")
        hgroups = json.load(open(HTEAMS_JSON_FILE))
        pgroups = json.load(open(P3TEAMS_JSON_FILE))
        logger.info(f"Loaded {len(hgroups)} homework teams and {len(pgroups)} project teams")
        return hgroups, pgroups

    # Fetch from Canvas using Teapot
    logger.info("Fetching groups from Canvas using Teapot...")
    try:
        # Try to use teapot.canvas first (faster if it works)
        try:
            groups = list(teapot.canvas.groups)
            students = teapot.canvas.students
        except AttributeError as e:
            # If teapot.canvas fails (e.g., 'User' object has no attribute 'login_id'),
            # fall back to direct Canvas API access
            logger.warning(f"Teapot canvas access failed: {e}")
            logger.info("Falling back to direct Canvas API access...")

            from canvasapi import Canvas as CanvasAPI

            # Ensure canvas_domain_name has https:// prefix
            canvas_url = teapot_settings.canvas_domain_name
            if not canvas_url.startswith('http://') and not canvas_url.startswith('https://'):
                canvas_url = f"https://{canvas_url}"

            canvas_api = CanvasAPI(canvas_url, teapot_settings.canvas_access_token)
            course = canvas_api.get_course(teapot_settings.canvas_course_id)

            groups = list(course.get_groups())
            students = list(course.get_users(enrollment_type=['student']))

        logger.info(f"Found {len(groups)} groups from Canvas")

        hgroups = _fetch_homework_teams(groups, students)
        pgroups = _fetch_project_teams(groups, students)
        _save_groups_to_files(hgroups, pgroups, teapot, students)

    except Exception as e:
        logger.error(f"Failed to fetch groups from Canvas: {e}")
        import traceback
        logger.error(traceback.format_exc())
        if not hteams_exists or not p3teams_exists:
            logger.error("No existing group files found. Cannot continue.")
            sys.exit(1)
        return {}, {}

    return hgroups, pgroups


def generate_hteams_csv(teapot, hgroups, students=None):
    """Generate hteams.csv from groups and students"""
    if not os.path.exists(HTEAMS_CSV_FILE):
        logger.info("Generating hteams.csv...")

        if students is None:
            students = teapot.canvas.students

        # Create student to team mapping
        student_to_team = {}
        for team_name, members in hgroups.items():
            for student_id, student_name in members:
                student_to_team[student_id] = team_name
                student_to_team[student_name] = team_name

        # Generate CSV data
        csv_data = []
        for student in students:
            name = student.name.strip()
            # Use sis_id (student ID number), fallback to login_id
            student_id = getattr(student, 'sis_id', None) or getattr(student, 'login_id', str(student.id))
            login_id = getattr(student, 'login_id', student_id)
            email = getattr(student, 'email', f"{login_id}@sjtu.edu.cn")
            team = student_to_team.get(student_id) or student_to_team.get(name) or ""
            csv_data.append([name, student_id, email, team])

        # Save to file
        with open(HTEAMS_CSV_FILE, "w", encoding="utf-8", newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Name", "ID", "Email", "Team"])
            writer.writerows(csv_data)

        logger.info(f"Generated hteams.csv with {len(csv_data)} students")


if __name__ == "__main__":
    # Parse arguments first
    args = parse()

    # Initialize Teapot
    teapot = Teapot()

    # Load or fetch groups
    hgroups, pgroups = load_or_fetch_groups(teapot)

    # Filter teams if --teams argument is provided
    if args.teams:
        hgroups = filter_teams_by_selection(hgroups, args.teams)
        print(f"Grading {len(hgroups)} selected team(s): {', '.join(hgroups.keys())}")
    else:
        print(f"Grading all {len(hgroups)} teams")

    names = [item[1] for value in hgroups.values() for item in value]
    ids = {
        item[1]: item[0]
        for items in hgroups.values() for item in items
    }
    indv_scores, group_scores, joj_scores = {}, {}, {}

    # Get mandatory and optional files based on homework number
    mandatoryFiles = list(set(MANDATORY_FILES.get(args.hw, []))) if args.hw else []
    optionalFiles = OPTIONAL_FILES.get(args.hw, []) if args.hw else []

    print("Mandatory file list:")
    for file in mandatoryFiles:
        print(file)

    # Select language based on homework number from HW_LANGUAGE config
    hw_language = HW_LANGUAGE.get(args.hw, LANGUAGE) if args.hw else LANGUAGE
    print(f"Using language: {hw_language}")

    gitWorker = worker.GitWorker(
        args, hgroups, pgroups, hw_language, mandatoryFiles,
        optionalFiles,
        course_org=COURSE_ORG,
        git_base_url=teapot_settings.git_host,  # Use SSH from Joint-Teapot
        repos_base_dir=REPOS_BASE_DIR,
        logger=Logger(), processCount=8) if args.indv or args.group or args.proj else None
    giteaWorker = worker.GiteaWorker(args, teapot, hgroups)

    # Check if we're in group mode with existing indv CSV (skip individual checks)
    indv_csv_file = f"hws/h{args.hw}indv.csv"
    skip_individual_checks = args.group and not args.indv and os.path.exists(indv_csv_file)

    # If skipping individual checks, also skip JOJ checks (JOJ already in indv.csv)
    skip_joj_checks = skip_individual_checks
    auto_generated_indv = False  # Track if we auto-generated indv.csv

    if skip_individual_checks:
        logger.info(f"Found existing {indv_csv_file}, skipping individual checks")
    if skip_joj_checks:
        logger.info(f"JOJ scores already included in {indv_csv_file}, skipping JOJ checks")

    if args.indv and gitWorker and not skip_individual_checks:
        indv_scores = gitWorker.checkIndv()
        # Check for individual PRs in -i mode (not reviews)
        tmpScores = giteaWorker.checkIndividualPR()

        # Check PR descriptions for template similarity
        prDescScores = giteaWorker.checkPRDescriptions()

        # Merge PR and description checks into indv_scores (preserving existing fields from checkIndv)
        for key in names:
            if key not in indv_scores:
                indv_scores[key] = {}
            # In -i mode: only add noIndividualPR and notWritingPR (preserve other fields like indvFailSubmit)
            indv_scores[key]["noIndividualPR"] = tmpScores.get(key, {}).get("noIndividualPR", 1)
            indv_scores[key]["notWritingPR"] = prDescScores.get(key, {}).get("notWritingPR", 0)
    if args.group and gitWorker:
        group_scores = gitWorker.checkGroup()
        tmpScores = giteaWorker.checkReview()

        # Check JOJ compile status via release status
        jojCompileScores = giteaWorker.checkJOJCompile()

        # Check for late submissions if deadline is provided
        lateSubmissions = {}
        if args.late:
            lateSubmissions = giteaWorker.checkLateSubmissions(args.late)

        for key in group_scores.keys():
            # Filter out individual items from tmpScores (already counted in -i mode)
            # noIndividualPR and notWritingPR are individual checks, only keep noReview
            review_scores = {k: v for k, v in tmpScores.get(key, {}).items() if k == "noReview"}

            group_scores[key] = {
                **group_scores.get(key, {}),
                **review_scores,  # Only noReview
                **jojCompileScores.get(key, {}),
                **lateSubmissions.get(key, {})
            }

    if (args.indv or args.group) and args.joj and not skip_joj_checks:
            import re  # For extracting English names
            import git
            from datetime import datetime

            # Use teapot's git worker to pull JOJ scoreboard
            repo_name = "engr151-joj"
            branch = "grading"
            csv_file_path = f"homework/h{args.hw}.csv"

            # First, manually fetch latest changes before using teapot's checkout
            try:
                # Check if repo already exists locally
                from joint_teapot.config import settings as teapot_settings
                repos_dir = os.path.join(os.getcwd(), "repos")
                local_repo_path = os.path.join(repos_dir, repo_name)

                if os.path.exists(local_repo_path):
                    logger.info(f"Repository exists at {local_repo_path}, fetching latest changes")
                    repo = git.Repo(local_repo_path)
                    logger.info(f"Fetching all branches from origin")
                    repo.git.fetch("--all")
                else:
                    logger.info(f"Repository does not exist yet, will be cloned")
            except Exception as e:
                logger.warning(f"Pre-fetch failed (this is OK if repo doesn't exist): {e}")

            # Clone or checkout the repo using teapot
            repo_dir = teapot.git.repo_clean_and_checkout(repo_name, branch, reset_target=f"origin/{branch}")

            # Double-check: ensure we're at the latest origin/grading
            try:
                repo = git.Repo(repo_dir)
                logger.info(f"Double-checking: Resetting to origin/{branch} to get latest scoreboard")
                repo.git.reset("--hard", f"origin/{branch}")
                repo.git.clean("-d", "-f", "-x")

                # Show current commit info
                current_commit = repo.head.commit
                logger.info(f"Current commit: {current_commit.hexsha[:8]} - {current_commit.message.strip()}")
            except Exception as e:
                logger.error(f"Failed to verify repo state: {e}")

            # If -t/--time is specified, checkout to the commit before the deadline
            if args.time:
                try:
                    # Parse the deadline time (RFC 3339 format)
                    deadline = datetime.fromisoformat(args.time.replace('Z', '+00:00'))
                    logger.info(f"Looking for scoreboard commit before deadline: {deadline}")

                    # Get all commits that modified the CSV file before the deadline
                    commits_before_deadline = []
                    for commit in repo.iter_commits(branch, paths=csv_file_path):
                        commit_time = datetime.fromtimestamp(commit.committed_date, tz=deadline.tzinfo)
                        if commit_time <= deadline:
                            commits_before_deadline.append((commit, commit_time))
                        # Stop early once we find commits before deadline
                        # (iter_commits returns newest first)
                        if commits_before_deadline and commit_time < deadline:
                            break

                    if commits_before_deadline:
                        # Get the most recent commit before deadline
                        latest_commit, commit_time = commits_before_deadline[0]
                        logger.info(f"Found commit {latest_commit.hexsha[:8]} at {commit_time}")

                        # Checkout to that commit
                        repo.git.checkout(latest_commit.hexsha)
                        logger.info("Checked out to commit before deadline")
                    else:
                        logger.warning(f"No commits found before deadline {deadline}, using latest commit")

                except Exception as e:
                    logger.error(f"Failed to checkout to commit before deadline: {e}")
                    logger.warning("Using latest commit instead")

            local_csv_file = os.path.join(repo_dir, csv_file_path)

            # Load email to student_id mapping
            email_to_name, _, _ = load_email_to_name_mapping(HTEAMS_CSV_FILE)

            # Get set of selected students (only for filtered teams)
            selected_students = set(names) if args.teams else None

            # Read CSV file scores and check for homework/exercise failures
            joj_scores, max_score, joj_failures = read_scores_from_csv(local_csv_file, email_to_name, args.hw, selected_students)

            # Calculate normalized JOJ score (0-10)
            # Note: joj_scores keys are from hteams.csv (full name with Chinese)
            # but names keys are from hteams.json (English name only)
            # So we need to convert to English names
            normalized_joj_scores = {}
            for full_name, score in joj_scores.items():
                # Extract English name (first part before Chinese characters)
                english_name = re.sub(r'\s*[\u4e00-\u9fa5]+.*$', '', full_name).strip()
                # Normalize to title case to match hteams.json format
                english_name = english_name.title()
                normalized_score = (score / max_score) * 10 if max_score > 0 else 0
                normalized_joj_scores[english_name] = normalized_score
            joj_scores = normalized_joj_scores

            # Track students in scoreboard (for both -i and -g modes)
            students_in_scoreboard = set()
            for full_name in joj_failures.keys():
                english_name = re.sub(r'\s*[\u4e00-\u9fa5]+.*$', '', full_name).strip()
                english_name = english_name.title()
                students_in_scoreboard.add(english_name)

            # Merge JOJ failures based on mode
            # If only -i mode (no -g): put JOJ failures in indv_scores
            # If -g mode (with or without -i): put JOJ failures in group_scores
            if args.group:
                # Group mode: JOJ failures go to group_scores
                for full_name, failures in joj_failures.items():
                    english_name = re.sub(r'\s*[\u4e00-\u9fa5]+.*$', '', full_name).strip()
                    english_name = english_name.title()

                    if english_name not in group_scores:
                        group_scores[english_name] = {}
                    group_scores[english_name].update(failures)
                    logger.debug(f"Student {english_name}: jojFailHomework={failures.get('jojFailHomework', 0)}, jojFailExercise={failures.get('jojFailExercise', 0)}")
            elif args.indv:
                # Individual-only mode: JOJ failures go to indv_scores
                for full_name, failures in joj_failures.items():
                    english_name = re.sub(r'\s*[\u4e00-\u9fa5]+.*$', '', full_name).strip()
                    english_name = english_name.title()

                    if english_name not in indv_scores:
                        indv_scores[english_name] = {}
                    indv_scores[english_name].update(failures)
                    logger.debug(f"Student {english_name}: jojFailHomework={failures.get('jojFailHomework', 0)}, jojFailExercise={failures.get('jojFailExercise', 0)}")

            # Check for students not in scoreboard and mark them as indvFailSubmit (individual issue)
            # This applies to both -i and -g modes
            for student_name in names:
                if student_name not in students_in_scoreboard:
                    if student_name not in indv_scores:
                        indv_scores[student_name] = {}
                    # Set indvFailSubmit to 1 regardless of previous value from gitWorker
                    # JOJ submission missing is a definite failure
                    indv_scores[student_name]["indvFailSubmit"] = 1
                    indv_scores[student_name].setdefault("indvComment", []).append("JOJ submission not found in scoreboard")
                    logger.warning(f"{student_name}: not found in JOJ scoreboard (indvFailSubmit)")



    # Ensure joj_scores is a dictionary
    joj_scores = {str(k): v for k, v in joj_scores.items()}

    # Send Mattermost warnings only if -w/--warning flag is set
    if args.warning and (args.indv or args.group) and args.hw:
        logger.info("Sending Mattermost warnings...")
        # When -i is used without -g, only send individual-related warnings
        indv_only = args.indv and not args.group
        send_mattermost_warnings(
            teapot,
            joj_scores,
            indv_scores,
            group_scores,
            ids,
            hgroups=hgroups if args.group else None,
            hw_num=args.hw,
            indv_only=indv_only
        )

    if args.upload and not args.score:
        csv_file = f"hws/h{args.hw}.csv"
        if os.path.exists(csv_file):
            logger.info(f"Uploading grades from existing CSV: {csv_file}")
            canvasWorker = worker.CanvasWorker(args, RUBRIC, teapot,
                                names, {}, {}, {},
                                ids, hgroups)
            canvasWorker.uploadFromCSV(csv_file)
        else:
            logger.error(f"CSV file not found: {csv_file}. Please run with -s first or check the file path.")

    if args.score:
        # Determine if we're in individual-only mode or group mode
        is_individual_only = args.indv and not args.group
        is_group_mode = args.group

        indv_csv_file = f"hws/h{args.hw}indv.csv"

        if is_individual_only:
            # Generate h<i>indv.csv for individual-only mode
            logger.info(f"Generating individual scores CSV: {indv_csv_file}")
            canvasWorker = worker.CanvasWorker(args, RUBRIC, teapot,
                                names, indv_scores, {}, {},
                                ids, hgroups)
            canvasWorker.exportIndividualScores(indv_csv_file)
            logger.info(f"Individual scores exported to {indv_csv_file}")

        elif is_group_mode:
            # Group mode: check if h<i>indv.csv exists, if not generate it first
            if not os.path.exists(indv_csv_file):
                logger.warning(f"Individual scores file not found: {indv_csv_file}")
                logger.info("Auto-generating individual scores with PR and JOJ checks (no individual submission check)...")

                # Initialize indv_scores for all students (without gitWorker.checkIndv)
                if not indv_scores:
                    logger.info("Initializing individual scores structure...")
                    indv_scores = {name: {} for name in names}

                    # Check for individual PRs (not reviews)
                    tmpScores = giteaWorker.checkIndividualPR()
                    prDescScores = giteaWorker.checkPRDescriptions()

                    # Merge PR and description checks into indv_scores
                    for key in names:
                        # Only add noIndividualPR and notWritingPR (no indvFailSubmit from gitWorker)
                        indv_scores[key]["noIndividualPR"] = tmpScores.get(key, {}).get("noIndividualPR", 1)
                        indv_scores[key]["notWritingPR"] = prDescScores.get(key, {}).get("notWritingPR", 0)

                # Also run JOJ checks for auto-generation (even without -j flag)
                if args.hw:
                    logger.info("Running JOJ checks for individual scores...")
                    import re
                    repo_name = "engr151-joj"
                    branch = "grading"
                    csv_file_path = f"homework/h{args.hw}.csv"

                    try:
                        import git

                        # First, manually fetch latest changes before using teapot's checkout
                        try:
                            # Check if repo already exists locally
                            from joint_teapot.config import settings as teapot_settings
                            repos_dir = os.path.join(os.getcwd(), "repos")
                            local_repo_path = os.path.join(repos_dir, repo_name)

                            if os.path.exists(local_repo_path):
                                logger.info(f"Repository exists at {local_repo_path}, fetching latest changes for auto-generation")
                                repo = git.Repo(local_repo_path)
                                logger.info(f"Fetching all branches from origin")
                                repo.git.fetch("--all")
                            else:
                                logger.info(f"Repository does not exist yet, will be cloned")
                        except Exception as e:
                            logger.warning(f"Pre-fetch failed (this is OK if repo doesn't exist): {e}")

                        # Clone or checkout the repo using teapot
                        repo_dir = teapot.git.repo_clean_and_checkout(repo_name, branch, reset_target=f"origin/{branch}")

                        # Double-check: ensure we're at the latest origin/grading
                        try:
                            repo = git.Repo(repo_dir)
                            logger.info(f"Double-checking: Resetting to origin/{branch} to get latest scoreboard for auto-generation")
                            repo.git.reset("--hard", f"origin/{branch}")
                            repo.git.clean("-d", "-f", "-x")

                            # Show current commit info
                            current_commit = repo.head.commit
                            logger.info(f"Current commit: {current_commit.hexsha[:8]} - {current_commit.message.strip()}")
                        except Exception as e:
                            logger.error(f"Failed to verify repo state: {e}")

                        local_csv_file = os.path.join(repo_dir, csv_file_path)

                        # Load email to student_id mapping
                        email_to_name, _, _ = load_email_to_name_mapping(HTEAMS_CSV_FILE)
                        selected_students = set(names) if args.teams else None

                        # Read CSV file scores and check for homework/exercise failures
                        _, _, joj_failures_auto = read_scores_from_csv(local_csv_file, email_to_name, args.hw, selected_students)

                        # Merge JOJ failures to indv_scores
                        for full_name, failures in joj_failures_auto.items():
                            english_name = re.sub(r'\s*[\u4e00-\u9fa5]+.*$', '', full_name).strip()
                            english_name = english_name.title()

                            if english_name not in indv_scores:
                                indv_scores[english_name] = {}
                            indv_scores[english_name].update(failures)

                        # Note: In auto-generation mode (when running -g -s without prior -i -j -s),
                        # we do NOT mark students not in scoreboard as indvFailSubmit
                        # because we're not checking individual git submissions here

                        logger.info("JOJ checks completed for auto-generation")

                        # Mark that we auto-generated indv.csv with JOJ, so skip subsequent JOJ checks
                        auto_generated_indv = True
                        skip_joj_checks = True

                    except Exception as e:
                        logger.error(f"Failed to run JOJ checks during auto-generation: {e}")
                        logger.warning("Continuing without JOJ checks...")

                # Generate h<i>indv.csv
                temp_canvas_worker = worker.CanvasWorker(args, RUBRIC, teapot,
                                    names, indv_scores, {}, {},
                                    ids, hgroups)
                temp_canvas_worker.exportIndividualScores(indv_csv_file)
                logger.info(f"Individual scores exported to {indv_csv_file}")

            # Load individual scores from CSV
            logger.info(f"Loading individual scores from {indv_csv_file}")
            canvasWorker = worker.CanvasWorker(args, RUBRIC, teapot,
                                names, {}, group_scores, joj_scores,
                                ids, hgroups)
            individual_scores_data = canvasWorker.loadIndividualScoresFromCSV(indv_csv_file)

            # Export merged scores (individual from CSV + group from checks)
            logger.info(f"Generating final merged CSV: hws/h{args.hw}.csv")
            canvasWorker.exportMergedScores(f"hws/h{args.hw}.csv", individual_scores_data)
        else:
            # Legacy mode: both individual and group in same run (not recommended)
            logger.warning("Running both -i and -g together is deprecated. Please use separate runs.")
            canvasWorker = worker.CanvasWorker(args, RUBRIC, teapot,
                                names, indv_scores, group_scores, joj_scores,
                                ids, hgroups)
            canvasWorker.exportScores(f"hws/h{args.hw}.csv")

        if args.upload:
            # For upload, always use the main h<i>.csv file
            if is_individual_only:
                logger.warning("Upload is not recommended in individual-only mode. Please run -g -s first to generate h{args.hw}.csv")
            else:
                canvasWorker.grade2Canvas()


    if args.proj and gitWorker:
        projScores = gitWorker.checkProj(args.proj, args.ms)
        if args.feedback:
            giteaWorker.raiseIssues(projScores)
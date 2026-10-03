import csv
import os
import sys
from util import first, Logger

# Add Joint-Teapot to path - support custom location via environment variable
joint_teapot_path = os.environ.get('JOINT_TEAPOT_PATH',
                                    os.path.join(os.path.dirname(__file__), '..', '..', 'Joint-Teapot'))
sys.path.insert(0, joint_teapot_path)

def load_email_to_name_mapping(hteams_file):
    email_to_name = {}
    name_to_email = {}
    id_to_jaccount = {}  # Map from student ID to jaccount (email username)
    with open(hteams_file, mode='r', encoding='utf-8') as file:
        csv_reader = csv.reader(file)
        next(csv_reader)  # Skip header row
        for row in csv_reader:
            if len(row) < 4:
                continue  # Skip rows that don't have enough columns
            name, student_id, email, _team = row
            email_to_name[email] = name
            name_to_email[name] = email  # Add name to email mapping
            # Also add username-only mapping for JOJ CSV compatibility
            if '@' in email:
                username = email.split('@')[0]
                email_to_name[username] = name
                # Map student ID to jaccount (username)
                if student_id:
                    id_to_jaccount[student_id] = username
    return email_to_name, name_to_email, id_to_jaccount

class CanvasWorker():
    def __init__(self,
                 args,
                 rubric,
                 teapot,
                 names,
                 indvScores,
                 groupScores,
                 jojScores,
                 ids,
                 hgroups,
                 totalScores=None,
                 logger=Logger()):
        self.args = args
        self.rubric = rubric
        self.teapot = teapot
        self.logger = logger
        self.scores = {}
        self.jojScores = jojScores
        self.ids = ids
        self.hgroups = hgroups

        if totalScores is None:
            self.names = names
            for key in names:
                self.scores[key] = {
                    **indvScores.get(key, {}),
                    **groupScores.get(key, {}),
                    "joj": jojScores.get(key, 0)
                }
                # Only set jojFailCompile if it's not already set by GiteaWorker
                # and if JOJ score is 0
                if "jojFailCompile" not in self.scores[key] and self.scores[key]["joj"] == 0:
                    self.scores[key]["jojFailCompile"] = 1
        else:
            self.scores = totalScores

    def generateHomeworkData(self, scoreInfo):
        score = 0
        comment = []
        for key, value in self.rubric.items():
            count = scoreInfo.get(key, 0)
            if count > 0:
                total_deduction = value[0] * count
                score += total_deduction

                # Format deduction: show as integer if it's a whole number
                if total_deduction == int(total_deduction):
                    deduction_str = str(int(total_deduction))
                else:
                    deduction_str = str(total_deduction)

                if count > 1:
                    # Multiple instances: show count
                    comment.append(f"{value[1]} (x{count}), {deduction_str}")
                else:
                    # Single instance: show normally
                    comment.append(f"{value[1]}, {deduction_str}")

        if not comment:
            comment = ['no problem detected by the auto-grader']
        else:
            comment.insert(0, "General Info:")

            # Collect detail comments
            detail_comments = (
                scoreInfo.get("indvComment", []) +
                scoreInfo.get("groupComment", []) +
                scoreInfo.get("jojComment", [])
            )

            # Only add "Detail:" section if there are detail comments
            if detail_comments:
                comment.append("")
                comment.append("Detail:")
                # Join detail comments with commas
                comment.append(", ".join(detail_comments))

        return {
            'submission': {
                'posted_grade': max(score, -2.5)
            },
            'comment': {
                'text_comment': '\n'.join(comment)
            },
        }

    def grade2Canvas(self):
        hwNum = self.args.hw
        # Use teapot to get assignment
        assignments = list(self.teapot.canvas.assignments)
        assignment = first(assignments, lambda x: x.name.startswith(f"h{hwNum}"))

        if not assignment:
            self.logger.error(f"Assignment h{hwNum} not found")
            return

        self.logger.info(f"Grading assignment: h{hwNum}")

        # Build student_id to name mapping (reverse of self.ids)
        id_to_name = {student_id: name for name, student_id in self.ids.items()}

        # Also build jaccount to name mapping from hteams.csv
        jaccount_to_name = {}
        hteams_file = os.path.join(os.path.dirname(__file__), '..', 'hteams.csv')
        try:
            with open(hteams_file, mode='r', encoding='utf-8') as file:
                csv_reader = csv.reader(file)
                next(csv_reader)  # Skip header
                for row in csv_reader:
                    if len(row) < 3:
                        continue
                    name, student_id, email, *_ = row
                    # Normalize name to title case to match hteams.json format
                    import re
                    # Remove Chinese characters and normalize to title case
                    name_normalized = re.sub(r'[\u4e00-\u9fff]+', '', name).strip().title()
                    if '@' in email:
                        jaccount = email.split('@')[0]
                        jaccount_to_name[jaccount] = name_normalized
        except Exception as e:
            self.logger.warning(f"Failed to load jaccount mapping from hteams.csv: {e}")

        # Get all users in course
        users_by_canvas_id = {}
        try:
            students = list(self.teapot.canvas.students)
            for student in students:
                users_by_canvas_id[student.id] = student
        except Exception as e:
            self.logger.error(f"Failed to get students: {e}")
            return

        # Process submissions
        matched_count = 0
        for submission in assignment.get_submissions():
            user = users_by_canvas_id.get(submission.user_id)
            if not user:
                self.logger.warning(f"User {submission.user_id} not found")
                continue

            # Try to match by student_id first, then by jaccount from email
            student_name = None
            matched_by = None

            # Try login_id (student_id/学号) first
            login_id = getattr(user, 'login_id', None)
            if login_id and login_id in id_to_name:
                student_name = id_to_name[login_id]
                matched_by = f"login_id={login_id}"

            # If not matched, try sis_user_id
            if not student_name:
                sis_user_id = getattr(user, 'sis_user_id', None) or getattr(user, 'sis_id', None)
                if sis_user_id and sis_user_id in id_to_name:
                    student_name = id_to_name[sis_user_id]
                    matched_by = f"sis_user_id={sis_user_id}"

            # If still not matched, try jaccount from email
            if not student_name:
                email = getattr(user, 'email', None)
                if email and '@' in email:
                    jaccount = email.split('@')[0]
                    if jaccount in jaccount_to_name:
                        student_name = jaccount_to_name[jaccount]
                        matched_by = f"jaccount={jaccount}"

            if not student_name:
                self.logger.warning(f"Could not match user_id={submission.user_id}, login_id={login_id}, sis_user_id={getattr(user, 'sis_user_id', None)}, email={getattr(user, 'email', None)}")
                continue

            if student_name not in self.scores:
                self.logger.warning(f"No scores available for {student_name}")
                continue

            data = self.generateHomeworkData(self.scores[student_name])
            self.logger.debug(f"Generated data for {student_name} (matched by {matched_by}): {data.__repr__()}")

            submission.edit(**data)
            self.logger.info(f"Submission for {student_name} updated successfully (matched by {matched_by})")
            matched_count += 1

        self.logger.info(f"Grading completed: {matched_count} grades uploaded")

    def uploadFromCSV(self, csv_file):
        hwNum = self.args.hw

        # Create a direct Canvas connection without using teapot.canvas
        # This avoids issues with patch_user on non-student users
        from canvasapi import Canvas as PyCanvas
        from joint_teapot.config import settings

        canvas = PyCanvas(
            f"https://{settings.canvas_domain_name}{settings.canvas_suffix}",
            settings.canvas_access_token
        )
        course = canvas.get_course(settings.canvas_course_id)
        assignments = list(course.get_assignments())

        assignment = first(assignments, lambda x: x.name.startswith(f"h{hwNum}"))

        if not assignment:
            self.logger.error(f"Assignment h{hwNum} not found")
            return

        self.logger.info(f"Uploading grades from CSV to assignment: h{hwNum}")

        # Read CSV data - index by both jaccount and student_id
        csv_data_by_jaccount = {}
        csv_data_by_id = {}
        with open(csv_file, mode='r', encoding='utf-8') as file:
            csv_reader = csv.DictReader(file)
            for row in csv_reader:
                name = row.get("Name", "").strip()
                student_id = row.get("ID", "").strip()
                jaccount = row.get("Jaccount", "").strip()
                score = row.get("Score", "").strip()
                comments = row.get("Comments", "").strip()

                if score:
                    grade_data = {
                        'submission': {'posted_grade': float(score)},
                        'comment': {'text_comment': comments},
                        'name': name  # Keep name for logging
                    }
                    if jaccount:
                        csv_data_by_jaccount[jaccount] = grade_data
                    if student_id:
                        csv_data_by_id[student_id] = grade_data

        self.logger.info(f"Loaded {len(csv_data_by_jaccount)} grades indexed by jaccount")
        self.logger.info(f"Loaded {len(csv_data_by_id)} grades indexed by student ID")

        # Get all users in course with their identifiers
        users_by_canvas_id = {}
        try:
            for user in course.get_users(enrollment_type=['student']):
                users_by_canvas_id[user.id] = user
        except Exception as e:
            self.logger.error(f"Failed to get course users: {e}")
            return

        # Process submissions
        matched_count = 0
        for submission in assignment.get_submissions():
            # Get user info
            user = users_by_canvas_id.get(submission.user_id)
            if not user:
                self.logger.warning(f"User {submission.user_id} not found in course users")
                continue

            # Try to match by student_id first, then by jaccount
            grade_data = None
            matched_by = None

            # Try login_id (student_id/学号) first
            login_id = getattr(user, 'login_id', None)
            if login_id and login_id in csv_data_by_id:
                grade_data = csv_data_by_id[login_id]
                matched_by = f"login_id={login_id}"

            # If not matched, try sis_user_id
            if not grade_data:
                sis_user_id = getattr(user, 'sis_user_id', None)
                if sis_user_id and sis_user_id in csv_data_by_id:
                    grade_data = csv_data_by_id[sis_user_id]
                    matched_by = f"sis_user_id={sis_user_id}"

            # If still not matched, try jaccount from email
            if not grade_data:
                email = getattr(user, 'email', None)
                if email and '@' in email:
                    jaccount = email.split('@')[0]
                    if jaccount in csv_data_by_jaccount:
                        grade_data = csv_data_by_jaccount[jaccount]
                        matched_by = f"jaccount={jaccount}"

            if not grade_data:
                self.logger.warning(f"No grade data found for user_id={submission.user_id}, login_id={login_id}, sis_user_id={getattr(user, 'sis_user_id', None)}, email={getattr(user, 'email', None)}")
                continue

            # Upload grade
            student_name = grade_data['name']
            upload_data = {
                'submission': grade_data['submission'],
                'comment': grade_data['comment']
            }
            self.logger.debug(f"Uploading grade for {student_name} (matched by {matched_by}): {upload_data}")
            submission.edit(**upload_data)
            self.logger.info(f"Submission for {student_name} updated successfully (matched by {matched_by})")
            matched_count += 1

        self.logger.info(f"CSV upload completed: {matched_count} grades uploaded")


    def exportScores(self, fileName):
        hteams_file = os.path.join(os.path.dirname(__file__), '..', 'hteams.csv')
        print(f"Loading email to name mapping from {hteams_file}")

        # Build id_to_email mapping from hteams.csv
        id_to_email = {}
        with open(hteams_file, mode='r', encoding='utf-8') as file:
            csv_reader = csv.reader(file)
            next(csv_reader)  # Skip header row
            for row in csv_reader:
                if len(row) < 3:
                    continue
                _name, student_id, email, *_ = row
                id_to_email[student_id] = email

        # Create a mapping of name to team for sorting
        name_to_team = {}
        for team_name, members in self.hgroups.items():
            for member in members:
                name_to_team[member[1]] = team_name

        # Sort names by team number, then by name
        def get_sort_key(name):
            team = name_to_team.get(name, "")
            # Extract team number for proper numeric sorting
            if team and team.startswith("hteam-"):
                try:
                    team_num = int(team.split("-")[1])
                    return (team_num, name)
                except (IndexError, ValueError):
                    pass
            return (999, name)  # Fallback for names without valid team

        sorted_names = sorted(self.scores.keys(), key=get_sort_key)

        with open(fileName, mode='w', encoding='utf-8', newline='') as file:
            csv_writer = csv.writer(file, quotechar='"', quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerow(["Name", "ID", "Jaccount", "Team", "Score", "Late", "Release Time", "Comments"])
            for name in sorted_names:
                score_info = self.scores[name]
                student_id = self.ids.get(name, "")
                # Get email from student_id
                email = id_to_email.get(student_id, "")
                # Extract jaccount from email (part before @)
                jaccount = email.split('@')[0] if '@' in email else ""
                team = name_to_team.get(name, "")

                homework_data = self.generateHomeworkData(score_info)
                comments = homework_data['comment']['text_comment'].replace('\n', ' ')
                grade = homework_data['submission']['posted_grade']

                # Format grade: show as integer if it's a whole number
                if grade == int(grade):
                    grade_str = str(int(grade))
                else:
                    grade_str = str(grade)

                # Check if late submission info exists
                is_late = "Yes" if score_info.get("lateSubmission", 0) == 1 else "No"
                release_time = score_info.get("releaseTime", "")

                csv_writer.writerow([name, student_id, jaccount, team, grade_str, is_late, release_time, comments])
        self.logger.debug(f"Score dump to {fileName} succeed")

    def exportIndividualScores(self, fileName):
        """Export only individual-related scores to h<i>indv.csv"""
        hteams_file = os.path.join(os.path.dirname(__file__), '..', 'hteams.csv')
        print(f"Loading email to name mapping from {hteams_file}")

        # Build id_to_email mapping from hteams.csv
        id_to_email = {}
        with open(hteams_file, mode='r', encoding='utf-8') as file:
            csv_reader = csv.reader(file)
            next(csv_reader)  # Skip header row
            for row in csv_reader:
                if len(row) < 3:
                    continue
                _name, student_id, email, *_ = row
                id_to_email[student_id] = email

        # Create a mapping of name to team for sorting
        name_to_team = {}
        for team_name, members in self.hgroups.items():
            for member in members:
                name_to_team[member[1]] = team_name

        # Sort names by team number, then by name
        def get_sort_key(name):
            team = name_to_team.get(name, "")
            # Extract team number for proper numeric sorting
            if team and team.startswith("hteam-"):
                try:
                    team_num = int(team.split("-")[1])
                    return (team_num, name)
                except (IndexError, ValueError):
                    pass
            return (999, name)  # Fallback for names without valid team

        sorted_names = sorted(self.scores.keys(), key=get_sort_key)

        # Individual-only rubric items (exclude group items like groupFailSubmit, groupUntidy, etc.)
        # JOJ failures are included here when running -i -j mode
        individual_rubric_keys = [
            "indvFailSubmit",
            "indvUntidy",
            "noIndividualPR",
            "notWritingPR",
            "jojFailHomework",
            "jojFailExercise",
        ]

        with open(fileName, mode='w', encoding='utf-8', newline='') as file:
            csv_writer = csv.writer(file, quotechar='"', quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerow(["Name", "ID", "Jaccount", "Team", "Individual_Score", "Individual_Comments"])

            for name in sorted_names:
                score_info = self.scores[name]
                student_id = self.ids.get(name, "")
                # Get email from student_id
                email = id_to_email.get(student_id, "")
                # Extract jaccount from email (part before @)
                jaccount = email.split('@')[0] if '@' in email else ""
                team = name_to_team.get(name, "")

                # Calculate individual score only with proper formatting
                individual_score = 0
                individual_comment_items = []
                for key in individual_rubric_keys:
                    count = score_info.get(key, 0)
                    if count > 0 and key in self.rubric:
                        deduction, comment_template = self.rubric[key]
                        total_deduction = deduction * count
                        individual_score += total_deduction

                        # Format deduction: show as integer if it's a whole number
                        if total_deduction == int(total_deduction):
                            deduction_str = str(int(total_deduction))
                        else:
                            deduction_str = str(total_deduction)

                        if count > 1:
                            # Multiple instances: show count
                            individual_comment_items.append(f"{comment_template} (x{count}), {deduction_str}")
                        else:
                            # Single instance: show normally
                            individual_comment_items.append(f"{comment_template}, {deduction_str}")

                # Build comment string
                if not individual_comment_items:
                    comments_str = "no problem detected by the auto-grader"
                else:
                    comment_parts = ["General Info:"] + individual_comment_items

                    # Add individual-specific detail comments
                    indv_comments = score_info.get("indvComment", [])
                    if indv_comments:
                        comment_parts.append("")
                        comment_parts.append("Detail:")
                        comment_parts.append(", ".join(indv_comments))

                    comments_str = '\n'.join(comment_parts).replace('\n', ' ')

                # Apply score cap: max(score, -2.5)
                individual_score = max(individual_score, -2.5)

                # Format score: show as integer if it's a whole number
                if individual_score == int(individual_score):
                    score_str = str(int(individual_score))
                else:
                    score_str = str(individual_score)

                csv_writer.writerow([name, student_id, jaccount, team, score_str, comments_str])

        self.logger.debug(f"Individual score dump to {fileName} succeed")

    def loadIndividualScoresFromCSV(self, fileName):
        """Load individual scores from h<i>indv.csv

        Returns:
            dict: {student_name: {'Individual_Score': score, 'Individual_Comments': comments}}
        """
        individual_scores = {}

        if not os.path.exists(fileName):
            self.logger.warning(f"Individual scores file not found: {fileName}")
            return individual_scores

        with open(fileName, mode='r', encoding='utf-8') as file:
            csv_reader = csv.DictReader(file)
            for row in csv_reader:
                name = row.get("Name", "").strip()
                score = row.get("Individual_Score", "0").strip()
                comments = row.get("Individual_Comments", "").strip()

                if name:
                    try:
                        individual_scores[name] = {
                            'Individual_Score': float(score),
                            'Individual_Comments': comments
                        }
                    except ValueError:
                        self.logger.warning(f"Invalid score for {name}: {score}")
                        individual_scores[name] = {
                            'Individual_Score': 0.0,
                            'Individual_Comments': comments
                        }

        self.logger.info(f"Loaded {len(individual_scores)} individual scores from {fileName}")
        return individual_scores

    def exportMergedScores(self, fileName, individual_scores_data):
        """Export merged scores combining individual scores from CSV and group scores

        Args:
            fileName: Output file name
            individual_scores_data: Dict from loadIndividualScoresFromCSV
        """
        hteams_file = os.path.join(os.path.dirname(__file__), '..', 'hteams.csv')
        print(f"Loading email to name mapping from {hteams_file}")

        # Build id_to_email mapping from hteams.csv
        id_to_email = {}
        with open(hteams_file, mode='r', encoding='utf-8') as file:
            csv_reader = csv.reader(file)
            next(csv_reader)  # Skip header row
            for row in csv_reader:
                if len(row) < 3:
                    continue
                _name, student_id, email, *_ = row
                id_to_email[student_id] = email

        # Create a mapping of name to team for sorting
        name_to_team = {}
        for team_name, members in self.hgroups.items():
            for member in members:
                name_to_team[member[1]] = team_name

        # Sort names by team number, then by name
        def get_sort_key(name):
            team = name_to_team.get(name, "")
            # Extract team number for proper numeric sorting
            if team and team.startswith("hteam-"):
                try:
                    team_num = int(team.split("-")[1])
                    return (team_num, name)
                except (IndexError, ValueError):
                    pass
            return (999, name)  # Fallback for names without valid team

        sorted_names = sorted(self.scores.keys(), key=get_sort_key)

        # Group-only rubric items (exclude individual items and JOJ failures)
        # jojFailHomework and jojFailExercise are in individual scores (from -i -j mode)
        group_rubric_keys = [
            "groupFailSubmit",
            "groupUntidy",
            "groupLowCodeQuality",
            "noReview",
            "jojFailCompile",  # Only compile check is group-related
        ]

        with open(fileName, mode='w', encoding='utf-8', newline='') as file:
            csv_writer = csv.writer(file, quotechar='"', quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerow(["Name", "ID", "Jaccount", "Team", "Score", "Late", "Release Time", "Comments"])

            for name in sorted_names:
                score_info = self.scores[name]
                student_id = self.ids.get(name, "")
                # Get email from student_id
                email = id_to_email.get(student_id, "")
                # Extract jaccount from email (part before @)
                jaccount = email.split('@')[0] if '@' in email else ""
                team = name_to_team.get(name, "")

                # Get individual score and comments from loaded CSV data
                individual_score = 0
                individual_comment_str = ""
                if name in individual_scores_data:
                    individual_score = individual_scores_data[name]['Individual_Score']
                    individual_comment_str = individual_scores_data[name]['Individual_Comments']

                # Calculate group score only with proper formatting
                group_score = 0
                group_comment_items = []
                for key in group_rubric_keys:
                    count = score_info.get(key, 0)
                    if count > 0 and key in self.rubric:
                        deduction, comment_template = self.rubric[key]
                        total_deduction = deduction * count
                        group_score += total_deduction

                        # Format deduction: show as integer if it's a whole number
                        if total_deduction == int(total_deduction):
                            deduction_str = str(int(total_deduction))
                        else:
                            deduction_str = str(total_deduction)

                        if count > 1:
                            # Multiple instances: show count
                            group_comment_items.append(f"{comment_template} (x{count}), {deduction_str}")
                        else:
                            # Single instance: show normally
                            group_comment_items.append(f"{comment_template}, {deduction_str}")

                # Combine individual and group scores
                total_score = individual_score + group_score

                # Apply score cap: max(score, -2.5)
                total_score = max(total_score, -2.5)

                # Build final comment string
                if individual_comment_str == "no problem detected by the auto-grader" and not group_comment_items:
                    comments_str = "no problem detected by the auto-grader"
                else:
                    comment_parts = []

                    # Parse individual comments to extract items (skip "no problem detected")
                    if individual_comment_str and individual_comment_str != "no problem detected by the auto-grader":
                        # Individual comments are already formatted, just include them
                        # They start with "General Info:" and may have "Detail:"
                        comment_parts.append(individual_comment_str)

                    # Add group comment items
                    if group_comment_items:
                        if comment_parts:
                            # If we have individual comments, append group items after General Info
                            # Parse the individual comment to insert group items properly
                            indv_lines = individual_comment_str.split(' Detail: ')
                            if len(indv_lines) == 2:
                                # Has detail section
                                general_part = indv_lines[0]  # "General Info: ..."
                                detail_part = indv_lines[1]
                                # Append group items to general part
                                combined_general = general_part + " " + " ".join(group_comment_items)
                                comment_parts = [combined_general]

                                # Add group-specific detail comments
                                group_detail_comments = score_info.get("groupComment", [])
                                if group_detail_comments:
                                    all_details = detail_part + ", " + ", ".join(group_detail_comments)
                                    comment_parts.append(" Detail: " + all_details)
                                else:
                                    comment_parts.append(" Detail: " + detail_part)
                            else:
                                # No detail section in individual, just append
                                comment_parts = [individual_comment_str + " " + " ".join(group_comment_items)]

                                # Add group-specific detail comments if any
                                group_detail_comments = score_info.get("groupComment", [])
                                if group_detail_comments:
                                    comment_parts.append("")
                                    comment_parts.append("Detail:")
                                    comment_parts.append(", ".join(group_detail_comments))
                        else:
                            # Only group comments
                            comment_parts = ["General Info:"] + group_comment_items

                            # Add group-specific detail comments
                            group_detail_comments = score_info.get("groupComment", [])
                            if group_detail_comments:
                                comment_parts.append("")
                                comment_parts.append("Detail:")
                                comment_parts.append(", ".join(group_detail_comments))

                    comments_str = '\n'.join(comment_parts).replace('\n', ' ')

                # Format score: show as integer if it's a whole number
                if total_score == int(total_score):
                    score_str = str(int(total_score))
                else:
                    score_str = str(total_score)

                # Check if late submission info exists
                is_late = "Yes" if score_info.get("lateSubmission", 0) == 1 else "No"
                release_time = score_info.get("releaseTime", "")

                csv_writer.writerow([name, student_id, jaccount, team, score_str, is_late, release_time, comments_str])

        self.logger.debug(f"Merged score dump to {fileName} succeed")


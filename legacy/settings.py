import os
import sys
from dotenv import load_dotenv

# Load gradehelper-specific environment variables from .env.gradehelper
load_dotenv(os.path.join(os.path.dirname(__file__), '.env.gradehelper'))

# Add Joint-Teapot to path - support custom location via environment variable
joint_teapot_path = os.environ.get('JOINT_TEAPOT_PATH',
                                    os.path.join(os.path.dirname(__file__), '..', 'Joint-Teapot'))
sys.path.insert(0, joint_teapot_path)

try:
    from joint_teapot.config import settings as teapot_settings

    # Use Joint-Teapot's configuration for all common settings
    CANVAS_TOKEN = teapot_settings.canvas_access_token
    COURSE_ID = teapot_settings.canvas_course_id
    CANVAS_BASE_URL = teapot_settings.canvas_domain_name

    GITEA_TOKEN = teapot_settings.gitea_access_token
    GITEA_BASE_URL = f"https://{teapot_settings.gitea_domain_name}{teapot_settings.gitea_suffix}/api/v1"
    ORG_NAME = teapot_settings.gitea_org_name
    COURSE_ORG = teapot_settings.gitea_org_name

    GIT_BASE_URL = f"https://{teapot_settings.gitea_domain_name}{teapot_settings.gitea_suffix}"
    REPOS_BASE_DIR = teapot_settings.repos_dir

    GITEA_JOJ_URL = f"https://{teapot_settings.gitea_domain_name}{teapot_settings.gitea_suffix}/{teapot_settings.gitea_org_name}/engr151-joj.git"

except ImportError:
    # Fallback if Joint-Teapot is not available
    print("Warning: Joint-Teapot not found. Using fallback configuration.")
    CANVAS_TOKEN = os.getenv('CANVAS_TOKEN', '')
    COURSE_ID = int(os.getenv('CANVAS_COURSE_ID', '0'))
    CANVAS_BASE_URL = os.getenv('CANVAS_BASE_URL', '')
    GITEA_TOKEN = os.getenv('GITEA_TOKEN', '')
    GITEA_BASE_URL = os.getenv('GITEA_BASE_URL', '')
    ORG_NAME = os.getenv('ORG_NAME', '')
    COURSE_ORG = os.getenv('COURSE_ORG', '')
    GIT_BASE_URL = os.getenv('GIT_BASE_URL', '')
    REPOS_BASE_DIR = os.getenv('REPOS_BASE_DIR', 'repos')
    GITEA_JOJ_URL = os.getenv('GITEA_JOJ_URL', '')

# Gradehelper-specific configuration (from local .env)
PROCESS_COUNT = int(os.getenv('GRADEHELPER_PROCESS_COUNT', '16'))
LANGUAGE = os.getenv('GRADEHELPER_LANGUAGE', 'matlab')  # Default language
MOSS_USER_ID = int(os.getenv('GRADEHELPER_MOSS_USER_ID', '0'))

# Language Configuration per Homework
# Format: {homework_num: language}
# Supported languages: 'matlab', 'c', 'cc' (C++)
# If not specified for a homework, LANGUAGE default will be used
HW_LANGUAGE = {
    1: 'matlab',
    2: 'matlab',
    3: 'matlab',
    4: 'c',
    5: 'c',
    6: 'c',
    7: 'cc',
    8: 'cc',
    # Add more homework language configurations as needed
    # Example: 4: 'c', 5: 'cc'
}

# Rubric Configuration
RUBRIC = {
    "indvFailSubmit": [-1, 'individual submission missing'],
    "indvUntidy": [-0.25, 'individual branch untidy'],
    "groupFailSubmit": [-2.5, 'group submission missing'],
    "groupUntidy": [-0.25, 'group submission untidy'],
    "jojFailHomework": [-0.5, 'JOJ homework not passed'],
    "jojFailExercise": [-0.25, 'JOJ exercise not passed'],
    "jojFailCompile": [-2.5, 'JOJ failed to compile'],
    "groupLowCodeQuality": [-0.5, 'group code quality issue'],  # -0.5 per issue
    "noReview": [-1, 'missing others\' code review'],
    "noIndividualPR": [-0.5, 'no individual PR found'],
    "notWritingPR": [-0.25, 'not writing PR as required'],
}

# File Configuration per Homework
# Format: {homework_num: [list of files]}
# If not specified for a homework, empty list will be used
MANDATORY_FILES = {
    1: ["ex2.m", "ex5.m", "ex6.m"], 
    2: ["ex2.m", "ex4.m", "ex5.m", "ex6.m"], 
    3: ["ex1.m", "ex3.m", "ex5.m"], 
    4: ["CMakeLists.txt", "Makefile", "homework.h", "main.c", "ex1.c", "ex2.c", "ex5.c", "ex5-exp.c", "ex5-prod.c", "ex5-quorem.c", "ex5-sum.c"],
    5: ["CMakeLists.txt", "Makefile", "homework.h", "main.c", "ex3.c", "ex5.c", "ex6.c"],
    6: ["CMakeLists.txt", "Makefile", "homework.h", "main.c", "ex2.c", "ex4.c", "ex5.c", "ex7.c"],
    7: ["CMakeLists.txt", "Makefile", "homework.h", "main.cpp", "ex3.cpp", "ex4.cpp", "ex5.cpp"],
    8: ["CMakeLists.txt", "Makefile", "homework.h", "main.cpp", "ex1.cpp", "ex2.cpp"],
    # Add more homework configurations as needed
}

OPTIONAL_FILES = {
    1: ["ex3.m", "ex4.m", "ex7.m"], 
    2: ["ex1.m", "ex3.m"], 
    3: ["ex2.m", "ex4.m", "ex6.m"], 
    4: ["h4-math.c", "ex3.c", "ex4.c", "ex5-exp.h", "ex5-prod.h", "ex5-quorem.h", "ex5-sum.h", "ex5.h", "ex1.h", "ex2.h", "ex3.h", "ex4.h"],
    5: ["ex1.c", "ex2.c", "ex4.c", "ex1.h", "ex2.h", "ex3.h", "ex4.h", "ex5.h", "ex6.h"],
    6: ["ex1.c", "ex3.c", "ex6.c", "ex1.h", "ex2.h", "ex3.h", "ex4.h", "ex5.h", "ex6.h", "ex7.h"],
    7: ["ex1.cpp", "ex2.cpp", "ex1.h", "ex2.h", "ex3.h", "ex4.h", "ex5.h", "ex5.c", "h7-c2cpp.c"],
    8: ["ex3.cpp", "ex1.h", "ex2.h", "ex3.h", "ex2-shapes.cpp", "ex2-shapes.h"],
    # Add more homework configurations as needed
}


# JOJ Exercise Configuration
# Format: {homework_num: {exercise_num: max_score}}
# Set max_score to 0 to skip that exercise in grading
EX_MAX_SCORES = {
    1: {2: 10, 3: 0, 4: 0, 5: 10, 6:50, 7:0},
    2: {1: 0, 2: 100, 3: 0, 4: 100, 5: 100, 6: 100},
    3: {1: 10, 2: 0, 3: 10, 4: 0, 5: 100, 6: 0},
    4: {1: 200, 2: 200, 3: 0, 4: 0, 5: 200},
    5: {1: 0, 2: 0, 3: 200, 4: 0, 5: 0, 6: 0},
    6: {1: 0, 2: 100, 3: 0, 4: 400, 5: 400, 6: 0, 7: 400},
    7: {1: 0, 2: 0, 3: 500, 4: 500, 5: 500},
    8: {1: 150, 2: 150},
    # Add more homework configurations as needed
    # 2: {1: 100, 2: 100, 3: 100},
}

# JOJ Homework Total Score Threshold Configuration
# Format: {homework_num: threshold_score}
# If h{N} score >= threshold, student passes both jojFailHomework and jojFailExercise checks
# Default threshold is 50 if not specified
HW_PASS_THRESHOLD = {
    1: 50,
    2: 50,
    3: 50,
    4: 300,
    5: 100,
    6: 650,
    7: 750,
    8: 150,
    # Add more homework thresholds as needed
}


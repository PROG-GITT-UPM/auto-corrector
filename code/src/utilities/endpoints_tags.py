# Endpoints declaration and the tags that group them in the API docs (/docs)
BASE_ENDPOINT = ""

# Default endpoints: service information and health
DEFAULT_TAG = "default"
DEFAULT_ENDPOINT = "/"
HEALTH_ENDPOINT = "/health"

# Assignments: what is graded, hidden tests and push watching
ASSIGNMENTS_TAG = "assignments"
BASE_ASSIGNMENTS_ENDPOINT = f"{BASE_ENDPOINT}/assignments"

# Grading requests
GRADING_TAG = "grading"
BASE_GRADING_ENDPOINT = f"{BASE_ENDPOINT}/grade"

# Jobs: the queue and the grading history
JOBS_TAG = "jobs"
BASE_JOBS_ENDPOINT = f"{BASE_ENDPOINT}/jobs"

# Scores waiting to be written to Classroom 50
SCORES_TAG = "scores"
BASE_SCORES_ENDPOINT = f"{BASE_ENDPOINT}/scores"

TAGS_METADATA = [
    {"name": DEFAULT_TAG, "description": "Service information and health."},
    {"name": ASSIGNMENTS_TAG,
     "description": "Classroom 50 assignments graded by this service, their hidden tests and "
                    "whether every push is graded automatically."},
    {"name": GRADING_TAG, "description": "Ask for submissions to be graded."},
    {"name": JOBS_TAG, "description": "The grading queue and its history."},
    {"name": SCORES_TAG,
     "description": "Scores graded but not yet written to Classroom 50 (they are written in "
                    "batches, one commit per classroom)."},
]

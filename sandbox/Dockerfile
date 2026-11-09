# Image in which student code runs.
#   docker build -t grader-sandbox:3.12 sandbox/
# It only contains Python and pytest. The container is launched without network (--network none),
# so everything the assignments need (libraries, data) has to be here or in the repository.
FROM python:3.12-slim

RUN pip install --no-cache-dir "pytest>=8,<9" \
 && useradd --create-home --uid 10001 student

# If an assignment needs libraries, add them here, with a pinned version:
# RUN pip install --no-cache-dir "numpy==2.1.*"

USER student
WORKDIR /submission
CMD ["sleep", "infinity"]

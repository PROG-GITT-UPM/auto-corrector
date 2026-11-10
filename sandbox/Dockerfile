# Image in which student code runs. Built by `docker compose build` (service `sandbox`).
# It only contains Python and pytest. Containers from it run without network, so everything the
# assignments need (libraries, data) has to be here or in the assignment repository.
FROM python:3.12-slim

# uid 10001 must match SANDBOX_USER in code/src/providers/sandbox.py and the grader image's user.
RUN pip install --no-cache-dir "pytest>=8,<9" \
 && useradd --create-home --uid 10001 student \
 && install -d -o 10001 -g 10001 /submission

# If an assignment needs libraries, add them here, with a pinned version:
# RUN pip install --no-cache-dir "numpy==2.1.*"

USER student
WORKDIR /submission
CMD ["sleep", "infinity"]

# One image for every backend service; compose (and later ECS) picks the command,
# e.g. `python -m tripwire.poller`. psycopg[binary] has wheels for amd64 and arm64,
# so the same file builds for laptops and for the ARM64 Fargate tasks.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tripwire ./tripwire

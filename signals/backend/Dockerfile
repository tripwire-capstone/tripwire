# One image for every backend service; compose (and ECS) picks the command,
# e.g. `python -m tripwire.poller`. psycopg[binary] has wheels for amd64 and arm64,
# so the same file builds for laptops and for the ARM64 Fargate tasks.
#
# Build context is signals/ (the folder above this one), so the UI can be built too:
#   docker build -f backend/Dockerfile .                    full image: backend + built UI (AWS)
#   docker build -f backend/Dockerfile --target backend .   backend only (what compose builds)

# ---- the UI, built once into plain files (served by the API container on AWS)
FROM node:22-slim AS ui
WORKDIR /ui
COPY mock-ui/package.json mock-ui/package-lock.json ./
RUN npm ci
COPY mock-ui/ ./
RUN npm run build

# ---- the backend
FROM python:3.12-slim AS backend

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/tripwire ./tripwire
# The migration task applies these on AWS (tripwire/migrate.py)
COPY backend/migrations ./migrations

# ---- the published image: backend + UI (tripwire/api/serve.py serves /app/ui)
FROM backend AS full
COPY --from=ui /ui/dist /app/ui

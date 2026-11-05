# One image for the three Python services (sync, classifier, api).
# compose.yaml gives each one its own start command.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
COPY sync/requirements.txt sync/
RUN pip install --no-cache-dir -r requirements.txt -r sync/requirements.txt
COPY . .

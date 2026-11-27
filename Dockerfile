# Runs Guardian's always-on protection around the clock on any server or cloud host.
#   docker build -t guardian .
#   docker run -d --restart unless-stopped --env-file .env \
#     -v "$PWD/.tokens.json:/app/.tokens.json" -v "$PWD/guardian-data:/data" guardian
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY guardian ./guardian
RUN pip install --no-cache-dir .
ENV GUARDIAN_STATE_FILE=/data/guardian.json PYTHONUNBUFFERED=1
CMD ["guardian-always-on"]

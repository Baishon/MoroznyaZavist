FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY app/ app/

# bot_storage/ is runtime state, not source -- mount it as a volume at
# `docker run` time so profiles/bans/etc. survive container recreation:
#   docker run --env-file config/.env -v "$(pwd)/bot_storage:/app/bot_storage" <image>
ENTRYPOINT ["python", "main.py"]

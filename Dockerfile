FROM python:3.11-slim

RUN apt-get update && apt-get install -y curl gnupg wget && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir playwright fastapi uvicorn && \
    playwright install --with-deps chromium

WORKDIR /app
COPY server.py .

EXPOSE 5050
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5050"]

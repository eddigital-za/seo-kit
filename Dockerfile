FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash curl jq bc git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config
COPY skills ./skills

ENV SEO_KIT_DIR=/app
ENV PORT=8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

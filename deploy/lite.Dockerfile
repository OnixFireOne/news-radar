# Lightweight image for analyzer and API on the VPS (articles pipeline only).
# Code is baked in at build time; only data/ and config/ are mounted.
FROM python:3.11-slim

WORKDIR /app

COPY deploy/requirements-lite.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY analyzer/ /app/analyzer/
COPY api/ /app/api/
COPY database/ /app/database/
COPY llm_core/ /app/llm_core/
COPY config/ /app/config/

RUN mkdir -p /app/data

ENV PYTHONPATH=/app

CMD ["python", "-m", "analyzer.analyzer"]

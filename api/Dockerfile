FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY api/ /app/api/
COPY database/ /app/database/
# Phase 1: API imports ChromaClient and Embedder from analyzer module
COPY analyzer/ /app/analyzer/

RUN mkdir -p /app/data /app/data/models

# Model cache (shared volume with analyzer in docker-compose keeps one copy of BGE-m3)
ENV HF_HOME=/app/data/models
ENV TRANSFORMERS_CACHE=/app/data/models
ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

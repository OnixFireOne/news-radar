FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY analyzer/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY analyzer/ /app/analyzer/
COPY database/ /app/database/
COPY config/ /app/config/

RUN mkdir -p /app/data /app/data/models

# HuggingFace model cache → mounted as volume so BGE-m3 survives container rebuilds
ENV HF_HOME=/app/data/models
ENV TRANSFORMERS_CACHE=/app/data/models
ENV PYTHONPATH=/app

CMD ["python", "-m", "analyzer.analyzer"]

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Зависимости — кэшируются. Ребилд нужен ТОЛЬКО если меняется requirements.txt
COPY analyzer/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Папки для данных
RUN mkdir -p /app/data /app/data/models

# HuggingFace model cache → volume чтобы BGE-m3 не скачивался каждый раз
ENV HF_HOME=/app/data/models
ENV TRANSFORMERS_CACHE=/app/data/models
ENV PYTHONPATH=/app

# Код монтируется через volume в docker-compose — не копируется сюда
CMD ["python", "-m", "analyzer.analyzer"]

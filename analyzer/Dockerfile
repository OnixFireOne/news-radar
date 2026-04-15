FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY analyzer/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY analyzer/ /app/analyzer/
COPY database/ /app/database/

RUN mkdir -p /app/data

ENV PYTHONPATH=/app

CMD ["python", "-m", "analyzer.analyzer"]

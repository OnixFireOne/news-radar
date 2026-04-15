FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY bot/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY bot/ /app/bot/
COPY database/ /app/database/

RUN mkdir -p /app/data

ENV PYTHONPATH=/app

CMD ["python", "-m", "bot.telegram_bot"]

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

# Копируем зависимости И устанавливаем
COPY collectors/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Копируем весь проект (build context = корень)
COPY collectors/ /app/collectors/
COPY database/ /app/database/

RUN mkdir -p /app/data /app/sessions

ENV PYTHONPATH=/app

CMD ["python", "-m", "collectors.telegram"]

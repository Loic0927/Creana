FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Build static assets without requiring runtime secrets or production services.
RUN SECRET_KEY=build-only-not-for-runtime \
    DEBUG=False \
    ALLOWED_HOSTS=localhost \
    USE_GCS=False \
    python manage.py collectstatic --noinput

CMD ["sh", "-c", "exec gunicorn socialmanager_project.wsgi:application --bind :${PORT} --workers 2 --threads 8 --timeout 0"]

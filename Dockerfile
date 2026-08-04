FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /data/avatars && chown -R app:app /data

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir --upgrade pip==26.2 setuptools==83.0.0 \
    && pip install .

COPY alembic.ini ./
COPY alembic ./alembic

USER app
EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers --no-access-log"]

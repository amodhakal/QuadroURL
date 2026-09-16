FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev

COPY . .

ENV FLASK_DEBUG=false
ENV PORT=8000
ENV GUNICORN_WORKERS=4
ENV GUNICORN_THREADS=8
ENV GUNICORN_TIMEOUT=60
# File-backed metrics shared by this container's workers; scrapes aggregate
# all of them via MultiProcessCollector (#136). /tmp is writable as appuser.
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-multiproc

EXPOSE 8000

RUN groupadd -r appuser && useradd -r -g appuser -m appuser \
    && chown -R appuser:appuser /app

ENV HOME=/home/appuser

USER appuser

CMD ["sh", "-c", "uv run gunicorn --config gunicorn.conf.py --bind 0.0.0.0:8000 --workers $GUNICORN_WORKERS --threads $GUNICORN_THREADS --worker-class gthread --timeout $GUNICORN_TIMEOUT run:app"]

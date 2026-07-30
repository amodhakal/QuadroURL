FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev

COPY . .

ENV FLASK_DEBUG=false
ENV PORT=8000
ENV GUNICORN_WORKERS=4
ENV GUNICORN_THREADS=4

EXPOSE 8000

CMD ["sh", "-c", "uv run gunicorn --bind 0.0.0.0:8000 --workers $GUNICORN_WORKERS --threads $GUNICORN_THREADS --worker-class gthread --timeout 5 run:app"]

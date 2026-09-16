FROM python:3.13-slim

WORKDIR /app

COPY consumer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared /app/shared
COPY consumer /app
ENV PYTHONPATH=/app

CMD ["python", "app.py"]

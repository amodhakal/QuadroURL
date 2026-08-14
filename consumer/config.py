import os


KAFKA_BROKER = os.environ.get("KAFKA_BROKER", "kafka:9092")
KAFKA_GROUP = os.environ.get("KAFKA_GROUP", "request-log-writer")

KAFKA_TOPIC_REQUEST_LOGS = os.environ.get("KAFKA_TOPIC_REQUEST_LOGS", "request-logs")
KAFKA_TOPIC_URL_EVENTS = os.environ.get("KAFKA_TOPIC_URL_EVENTS", "url-events")
KAFKA_TOPIC_URL_CREATES = os.environ.get("KAFKA_TOPIC_URL_CREATES", "url-creates")

DRAIN_INTERVAL_LOGS = int(os.environ.get("DRAIN_INTERVAL_LOGS", "5"))
DRAIN_INTERVAL_EVENTS = int(os.environ.get("DRAIN_INTERVAL_EVENTS", "1"))

BATCH_SIZE_LOGS = int(os.environ.get("BATCH_SIZE_LOGS", "1000"))
BATCH_SIZE_EVENTS = int(os.environ.get("BATCH_SIZE_EVENTS", "500"))

DATABASE_NAME = os.environ.get("DATABASE_NAME", "hackathon_db")
DATABASE_HOST = os.environ.get("DATABASE_HOST", "postgres")
DATABASE_PORT = int(os.environ.get("DATABASE_PORT", 5432))
DATABASE_USER = os.environ.get("DATABASE_USER", "postgres")
DATABASE_PASSWORD = os.environ.get("DATABASE_PASSWORD", "postgres")

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

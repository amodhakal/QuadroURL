import os


KAFKA_BROKER = os.environ.get("KAFKA_BROKER", "kafka:9092")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "request-logs")
KAFKA_GROUP = os.environ.get("KAFKA_GROUP", "request-log-writer")

DATABASE_NAME = os.environ.get("DATABASE_NAME", "hackathon_db")
DATABASE_HOST = os.environ.get("DATABASE_HOST", "postgres")
DATABASE_PORT = int(os.environ.get("DATABASE_PORT", 5432))
DATABASE_USER = os.environ.get("DATABASE_USER", "postgres")
DATABASE_PASSWORD = os.environ.get("DATABASE_PASSWORD", "postgres")

DRAIN_INTERVAL = int(os.environ.get("DRAIN_INTERVAL", "30"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "1000"))

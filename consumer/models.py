"""Consumer-owned pool and canonical models; no Flask dependency."""

from playhouse.pool import PooledPostgresqlDatabase
from shared.schema import create_models
from shared.delivery_models import create_delivery_models

import config

_MAX_CONNECTIONS = {
    "logs": config.DB_MAX_CONNECTIONS_LOGS,
    "events": config.DB_MAX_CONNECTIONS_EVENTS,
    "creates": config.DB_MAX_CONNECTIONS_CREATES,
}
db = PooledPostgresqlDatabase(
    config.DATABASE_NAME,
    host=config.DATABASE_HOST,
    port=config.DATABASE_PORT,
    user=config.DATABASE_USER,
    password=config.DATABASE_PASSWORD,
    max_connections=_MAX_CONNECTIONS.get(config.CONSUMER_TYPE, 10),
    stale_timeout=300,
    connect_timeout=5,
)
models = create_models(db)

delivery_models = create_delivery_models(db, models)
User = models.User
Url = models.Url
Event = models.Event
RequestLog = models.RequestLog

# Shared database models (#144)

`shared/schema.py` is the single source of model fields, defaults, indexes,
relationships, and URL save behavior. `create_models(database)` makes a separate
class family for each service's database connection. App compatibility imports
remain available under `app.models`; the consumer binds its own pool through
`consumer/models.py`, without importing Flask or the API's database proxy.

Consumer payloads may still use `user_id` and `url_id`: Peewee foreign keys
provide those ID accessors without fetching related objects. PostgreSQL's actual
foreign-key constraints were enforced even when the old consumer definitions
used plain integer fields.

Build consumers from the **repository root** (Compose is updated accordingly):

```sh
docker build -f consumer/Dockerfile -t quadrourl-consumer .
```

For local standalone execution from the repository root:

```sh
PYTHONPATH=. python consumer/app.py
```

## Verification

Targeted tests only (no full suite):

```sh
python -m pytest tests/test_shared_schema.py tests/test_model_parity.py -q
```

The smoke test writes a real row via the consumer URL-create handler into SQLite,
then reads it through the authenticated Flask URL endpoint using the app's model
family. Redis is mocked and Kafka is not used. A consumer image build and isolated
container import check additionally validate packaging and lazy connection binding.
A live Kafka/PostgreSQL round trip is not covered by these checks.

This change preserves the existing SQL schema; it does not implement the separate
versioned-migrations request (#156).

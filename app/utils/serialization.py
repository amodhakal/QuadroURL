from datetime import datetime, timezone


def iso_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            return iso_datetime(datetime.fromisoformat(text))
        except (TypeError, ValueError):
            return value
    return value


def serialize_fields(data, *fields):
    result = dict(data)
    for field in fields:
        if field in result:
            result[field] = iso_datetime(result[field])
    return result

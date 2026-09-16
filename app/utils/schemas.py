"""Request contracts shared by legacy/v1 handlers and the OpenAPI document."""

from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlsplit

from flask import abort, request
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    ValidationError,
    field_validator,
)

Text = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=255)
]
PositiveId = Annotated[StrictInt, Field(gt=0)]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserCreate(Body):
    username: Text
    email: Text


class UserUpdate(UserCreate):
    # Omitted fields stay absent in model_dump(exclude_unset=True); explicit null
    # remains invalid, rather than becoming a request to erase a required column.
    username: Text = Field(default=None)
    email: Text = Field(default=None)


class UrlCreate(Body):
    user_id: PositiveId = Field(default=None)
    original_url: Text
    title: Annotated[str, StringConstraints(strict=True, max_length=255)]
    expires_at: datetime | None = None
    request_id: Annotated[str, StringConstraints(strict=True, max_length=255)] = Field(default=None)

    @field_validator("original_url")
    @classmethod
    def safe_url(cls, value):
        try:
            parsed = urlsplit(value)
            valid = parsed.scheme in ("http", "https") and parsed.hostname
        except ValueError:
            valid = False
        if not valid or any(c.isspace() for c in value):
            raise ValueError("original_url must be a valid http(s) URL")
        return value

    @field_validator("expires_at", mode="before")
    @classmethod
    def iso_expiry(cls, value):
        if value is not None and not isinstance(value, str):
            raise ValueError("expires_at must be a timezone-aware ISO 8601 string or null")
        return value

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value):
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("expires_at must include a timezone")
            if value <= datetime.now(timezone.utc):
                raise ValueError("expires_at must be in the future")
            return value.astimezone(timezone.utc)
        return value


class UrlUpdate(Body):
    title: Text = Field(default=None)
    is_active: StrictBool = Field(default=None)
    expires_at: datetime | None = None
    iso_expiry = field_validator("expires_at", mode="before")(UrlCreate.iso_expiry.__func__)
    aware_expiry = field_validator("expires_at")(UrlCreate.aware_expiry.__func__)


class EventCreate(Body):
    url_id: PositiveId
    user_id: PositiveId = Field(default=None)
    event_type: Text
    details: dict = Field(default_factory=dict)


class ApiKeyCreate(Body):
    name: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=255)] = ""


class ListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offset: int = Field(default=0, ge=0, le=100000)
    size: int = Field(default=20, ge=1, le=200)
    page: int | None = Field(default=None, ge=1, le=5001)
    per_page: int | None = Field(default=None, ge=1, le=200)
    before_id: int | None = Field(default=None, gt=0)


class UrlQuery(ListQuery):
    id: int | None = Field(default=None, gt=0)
    user_id: int | None = Field(default=None, gt=0)
    short_code: Annotated[str, Field(max_length=255)] | None = None
    original_url: Annotated[str, Field(max_length=255)] | None = None
    is_active: bool | None = None

    @field_validator("is_active", mode="before")
    @classmethod
    def boolean_query(cls, value):
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        if value is not None:
            raise ValueError("is_active must be 'true' or 'false'")
        return value


class EventQuery(ListQuery):
    url_id: int | None = Field(default=None, gt=0)
    user_id: int | None = Field(default=None, gt=0)
    event_type: Annotated[str, Field(max_length=255)] | None = None


def validate(schema, data):
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        error = exc.errors()[0]
        field = ".".join(map(str, error["loc"]))
        abort(400, description=f"{field}: {error['msg']}")


def parse_body(schema, log_message=None):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, description="Invalid JSON")
    return validate(schema, data).model_dump(exclude_unset=True)


def parse_query(schema):
    if any(len(request.args.getlist(key)) != 1 for key in request.args):
        abort(400, description="Duplicate query parameters are not supported")
    return validate(schema, request.args.to_dict())

"""Request contracts shared by legacy/v1 handlers and the OpenAPI document."""

import json
from datetime import datetime, timezone
from typing import Annotated, Literal
from app.utils.url_safety import validate_destination

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
    model_validator,
)

Text = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=255)
]
PositiveId = Annotated[StrictInt, Field(gt=0)]
MAX_EVENT_DETAILS_BYTES = 16 * 1024
MAX_EVENT_DETAILS_DEPTH = 8
MAX_EVENT_DETAILS_KEYS = 100
MAX_EVENT_DETAILS_ITEMS = 1000


def validate_future_datetime(value):
    if value is not None and not isinstance(value, str):
        raise ValueError("expires_at must be a timezone-aware ISO 8601 string or null")
    if value is None:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("expires_at must be a timezone-aware ISO 8601 string or null") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("expires_at must include a timezone")
    if parsed <= datetime.now(timezone.utc):
        raise ValueError("expires_at must be in the future")
    return parsed.astimezone(timezone.utc)


def validate_event_details(value):
    key_count = 0

    def walk(node, depth):
        nonlocal key_count
        if depth > MAX_EVENT_DETAILS_DEPTH:
            raise ValueError("details nesting is too deep")
        if isinstance(node, dict):
            for key, child in node.items():
                if not isinstance(key, str):
                    raise ValueError("details keys must be strings")
                key_count += 1
                if key_count > MAX_EVENT_DETAILS_KEYS:
                    raise ValueError("details contains too many keys")
                walk(child, depth + 1)
        elif isinstance(node, list):
            if len(node) > MAX_EVENT_DETAILS_ITEMS:
                raise ValueError("details contains too many items")
            for child in node:
                walk(child, depth + 1)

    walk(value, 1)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("details must contain valid JSON values") from exc
    if len(encoded) > MAX_EVENT_DETAILS_BYTES:
        raise ValueError("details is too large")
    return value


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
    # Nullable so an explicit ``"user_id": null`` is accepted and falls back to
    # the caller's own id at the route layer rather than aborting (#302).
    user_id: PositiveId | None = Field(default=None)
    original_url: Text
    title: Annotated[str, StringConstraints(strict=True, max_length=255)]
    expires_at: datetime | None = None
    request_id: Annotated[str, StringConstraints(strict=True, max_length=255)] = Field(default=None)

    @field_validator("original_url")
    @classmethod
    def safe_url(cls, value):
        return validate_destination(value)

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

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        return self


class EventCreate(Body):
    url_id: PositiveId
    # Nullable for the same reason as ``UrlCreate.user_id`` (#302).
    user_id: PositiveId | None = Field(default=None)
    event_type: Text
    details: dict = Field(default_factory=dict)

    @field_validator("details")
    @classmethod
    def bounded_details(cls, value):
        return validate_event_details(value)


class ApiKeyCreate(Body):
    name: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=255)] = ""
    expires_at: datetime | None = None

    @field_validator("expires_at", mode="before")
    @classmethod
    def iso_expiry(cls, value):
        return validate_future_datetime(value)

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


class RoleUpdate(Body):
    is_admin: StrictBool | None = None
    role: Literal["admin", "user"] | None = None

    @model_validator(mode="after")
    def one_role_field(self):
        if (self.is_admin is None) == (self.role is None):
            raise ValueError("Provide exactly one of is_admin or role")
        if self.role is not None:
            self.is_admin = self.role == "admin"
        return self


class AdminBootstrap(Body):
    user_id: PositiveId | None = None
    username: Text | None = None
    email: Text | None = None

    @model_validator(mode="after")
    def valid_identity(self):
        if self.user_id is None and (self.username is None or self.email is None):
            raise ValueError("Provide user_id or both username and email")
        if self.user_id is not None and (self.username is not None or self.email is not None):
            raise ValueError("Provide only user_id or both username and email")
        return self


class WebhookCreate(Body):
    """Owner-scoped click-milestone subscription (#184); shape checked here (#191)."""

    destination: Text
    milestone: Annotated[StrictInt, Field(gt=0, le=2**63 - 1)]

    @field_validator("destination")
    @classmethod
    def approved_destination(cls, value):
        from shared.delivery_worker import validate_destination as approved

        try:
            approved(value)
        except ValueError:
            raise ValueError("Destination must be an operator-approved HTTPS URL")
        return value


class ReplayRequest(Body):
    """Optional corrected JSON-object payload for a dead-letter replay."""

    payload: dict | None = None


class DeadLetterQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    after: int = Field(default=0, ge=0, le=2**63 - 1)


class ExportQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["json", "csv"] = "json"
    limit: int = Field(default=1000, ge=1, le=1000)
    after_id: int = Field(default=0, ge=0, le=2**63 - 1)


class AnalyticsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bucket: Literal["day", "week", "month"] = "day"
    days: int = Field(default=30, ge=1, le=365)


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


class SearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)] = Field(
        max_length=500
    )
    k: int = Field(default=5, ge=1, le=20)


class AskRequest(Body):
    question: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2000)
    ]
    k: int = Field(default=5, ge=1, le=20)


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

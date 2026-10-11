"""OpenAPI 3.1 built from the live route map and shared input contracts."""

import re

from flask import Blueprint, Response, current_app, jsonify

from app.utils.schemas import (
    AnalyticsQuery,
    AdminBootstrap,
    ApiKeyCreate,
    DeadLetterQuery,
    EventCreate,
    EventQuery,
    ExportQuery,
    ListQuery,
    ReplayRequest,
    RoleUpdate,
    UrlCreate,
    UrlQuery,
    UrlUpdate,
    UserCreate,
    UserUpdate,
    WebhookCreate,
)

docs_bp = Blueprint("docs", __name__)
BODY_SCHEMAS = {
    ("users", "POST"): UserCreate,
    ("users", "PUT"): UserUpdate,
    ("urls", "POST"): UrlCreate,
    ("urls", "PUT"): UrlUpdate,
    ("events", "POST"): EventCreate,
    ("auth", "POST"): ApiKeyCreate,
    ("delivery", "POST"): WebhookCreate,
}
QUERY_SCHEMAS = {
    "users": ListQuery,
    "urls": UrlQuery,
    "events": EventQuery,
    "logs": ListQuery,
    "delivery": DeadLetterQuery,
}


def json_response(description, schema=None):
    result = {"description": description}
    if schema is not None:
        result["content"] = {"application/json": {"schema": schema}}
    return result


def build_spec():
    error = {"type": "object", "required": ["error"], "properties": {"error": {"type": "string"}}}
    page = {
        "type": "object",
        "required": ["items", "pagination"],
        "properties": {
            "items": {"type": "array", "items": {"type": "object"}},
            "pagination": {
                "type": "object",
                "required": ["offset", "size", "has_more", "next_offset", "next_before_id"],
                "properties": {
                    "offset": {"type": "integer"},
                    "size": {"type": "integer"},
                    "has_more": {"type": "boolean"},
                    "next_offset": {"type": ["integer", "null"]},
                    "next_before_id": {"type": ["integer", "null"]},
                },
            },
        },
    }
    schemas = {"Error": error, "Page": page}
    for model in set(BODY_SCHEMAS.values()) | {
        ReplayRequest,
        AdminBootstrap,
        RoleUpdate,
        AnalyticsQuery,
        ExportQuery,
        DeadLetterQuery,
        WebhookCreate,
    }:
        schemas[model.__name__] = model.model_json_schema()
    paths = {}
    for rule in current_app.url_map.iter_rules():
        if not rule.rule.startswith("/api/v1/") or rule.endpoint.startswith("docs."):
            continue
        path = re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", rule.rule)
        resource = rule.endpoint.split(".")[0].removesuffix("_v1")
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            session_exchange = resource == "auth" and path == "/auth/session" and method == "POST"
            bootstrap = resource == "auth" and path in {
                "/auth/admin/bootstrap",
                "/auth/bootstrap-admin",
                "/auth/bootstrap",
            }
            role_management = path.endswith("/role")
            public = (
                (resource == "users" and method == "POST" and not path.endswith("/bulk"))
                or rule.endpoint.endswith((".redirect_short_code", ".redirect_short_code_legacy"))
                or session_exchange
                or bootstrap
            )
            parameters = []
            for name in sorted(rule.arguments):
                parameters.append(
                    {
                        "name": name,
                        "in": "path",
                        "required": True,
                        "schema": {
                            "type": "integer" if name in ("url_id", "user_id") else "string"
                        },
                    }
                )
            operation = {
                "operationId": f"{rule.endpoint.replace('.', '_')}_{method.lower()}",
                "summary": rule.endpoint.split(".")[-1].replace("_", " ").capitalize(),
                "tags": [resource],
                "security": ([] if public else [{"bearerAuth": []}, {"sessionCookie": []}]),
                "parameters": parameters,
                "responses": {
                    "200": json_response("Success", {"type": "object"}),
                    "400": json_response("Invalid input", {"$ref": "#/components/schemas/Error"}),
                    "404": json_response("Not found or not owned", error),
                    "429": {
                        **json_response("Rate limit exceeded", error),
                        "headers": {
                            "Retry-After": {
                                "schema": {"type": "integer"},
                                "description": "Seconds until retry",
                            },
                        },
                    },
                    "503": json_response("Required dependency unavailable", error),
                },
            }
            if bootstrap:
                parameters.append(
                    {
                        "name": "X-Admin-Bootstrap-Token",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                )
                operation["responses"]["401"] = json_response("Invalid bootstrap token", error)
            if session_exchange:
                parameters.append(
                    {
                        "name": "Authorization",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                )
            if not public:
                if method in {"POST", "PUT", "PATCH", "DELETE"}:
                    parameters.append(
                        {
                            "name": "X-CSRF-Token",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string"},
                            "description": "Required when authenticating with the session cookie",
                        }
                    )
                operation["responses"]["401"] = json_response(
                    "Missing or invalid bearer key or session", error
                )
                operation["description"] = (
                    "Only owned resources are visible;"
                    " configured administrators may access all owners."
                    " Cookie-authenticated unsafe methods require X-CSRF-Token."
                )
            if resource in ("logs", "fail") or path.endswith("/bulk") or role_management:
                operation["description"] = "Administrator only (user.is_admin)."
                operation["responses"]["403"] = json_response("Administrator required", error)
            list_route = method == "GET" and not rule.arguments and resource in QUERY_SCHEMAS
            if list_route:
                properties = QUERY_SCHEMAS[resource].model_json_schema()["properties"]
                for name, schema in properties.items():
                    if resource == "logs" and name == "before_id":
                        continue
                    parameters.append({"name": name, "in": "query", "schema": schema})
                operation["responses"]["200"] = json_response(
                    "Bounded page", {"$ref": "#/components/schemas/Page"}
                )
            elif method == "GET" and rule.endpoint.endswith(".click_analytics"):
                for name, schema in AnalyticsQuery.model_json_schema()["properties"].items():
                    parameters.append({"name": name, "in": "query", "schema": schema})
            elif method == "GET" and rule.endpoint.endswith(".export_rows"):
                for name, schema in ExportQuery.model_json_schema()["properties"].items():
                    parameters.append({"name": name, "in": "query", "schema": schema})
            model = BODY_SCHEMAS.get((resource, method))
            if resource == "auth" and path != "/auth/api-keys":
                model = None
            if role_management:
                model = RoleUpdate
            if bootstrap:
                model = AdminBootstrap
            if path.endswith("/replay"):
                model = ReplayRequest
            if path.endswith("/bulk"):
                operation["requestBody"] = {
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "required": ["file"],
                                "properties": {"file": {"type": "string", "format": "binary"}},
                            }
                        }
                    },
                }
            elif model:
                operation["requestBody"] = {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": f"#/components/schemas/{model.__name__}",
                            }
                        }
                    },
                }
                if method == "POST":
                    operation["responses"]["201"] = json_response(
                        "Created; registration/key minting returns api_key once", {"type": "object"}
                    )
            if resource == "urls" and method == "POST":
                parameters.append(
                    {"name": "Idempotency-Key", "in": "header", "schema": {"type": "string"}}
                )
                operation["responses"]["202"] = json_response(
                    "Queued; poll authenticated status URL",
                    {
                        "type": "object",
                        "required": ["request_id", "status"],
                        "properties": {
                            "request_id": {"type": "string"},
                            "status": {"const": "pending"},
                        },
                    },
                )
            if rule.endpoint.endswith(".redirect_short_code"):
                operation["responses"] = {
                    "302": {
                        "description": "Redirect to target",
                        "headers": {
                            "Location": {"schema": {"type": "string", "format": "uri"}},
                        },
                    },
                    "404": json_response("Missing, inactive or expired link", error),
                    "429": json_response("Rate limit exceeded", error),
                }
            paths.setdefault(path, {})[method.lower()] = operation
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "quadroURL API",
            "version": "1.0.0",
            "description": (
                "Bearer-key API with optional same-origin cookie sessions. Legacy"
                " unprefixed routes remain available with their original list shapes."
                " v1 lists return items and pagination; offset order is ascending ID,"
                " before_id order descending. Do not mix pagination styles. Logs are"
                " a changing in-memory snapshot. Cookie-authenticated unsafe methods"
                " require a session-bound X-CSRF-Token."
            ),
        },
        "servers": [{"url": "/"}],
        "paths": paths,
        "components": {
            "schemas": schemas,
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "API key"},
                "sessionCookie": {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": "session",
                    "description": (
                        "Same-origin HttpOnly session; unsafe methods require X-CSRF-Token"
                    ),
                },
            },
        },
    }


@docs_bp.get("/openapi.json")
@docs_bp.get("/api/v1/openapi.json")
def openapi():
    return jsonify(build_spec())


@docs_bp.get("/docs")
@docs_bp.get("/api/v1/docs")
def docs():
    response = Response(
        """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>quadroURL API docs</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css">
</head><body><div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({url:'/openapi.json',dom_id:'#swagger-ui',
persistAuthorization:false,validatorUrl:null});</script></body></html>""",
        mimetype="text/html",
    )
    # Overrides the app-wide deny-all default: Swagger UI pulls its CSS/JS
    # from a CDN and runs an inline bootstrap script.
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' https://cdn.jsdelivr.net"
    )
    return response

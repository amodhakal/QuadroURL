"""OpenAPI coverage for delivery, analytics, and exports (#181).

Builds the spec from the real route map: every operation carries responses
and security metadata, schema-validated endpoints expose their query and body
contracts, and the interactive Swagger UI serves on both flavors.
"""

import pytest
from flask import Flask


@pytest.fixture(autouse=True)
def clean_tables():
    yield


@pytest.fixture()
def docs_client():
    from app.routes import register_routes

    instance = Flask(__name__)
    instance.config.update(TESTING=True)
    register_routes(instance)
    yield instance.test_client()


def test_spec_documents_validated_endpoints(docs_client):
    spec = docs_client.get("/openapi.json").json
    assert spec["openapi"].startswith("3.1")
    assert docs_client.get("/api/v1/openapi.json").json == spec

    webhooks = spec["paths"]["/api/v1/urls/{url_id}/webhooks"]["post"]
    assert webhooks["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/WebhookCreate"
    }
    replay = spec["paths"]["/api/v1/admin/dead-letters/{letter_id}/replay"]["post"]
    assert replay["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReplayRequest"
    }
    dead_letters = spec["paths"]["/api/v1/admin/dead-letters"]["get"]
    assert {param["name"] for param in dead_letters["parameters"]} == {"after"}
    analytics = spec["paths"]["/api/v1/urls/{short_code}/analytics"]["get"]
    assert {"bucket", "days"} <= {param["name"] for param in analytics["parameters"]}
    exports = spec["paths"]["/api/v1/exports/{resource}"]["get"]
    assert {"format", "limit", "after_id"} <= {param["name"] for param in exports["parameters"]}

    for schemas in ("WebhookCreate", "ReplayRequest", "AnalyticsQuery", "ExportQuery"):
        assert schemas in spec["components"]["schemas"]

    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            assert "responses" in operation and "security" in operation, f"{method} {path}"
            if operation["security"]:
                assert "401" in operation["responses"], f"{method} {path}"


def test_interactive_docs_serve_on_both_flavors(docs_client):
    for path in ("/docs", "/api/v1/docs"):
        response = docs_client.get(path)
        assert response.status_code == 200
        assert "swagger" in response.text.lower()

"""Tests for the /dashboard endpoint."""


def test_dashboard(admin_client):
    response = admin_client.get("/dashboard")
    assert response.status_code == 200
    assert response.content_type == "text/html"
    assert "quadroPE" in response.get_data(as_text=True)


def test_dashboard_requires_admin(app, client):
    """The HTML page is admin-only like the stream it drives (#302).

    It names the internal stack and renders live operational telemetry, so an
    unauthenticated caller must not receive the page.
    """
    raw = app.test_client()
    unauthenticated = raw.get("/dashboard")
    assert unauthenticated.status_code == 401
    assert "quadroPE" not in unauthenticated.get_data(as_text=True)
    assert raw.get("/dashboard", headers={"Authorization": ""}).status_code == 401
    # Authenticated but not an administrator.
    assert client.get("/dashboard").status_code == 403

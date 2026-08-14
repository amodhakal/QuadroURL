"""Tests for the /dashboard endpoint."""


def test_dashboard(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert response.content_type == "text/html"
    assert "quadroPE" in response.get_data(as_text=True)

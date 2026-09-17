"""Missing bulk-import failure pins (#167).

Already covered elsewhere (test_users.py / test_graceful_failure.py):
missing file field, non-CSV extension, empty CSV, wrong content type.
These pin the remaining 400 paths in POST /users/bulk: missing columns,
row-level blanks, unreadable bytes, >5000 rows, >5MB payload.
"""

import io


def _post_csv(admin_client, payload: bytes, filename="users.csv"):
    return admin_client.post(
        "/users/bulk",
        data={"file": (io.BytesIO(payload), filename)},
        content_type="multipart/form-data",
    )


def test_bulk_import_missing_columns(admin_client):
    response = _post_csv(admin_client, b"username,created_at\nalice,2025-01-01\n")
    assert response.status_code == 400


def test_bulk_import_row_missing_email(admin_client):
    response = _post_csv(admin_client, b"username,email\nalice,\n")
    assert response.status_code == 400


def test_bulk_import_unreadable_csv(admin_client):
    response = _post_csv(admin_client, b"\xff\xfe\x00not-utf8")
    assert response.status_code == 400


def test_bulk_import_too_many_rows(admin_client):
    lines = ["username,email"] + [f"user{i},user{i}@example.com" for i in range(5001)]
    response = _post_csv(admin_client, ("\n".join(lines) + "\n").encode("utf-8"))
    assert response.status_code == 400


def test_bulk_import_too_large(admin_client):
    response = _post_csv(admin_client, b"x" * (5 * 1024 * 1024 + 1))
    assert response.status_code == 400

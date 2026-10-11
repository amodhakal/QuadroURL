import fnmatch
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

import pytest


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "client"


def _read(path):
    return path.read_text()


def _ignore_rules(path):
    return [
        line.strip()
        for line in _read(path).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _dockerignore_matches(path, rules):
    parts = PurePosixPath(path).parts
    ignored = False
    for index in range(1, len(parts) + 1):
        candidate = "/".join(parts[:index])
        for raw_rule in rules:
            negated = raw_rule.startswith("!")
            rule = raw_rule[1:] if negated else raw_rule
            rule = rule.rstrip("/")
            if fnmatch.fnmatch(candidate, rule):
                ignored = not negated
    return ignored


def _git_ignored(path):
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", "--", path],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


@pytest.mark.parametrize(
    ("ignore_path", "relative_path"),
    [
        (ROOT / ".dockerignore", ".env"),
        (ROOT / ".dockerignore", ".env.local"),
        (ROOT / ".dockerignore", "client/.env.production"),
        (ROOT / ".dockerignore", "terraform/main.tfstate"),
        (ROOT / ".dockerignore", "terraform/plan.tfplan"),
        (ROOT / ".dockerignore", "terraform/production.tfvars"),
        (ROOT / ".dockerignore", "terraform/.terraform/providers/example"),
        (ROOT / ".dockerignore", "node_modules/package"),
        (ROOT / ".dockerignore", "client/node_modules/package"),
        (ROOT / ".dockerignore", "dist/bundle.js"),
        (ROOT / ".dockerignore", "client/dist/bundle.js"),
        (ROOT / ".dockerignore", "coverage/lcov.info"),
        (ROOT / ".dockerignore", "client/test-results/.last-run.json"),
        (CLIENT / ".dockerignore", ".env"),
        (CLIENT / ".dockerignore", ".env.local"),
        (CLIENT / ".dockerignore", ".env.production"),
        (CLIENT / ".dockerignore", "terraform/main.tfstate"),
        (CLIENT / ".dockerignore", "terraform/plan.tfplan"),
        (CLIENT / ".dockerignore", "terraform/production.tfvars"),
        (CLIENT / ".dockerignore", "terraform/.terraform/providers/example"),
        (CLIENT / ".dockerignore", "node_modules/package"),
        (CLIENT / ".dockerignore", "dist/bundle.js"),
        (CLIENT / ".dockerignore", "coverage/lcov.info"),
        (CLIENT / ".dockerignore", "test-results/.last-run.json"),
        (CLIENT / ".dockerignore", "playwright-report/index.html"),
    ],
)
def test_dockerignores_exclude_secrets_and_local_artifacts(ignore_path, relative_path):
    assert _dockerignore_matches(relative_path, _ignore_rules(ignore_path))


@pytest.mark.parametrize("ignore_path", [ROOT / ".dockerignore", CLIENT / ".dockerignore"])
def test_dockerignores_allow_env_example(ignore_path):
    assert not _dockerignore_matches(".env.example", _ignore_rules(ignore_path))


@pytest.mark.parametrize(
    "relative_path",
    [
        ".env",
        ".env.local",
        "client/.env.production",
        "terraform/main.tfstate",
        "terraform/plan.tfplan",
        "terraform/production.tfvars",
        "terraform/.terraform/providers/example",
        "node_modules/package",
        "client/node_modules/package",
        "dist/bundle.js",
        "client/dist/bundle.js",
        "coverage/lcov.info",
        "client/test-results/.last-run.json",
    ],
)
def test_gitignore_excludes_secrets_and_local_artifacts(relative_path):
    assert _git_ignored(relative_path)


@pytest.mark.parametrize("relative_path", [".env.example", "client/.env.example"])
def test_gitignore_allows_env_example(relative_path):
    assert not _git_ignored(relative_path)


def test_root_dockerfile_uses_runtime_allowlist():
    copy_lines = [
        line.strip()
        for line in _read(ROOT / "Dockerfile").splitlines()
        if line.strip().upper().startswith("COPY ")
    ]
    assert not any(line.startswith("COPY .") for line in copy_lines)
    for required in (
        "COPY app ./app",
        "COPY shared ./shared",
        "COPY migrations ./migrations",
        "COPY run.py gunicorn.conf.py migrations_runner.py ./",
    ):
        assert required in copy_lines
    for forbidden in (".env", "terraform", "client", "docker-compose", "tests", "scripts"):
        assert not any(forbidden in line for line in copy_lines)


def test_client_dockerfile_and_package_pin_the_same_pnpm_version():
    dockerfile = _read(CLIENT / "Dockerfile")
    match = re.search(r"corepack prepare pnpm@(\d+\.\d+\.\d+)", dockerfile)
    assert match is not None
    version = match.group(1)
    assert version.startswith("10.")
    package = json.loads(_read(CLIENT / "package.json"))
    assert package["packageManager"] == f"pnpm@{version}"
    assert "pnpm@latest" not in dockerfile


def test_env_example_is_compose_ready_and_has_no_unused_database_url():
    values = {}
    for line in _read(ROOT / ".env.example").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value

    assert values["GF_ADMIN_USER"] == "admin"
    # #298: .env.example must not ship default credentials; values are
    # supplied per-deployment (CI overrides them for `compose config`).
    assert values["GF_ADMIN_PASSWORD"] == ""
    assert values["DATABASE_PASSWORD"] == ""
    assert values["DISCORD_WEBHOOK_URL"] == ""
    assert values["APP_URL"] == "http://127.0.0.1:8000"
    assert "DATABASE_URL" not in values
    assert "RESTOFURLHERE" not in _read(ROOT / ".env.example")


def test_compose_binds_requested_local_service_ports_to_loopback():
    compose = _read(ROOT / "docker-compose.yml")
    for published_port in (
        '"127.0.0.1:${DATABASE_PORT}:5432"',
        '"127.0.0.1:6379:6379"',
        '"127.0.0.1:9090:9090"',
        '"127.0.0.1:3001:3000"',
        '"127.0.0.1:3100:3100"',
    ):
        assert published_port in compose

    for unbound_port in (
        '"${DATABASE_PORT}:5432"',
        '"6379:6379"',
        '"9090:9090"',
        '"3001:3000"',
        '"3100:3100"',
    ):
        assert unbound_port not in compose

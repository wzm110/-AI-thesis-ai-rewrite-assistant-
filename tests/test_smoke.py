"""pytest 冒烟：在项目根目录执行 `pytest`。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from server import app

    with TestClient(app) as c:
        yield c


def test_get_root(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "论文" in r.text


def test_static_app_js(client: TestClient) -> None:
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "upload-docx" in r.text


def test_diagnostics(client: TestClient) -> None:
    r = client.get("/api/diagnostics")
    assert r.status_code == 200
    data = r.json()
    assert "thesis_exists" in data
    assert "tasks_total" in data

"""e2e tests exercise a running `docker compose up` stack over real HTTP -- they are skipped
entirely (not failed) if that stack isn't reachable, since CI/dev machines may not have it up."""
from __future__ import annotations

import httpx
import pytest

BASE_URL = "http://localhost:8080"


@pytest.fixture(scope="session")
def base_url() -> str:
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=2.0)
        r.raise_for_status()
    except Exception:
        pytest.skip(f"no running stack reachable at {BASE_URL} (run `docker compose up -d` first)")
    return BASE_URL


@pytest.fixture()
def client(base_url: str):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        yield c

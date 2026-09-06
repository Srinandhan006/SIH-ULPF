from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from uli.config import Settings, reset_settings_cache


@pytest.fixture()
def tmp_settings(tmp_path: Path) -> Settings:
    reset_settings_cache()
    s = Settings(data_dir=tmp_path / "data", parsers_dir=Path("parsers"), schemas_dir=Path("schemas"),
                 queue_backend="memory", ml_enabled=False, drift_window_events=5, drift_window_seconds=2,
                 unknown_suggest_min_events=3)
    return s


@pytest.fixture()
def stack(tmp_settings: Settings):
    from uli.bootstrap import build_stack

    st = build_stack(tmp_settings)
    yield st
    st.raw_store.close()


@pytest.fixture()
def api_client(tmp_settings: Settings):
    from fastapi.testclient import TestClient
    from uli.api.app import create_app
    from uli.bootstrap import build_stack

    st = build_stack(tmp_settings)
    app = create_app(st)
    with TestClient(app) as c:
        yield c
    st.raw_store.close()

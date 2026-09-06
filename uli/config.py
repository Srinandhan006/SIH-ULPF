"""Configuration. Everything is environment-driven (prefix ULI_); nothing is hard-coded."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ULI_", env_file=".env", extra="ignore")

    # --- deployment mode ---------------------------------------------------
    mode: str = Field("local", description="local | distributed")
    data_dir: Path = Field(Path("./data"))
    parsers_dir: Path = Field(Path("./parsers"))
    schemas_dir: Path = Field(Path("./schemas"))
    default_tenant: str = "default"

    # --- storage -----------------------------------------------------------
    db_url: str = Field("", description="SQLAlchemy URL; default sqlite under data_dir")
    raw_segment_max_bytes: int = 64 * 1024 * 1024
    raw_compress_on_rotate: bool = True
    embed_raw: bool = Field(False, description="Inline raw_data in OCSF body")

    # --- queue -------------------------------------------------------------
    queue_backend: str = Field("auto", description="auto | memory | redis")
    redis_url: str = "redis://localhost:6379/0"
    stream_name: str = "uli:raw"
    consumer_group: str = "uli-workers"
    stream_maxlen: int = 1_000_000

    # --- parsing -----------------------------------------------------------
    max_line_bytes: int = 64 * 1024
    regex_timeout_ms: int = 50
    tau_known: float = 0.6
    similarity_threshold: float = 0.8
    drain_sim_th: float = 0.4
    drain_depth: int = 4
    drain_max_clusters: int = 5000
    parser_inbox_poll_s: float = 5.0
    unknown_suggest_min_events: int = 20

    # --- drift -------------------------------------------------------------
    drift_window_events: int = 200
    drift_window_seconds: float = 60.0
    drift_js_threshold: float = 0.25
    drift_consecutive_windows: int = 2
    drift_confidence_drop: float = 0.15
    drift_anomaly_rate: float = 0.01
    resource_drift_interval_s: float = 30.0
    desired_state_path: Path = Field(Path("./deployment/desired-state.yaml"))
    correlation_window_s: float = 900.0

    # --- ML sidecar --------------------------------------------------------
    ml_enabled: bool = False
    ml_url: str = "http://localhost:8090"
    ml_timeout_ms: int = 20
    ml_circuit_failures: int = 5
    ml_circuit_reset_s: float = 30.0
    ml_models_dir: Path = Field(Path("./models"))

    # --- API / security ----------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    auth: str = Field("none", description="none | static")
    api_keys: str = Field("", description='JSON {"key": "tenant"} or "key:tenant,key2:tenant2"')
    bundle_pubkey_path: Path | None = None
    bundle_require_signature: bool = False
    max_ingest_body_bytes: int = 8 * 1024 * 1024

    # --- observability -----------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("data_dir", "parsers_dir", "schemas_dir", "ml_models_dir", mode="after")
    @classmethod
    def _abs(cls, v: Path) -> Path:
        return v.expanduser().resolve()

    @property
    def effective_db_url(self) -> str:
        if self.db_url:
            return self.db_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_dir / 'uli.db'}"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def drain_dir(self) -> Path:
        return self.data_dir / "drain"

    def api_key_map(self) -> dict[str, str]:
        s = self.api_keys.strip()
        if not s:
            return {}
        if s.startswith("{"):
            return {str(k): str(v) for k, v in json.loads(s).items()}
        out: dict[str, str] = {}
        for pair in s.split(","):
            if ":" in pair:
                k, t = pair.split(":", 1)
                out[k.strip()] = t.strip()
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()

"""Tenant auth abstraction (docs/architecture.md §8). Static-key mode maps API keys to tenants via
config; `none` mode (default for the local demo) uses ULI_DEFAULT_TENANT for everything. Either
way, tenant_id always comes from the authenticated identity, never from the request body."""
from __future__ import annotations

from fastapi import Header, HTTPException, Request

from uli.config import Settings


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def tenant_id(request: Request, x_api_key: str | None = Header(default=None)) -> str:
    settings: Settings = request.app.state.settings
    if settings.auth == "none":
        return settings.default_tenant
    keymap = settings.api_key_map()
    if not x_api_key or x_api_key not in keymap:
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")
    return keymap[x_api_key]

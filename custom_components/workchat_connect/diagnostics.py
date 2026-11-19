"""企微通集成诊断支持."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AES_KEY,
    CONF_SECRET,
    CONF_TOKEN,
    DOMAIN,
)

# 配置项中需要脱敏的敏感字段
TO_REDACT = {CONF_SECRET, CONF_AES_KEY, CONF_TOKEN}

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """返回配置项级诊断信息（含脱敏后的配置与协调器运行状态）."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    data: dict[str, Any] = {
        "config_entry": async_redact_data(dict(entry.data), TO_REDACT),
    }
    if coordinator is not None:
        data["coordinator"] = coordinator.get_diagnostic_info()
    return data
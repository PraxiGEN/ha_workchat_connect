"""企微通集成入口."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import get_url

# 使用 TYPE_CHECKING 避免循环导入
if TYPE_CHECKING:
    from .coordinator import WorkChatCoordinator

from .api import WorkChatApi
from .const import (
    CONF_AGENT_ID,
    CONF_CORP_ID,
    CONF_EXTERNAL_URL,
    CONF_PROXY,
    CONF_SECRET,
    CONF_AES_KEY,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import WorkChatCoordinator
from .encrypt_helper import EncryptHelper
from .views import WorkChatCallbackView

_LOGGER = logging.getLogger(__name__)

# 兼容 Python < 3.12 的别名写法
WorkChatConfigEntry = ConfigEntry["WorkChatCoordinator"]

async def async_setup_entry(hass: HomeAssistant, entry: WorkChatConfigEntry) -> bool:
    """设置集成入口."""

    # 1. 初始化底层 API 客户端
    api = WorkChatApi(
        session=async_get_clientsession(hass),
        corp_id=entry.data[CONF_CORP_ID],
        secret=entry.data[CONF_SECRET],
        agent_id=entry.data[CONF_AGENT_ID],
        proxy=entry.data.get(CONF_PROXY)
    )

    # 2. 初始化协调器
    coordinator = WorkChatCoordinator(hass, api, entry)
    
    # 3. 初始化加解密助手 (必须使用 CorpID)
    coordinator.encryptor = EncryptHelper(
        entry.data[CONF_AES_KEY], 
        entry.data[CONF_CORP_ID]
    )
    # 标准化外部 URL
    coordinator.external_url = entry.data.get(CONF_EXTERNAL_URL) or get_url(hass)

    # 4. 存储运行时数据
    entry.runtime_data = coordinator

    # 5. 注册 Webhook 回调视图
    hass.http.register_view(WorkChatCallbackView(coordinator))

    # --- 6. 注册动作 (Services) ---

    async def handle_notify(call: ServiceCall):
        await coordinator.async_send_message(**call.data)

    async def handle_upload_media(call: ServiceCall):
        media_id = await coordinator.async_upload_media_file(
            media_type=call.data["type"],
            file_path=call.data["file_path"],
            file_name=call.data.get("file_name")
        )
        return {"media_id": media_id}

    hass.services.async_register(DOMAIN, "notify", handle_notify)
    hass.services.async_register(
        DOMAIN, "upload_media", handle_upload_media, supports_response=SupportsResponse.ONLY
    )

    # 7. 转发到各平台
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: WorkChatConfigEntry) -> bool:
    """卸载集成."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.services.async_remove(DOMAIN, "notify")
        hass.services.async_remove(DOMAIN, "upload_media")
    return unload_ok
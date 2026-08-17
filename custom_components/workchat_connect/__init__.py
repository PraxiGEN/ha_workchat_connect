"""企微通集成入口."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.loader import async_get_integration
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import get_url

from .api import WorkChatApi
from .const import (
    CONF_AGENT_ID,
    CONF_CORP_ID,
    CONF_EXTERNAL_URL,
    CONF_PROXY,
    CONF_SECRET,
    CONF_AES_KEY,
    DOMAIN,
    LOGGER,
    PLATFORMS,
)
from .coordinator import WorkChatCoordinator
from .encrypt_helper import EncryptHelper
from .views import WorkChatCallbackView, WorkChatDiagnosticView
from .services import register_global_services

type WorkChatConfigEntry = ConfigEntry[WorkChatCoordinator]

# 全局服务/视图是否已初始化的标记
_INIT_FLAG = f"{DOMAIN}_initialized"

async def async_setup_entry(hass: HomeAssistant, entry: WorkChatConfigEntry) -> bool:
    """设置集成入口."""

    # 初始化底层 API 客户端
    api = WorkChatApi(
        session=async_get_clientsession(hass),
        corp_id=entry.data[CONF_CORP_ID],
        secret=entry.data[CONF_SECRET],
        agent_id=entry.data[CONF_AGENT_ID],
        proxy=entry.data.get(CONF_PROXY),
    )

    integration = await async_get_integration(hass, DOMAIN)
    version = str(integration.version) if integration.version else "1.0.0"

    # 初始化协调器
    coordinator = WorkChatCoordinator(hass, api, entry, version)

    # 初始化加解密助手 (必须使用 CorpID)
    coordinator.encryptor = EncryptHelper(
        entry.data[CONF_AES_KEY],
        entry.data[CONF_CORP_ID],
    )
    # 标准化外部 URL
    coordinator.external_url = entry.data.get(CONF_EXTERNAL_URL) or get_url(hass)

    # 多配置项注册表：entry_id -> coordinator
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    # 供各平台（notify/sensor）通过 entry.runtime_data 获取协调器
    entry.runtime_data = coordinator

    # 全局视图与服务仅注册一次（多个配置项共享，按 token / config_entry_id 分发）
    if not hass.data.get(_INIT_FLAG):
        hass.http.register_view(WorkChatCallbackView(hass))
        hass.http.register_view(WorkChatDiagnosticView(hass))
        register_global_services(hass)
        hass.data[_INIT_FLAG] = True

    # 转发到各平台
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: WorkChatConfigEntry) -> bool:
    """卸载集成."""

    coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok and coordinator is not None:
        # 触发语音文件物理清理（按设计在卸载时进行）
        await coordinator.async_remove_media_data()

        # 仅当没有任何剩余配置项时，才移除全局服务（视图保留，由 token 分发自动失效）
        if not hass.data.get(DOMAIN):
            if hass.services.has_service(DOMAIN, "notify"):
                hass.services.async_remove(DOMAIN, "notify")
            if hass.services.has_service(DOMAIN, "upload_media"):
                hass.services.async_remove(DOMAIN, "upload_media")
            hass.data.pop(_INIT_FLAG, None)
        LOGGER.info("企微通集成 %s 已卸载完成", entry.title)

    return unload_ok

"""企微通全局服务实现（通知与媒体上传，支持多配置项路由）."""
from __future__ import annotations

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse

from .const import CONF_ENTRY_ID, DOMAIN, LOGGER
from .coordinator import WorkChatCoordinator

def _select_coordinator(hass: HomeAssistant, call: ServiceCall) -> WorkChatCoordinator | None:
    """根据服务调用选择目标协调器（支持多配置项）。"""
    entry_id = call.data.get(CONF_ENTRY_ID)
    registry = hass.data.get(DOMAIN, {})
    if entry_id and entry_id in registry:
        return registry[entry_id]
    # 未指定时，取注册表中的第一个（单配置项场景即唯一项）
    return next(iter(registry.values()), None)

def register_global_services(hass: HomeAssistant) -> None:
    """注册一次全局服务（多配置项共享，按 config_entry_id 路由）。"""

    async def handle_notify(call: ServiceCall):
        coordinator = _select_coordinator(hass, call)
        if coordinator is None:
            LOGGER.error("企微通: 未找到可用的配置项，无法发送消息")
            return
        await coordinator.async_send_message(**call.data)

    async def handle_upload_media(call: ServiceCall):
        coordinator = _select_coordinator(hass, call)
        if coordinator is None:
            LOGGER.error("企微通: 未找到可用的配置项，无法上传媒体")
            return
        media_id = await coordinator.async_upload_media_file(
            media_type=call.data["type"],
            file_path=call.data["file_path"],
            file_name=call.data.get("file_name"),
        )
        return {"media_id": media_id}

    hass.services.async_register(DOMAIN, "notify", handle_notify)
    hass.services.async_register(
        DOMAIN, "upload_media", handle_upload_media, supports_response=SupportsResponse.ONLY
    )

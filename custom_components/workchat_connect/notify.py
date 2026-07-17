"""企微通通知实体实现."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.notify import (
    ATTR_DATA,
    NotifyEntity,
    NotifyEntityDescription, # 引入通知描述符类
    NotifyEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .__init__ import WorkChatConfigEntry
from .const import DOMAIN, LOGGER
from .coordinator import WorkChatCoordinator

# --- 定义通知描述符（完全模仿传感器模式） ---
@dataclass(frozen=True, kw_only=True)
class WorkChatNotifyEntityDescription(NotifyEntityDescription):
    """描述企微通通知实体的扩展类."""

# 定义通知实体列表（虽然通常只有一个，但模式是一致的）
NOTIFY_ENTITY_DESCRIPTIONS: tuple[WorkChatNotifyEntityDescription, ...] = (
    WorkChatNotifyEntityDescription(
        key="workchat_notifier",
        name="WorkChat Notifier",
        translation_key="workchat_notifier",
    ),
)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: WorkChatConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """设置企微通通知实体入口."""
    coordinator = entry.runtime_data
    
    # 按照描述符循环生成实体
    async_add_entities(
        [WorkChatNotifyEntity(coordinator, description) 
         for description in NOTIFY_ENTITY_DESCRIPTIONS]
    )

class WorkChatNotifyEntity(NotifyEntity):
    """企微通通知实体类."""

    # 声明使用描述符
    entity_description: WorkChatNotifyEntityDescription

    _attr_has_entity_name = True
    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(
        self, 
        coordinator: WorkChatCoordinator, 
        description: WorkChatNotifyEntityDescription
    ) -> None:
        """初始化."""
        super().__init__()
        self.coordinator = coordinator
        self.entity_description = description
        
        # 基础属性绑定
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_device_info = coordinator.device_info
        
        # 翻译键绑定
        self._attr_translation_key = description.translation_key

    async def async_send_message(self, message: str, title: str | None = None, **kwargs: Any) -> None:
        """发送通知消息."""
        data = kwargs.get(ATTR_DATA) or {}
        
        params = {
            "message": message,
            "title": title,
            **data
        }
        
        if "msg_type" not in params:
            params["msg_type"] = "text"

        try:
            success = await self.coordinator.async_send_message(**params)
            if not success:
                LOGGER.error("发送失败，请检查企微配置或网络")
        except Exception as err:
            LOGGER.error("通知实体发送异常: %s", err)
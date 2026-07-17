"""企微通传感器平台实现 - 全描述符驱动优化版."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .__init__ import WorkChatConfigEntry
from .const import ( 
    DOMAIN, LOGGER,
    EVENT_MESSAGE_RECEIVED, EVENT_MEDIA_UPLOADED, 
    TYPE_INFO, TYPE_MSG, TYPE_UPLOAD, 
    MSG_TYPE_IMAGE, MSG_TYPE_TEXT, MSG_TYPE_VOICE
)
from .coordinator import WorkChatCoordinator

@dataclass(frozen=True, kw_only=True)
class WorkChatSensorEntityDescription(SensorEntityDescription):
    """描述企微通传感器的扩展类."""
    event_type: str | None = None  # 仅消息类使用
    sensor_type: str = TYPE_MSG     # 传感器逻辑类型

# --- 所有实体的统一描述符定义 ---
SENSOR_DESCRIPTIONS: tuple[WorkChatSensorEntityDescription, ...] = (
    # 消息类传感器
    WorkChatSensorEntityDescription(
        key=MSG_TYPE_TEXT,
        name="Text Message",
        translation_key="text_message",
        event_type=MSG_TYPE_TEXT,
        sensor_type=TYPE_MSG,
        icon="mdi:chat-processing-outline",
    ),
    WorkChatSensorEntityDescription(
        key=MSG_TYPE_VOICE,
        name="Voice Message",
        translation_key="voice_message",
        event_type=MSG_TYPE_VOICE,
        sensor_type=TYPE_MSG,
        icon="mdi:microphone",
    ),
    WorkChatSensorEntityDescription(
        key=MSG_TYPE_IMAGE,
        name="Image Message",
        translation_key="image_message",
        event_type=MSG_TYPE_IMAGE,
        sensor_type=TYPE_MSG,
        icon="mdi:image-filter-hdr",
    ),
    WorkChatSensorEntityDescription(
        key="location",
        name="Location Message",
        translation_key="location_message",
        event_type="location",
        sensor_type=TYPE_MSG,
        icon="mdi:map-marker-radius",
    ),
    WorkChatSensorEntityDescription(
        key="menu_click",
        name="Menu Click",
        translation_key="menu_click",
        event_type="menu_click",
        sensor_type=TYPE_MSG,
        icon="mdi:cursor-default-click",
    ),
    # 诊断类传感器
    WorkChatSensorEntityDescription(
        key="callback_info",
        name="Callback Info",
        translation_key="callback_info",
        sensor_type=TYPE_INFO,
        icon="mdi:api",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # 上传追踪传感器
    WorkChatSensorEntityDescription(
        key="last_media_upload",
        name="Media Upload",
        translation_key="media_upload",
        sensor_type=TYPE_UPLOAD,
        icon="mdi:cloud-upload-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: WorkChatConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """根据统一描述符设置所有传感器实体."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    
    # 映射逻辑类型到具体的类
    for description in SENSOR_DESCRIPTIONS:
        if description.sensor_type == TYPE_MSG:
            entities.append(WorkChatMessageSensor(coordinator, description))
        elif description.sensor_type == TYPE_INFO:
            entities.append(WorkChatCallbackInfoSensor(coordinator, description))
        elif description.sensor_type == TYPE_UPLOAD:
            entities.append(WorkChatMediaUploadSensor(coordinator, description))
    
    async_add_entities(entities)

class WorkChatBaseEntity(CoordinatorEntity[WorkChatCoordinator], SensorEntity):
    """基类：统一处理基础属性."""
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: WorkChatCoordinator, description: WorkChatSensorEntityDescription):
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = coordinator.entry
        self.entity_description = description
        self._attr_translation_key = description.translation_key
        self._attr_unique_id = f"{self.entry.entry_id}_{description.key}"
        self._attr_device_info = coordinator.device_info

class WorkChatMessageSensor(WorkChatBaseEntity):
    """消息类传感器：监听事件总线."""
    entity_description: WorkChatSensorEntityDescription

    def __init__(self, coordinator: WorkChatCoordinator, description: WorkChatSensorEntityDescription):
        super().__init__(coordinator, description)
        self._msg_data: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_MESSAGE_RECEIVED, self._handle_message))

    @callback
    def _handle_message(self, event):
        data = event.data
        if data.get("type") == self.entity_description.event_type:
            self._msg_data = data
            if ts := data.get("timestamp"):
                try:
                    dt_obj = dt_util.utc_from_timestamp(float(ts))
                    self._msg_data["formatted_time"] = dt_util.as_local(dt_obj).isoformat()
                except (ValueError, TypeError):
                    self._msg_data["formatted_time"] = str(ts)
            self.async_write_ha_state()

    @property
    def native_value(self) -> str | None:
        if not self._msg_data: return None
        key = self.entity_description.key
        if key == MSG_TYPE_TEXT: return self._msg_data.get("content")
        if key == MSG_TYPE_VOICE: return "voice_message"
        if key == MSG_TYPE_IMAGE: return "image_message"
        if key == "menu_click": return "menu_message"
        if key == "location": return self._msg_data.get("label") or self._msg_data.get("lat")
        val = self._msg_data.get("media_id")
        if val and len(val) > 16: return f"{val[:6]}...{val[-6:]}"
        return val

    @property
    def entity_picture(self) -> str | None:
        if self.entity_description.key == MSG_TYPE_IMAGE: return self._msg_data.get("pic_url")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._msg_data: return {}
        attrs = {
            "from_user": self._msg_data.get("user"),
            "receive_time": self._msg_data.get("formatted_time"),
            "agent_id": self._msg_data.get("agent_id"),
        }
        if self.entity_description.key == MSG_TYPE_VOICE:
            media_id = self._msg_data.get("media_id")
            attrs.update({
                "media_id": media_id,
                "msg_id": self._msg_data.get("msg_id"),
                "original_format": self._msg_data.get("format"), # 保留原始格式记录（如 amr）
                "format": "mp3",                                 # 当前实际格式
                "local_path": f"/config/media/workchat/{media_id}.mp3"  # 指向转码后的路径
            })
        if self.entity_description.key == MSG_TYPE_IMAGE:
            attrs.update({"media_id": self._msg_data.get("media_id"), "pic_url": self._msg_data.get("pic_url")})
        if self.entity_description.key == "location":
            attrs.update({"latitude": self._msg_data.get("lat"), "longitude": self._msg_data.get("lon"), "address": self._msg_data.get("label")})
        attrs["raw_info"] = self._msg_data
        return attrs

class WorkChatCallbackInfoSensor(WorkChatBaseEntity):
    """诊断类传感器：展示集成连接状态."""

    @property
    def native_value(self) -> str:
        if not self.coordinator.data: return "disconnected"
        return "connected" if self.coordinator.data.get("token_ready") else "disconnected"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """展示所有诊断相关的元数据（修复了之前的逻辑重叠错误）."""
        attrs = {
            "webhook_url": self.coordinator.callback_url, 
            "corpid": self.entry.data.get("corp_id"),
            "last_msg_received": self.coordinator.last_msg_time,
        }
        
        # 安全获取协调器中的诊断数据
        if self.coordinator.data:
            attrs.update({
                "agent_id": self.coordinator.data.get("agent_id"),
                "token_expires_at": self.coordinator.data.get("token_expires_at"),
            })
        
        # 获取 Token 过期时间的原始计算值
        expire_ts = getattr(self.coordinator.api, "_token_expire", 0)
        if expire_ts > 0:
            attrs["token_expires_at_raw"] = dt_util.as_local(dt_util.utc_from_timestamp(expire_ts)).isoformat()
            
        return attrs

class WorkChatMediaUploadSensor(WorkChatBaseEntity):
    """上传追踪类传感器：监听媒体上传事件."""

    def __init__(self, coordinator: WorkChatCoordinator, description: WorkChatSensorEntityDescription):
        super().__init__(coordinator, description)
        self._upload_data: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_MEDIA_UPLOADED, self._handle_upload))

    @callback
    def _handle_upload(self, event):
        self._upload_data = event.data
        self.async_write_ha_state()

    @property
    def native_value(self) -> str | None:
        return "ready" if self._upload_data.get("media_id") else "waiting_to_upload"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._upload_data
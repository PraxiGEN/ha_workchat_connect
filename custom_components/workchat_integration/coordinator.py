"""企微通数据协调器."""
from __future__ import annotations

import hashlib
import logging
import time
import os
from aiohttp import FormData
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import WorkChatApi
from .const import (
    API_SEND_MESSAGE,
    API_UPLOAD_MEDIA,
    CONF_AGENT_ID,
    CONF_RECEIVE_USER,
    CONF_TOKEN,
    DOMAIN,
    EVENT_MEDIA_UPLOADED,
    EVENT_MESSAGE_RECEIVED,
)

_LOGGER = logging.getLogger(__name__)

class WorkChatCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """集成协调器，处理服务调用、状态维护与数据同步."""

    def __init__(self, hass: HomeAssistant, api: WorkChatApi, entry) -> None:
        """初始化协调器."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            # 每 30 分钟轮询一次诊断信息（如 Token 状态）
            update_interval=timedelta(minutes=30),
        )
        self.api = api
        self.entry = entry
        
        # --- 状态持久化变量 ---
        self.last_msg_time: str | None = None
        self.last_event: dict[str, Any] = {}
        self.last_upload: dict[str, Any] = {}
        
        # 由 __init__.py 注入的助手
        self.encryptor = None 
        self.external_url = ""

        # --- 设备信息定义 ---
        agent_id = entry.data[CONF_AGENT_ID]
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"WorkChat App ({agent_id})",
            manufacturer="Tencent",
            model="WorkChat Integration",
            configuration_url=f"https://work.weixin.qq.com/wework_admin/frame#apps/modApiApp/{agent_id}",
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """定期从 API 获取并构建传感器数据字典."""
        try:
            # 获取/刷新 Token 并获取过期时间
            token = await self.api.get_access_token()
            
            expires_at = None
            if self.api._token_expire > 0:
                expires_at = dt_util.as_local(
                    dt_util.utc_from_timestamp(self.api._token_expire)
                ).isoformat()

            # 构建符合 sensor.py 要求的完整数据包
            return {
                "token_ready": token is not None,
                "token_expires_at": expires_at,
                "agent_id": self.api.agent_id,
                "corp_id": self.api.corp_id,
                "external_url": self.external_url,
                "last_msg_time": self.last_msg_time,
                "last_event": self.last_event,
                "last_upload": self.last_upload,
            }
        except Exception as err:
            # 如果是 API 错误，抛出 UpdateFailed 让实体显示为“不可用”
            raise UpdateFailed(f"获取企微 API 数据失败: {err}") from err

    async def async_send_message(self, **kwargs: Any) -> bool:
        """发送消息入口 (完全支持 services.yaml 定义的所有字段)."""
        # 补充默认接收人
        if not kwargs.get("touser"):
            kwargs["touser"] = self.entry.data.get(CONF_RECEIVE_USER, "@all")
            
        # 构造 Payload
        payload = self.api.build_message_payload(**kwargs)
        
        # 使用常量 API_SEND_MESSAGE
        res = await self.api.post_api(API_SEND_MESSAGE, json_data=payload)
        
        if res.get("errcode") == 0:
            return True
        
        _LOGGER.error("消息发送失败: %s (代码: %s)", res.get("errmsg"), res.get("errcode"))
        return False

    async def async_upload_media_file(
        self, media_type: str, file_path: str, file_name: str | None = None
    ) -> str | None:
        """上传媒体文件并更新状态."""

        # 在线程池中读取文件
        def _get_file():
            if not os.path.exists(file_path): return None
            with open(file_path, "rb") as f:
                return f.read()

        content = await self.hass.async_add_executor_job(_get_file)
        if not content:
            _LOGGER.error("文件上传失败：路径不存在 %s", file_path)
            return None

        # 准备表单
        form = FormData()
        form.add_field("media", content, filename=file_name or os.path.basename(file_path))
        
        # 使用常量 API_UPLOAD_MEDIA
        res = await self.api.post_api(API_UPLOAD_MEDIA, params={"type": media_type}, data=form)
        
        if res.get("errcode") == 0:
            media_id = str(res.get("media_id"))
            
            # 更新本地上传缓存
            self.last_upload = {
                "media_id": media_id,
                "file_path": file_path,
                "type": media_type,
                "upload_time": dt_util.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
            # 触发事件总线
            self.hass.bus.async_fire(EVENT_MEDIA_UPLOADED, self.last_upload)
            
            # 立即推送最新数据到传感器实体，无需等待 30 分钟轮询
            self.async_set_updated_data(await self._async_update_data())
            return media_id
        
        _LOGGER.error("媒体上传失败: %s", res.get("errmsg"))
        return None

    @property
    def callback_url(self) -> str:
        """构建完整的 Webhook 回调地址 (完全复刻原代码逻辑)."""
        # 从配置中获取 Token
        token = self.entry.data.get(CONF_TOKEN)
        # 确保 external_url 末尾没有多余的斜杠，然后拼接路径
        base_url = self.external_url.rstrip("/")
        return f"{base_url}/api/workchat_callback/{token}"

    def process_callback_data(self, event_data: dict[str, Any]):
        """处理 Webhook 回调接收到的数据并实时推送."""
        # 更新最后接收时间与事件内容
        self.last_msg_time = dt_util.now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_event = event_data
        
        # 1. 触发 HA 事件 (供自动化使用)
        self.hass.bus.async_fire(EVENT_MESSAGE_RECEIVED, event_data)
        
        # 2. 实时推送数据更新到传感器 (不经过 API 请求，直接合并当前状态)
        # 这样文本传感器、图片传感器会立刻显示最新内容
        self.hass.async_create_task(self._async_push_update())

    async def _async_push_update(self):
        """内部辅助：强制刷新传感器状态显示."""
        new_data = await self._async_update_data()
        self.async_set_updated_data(new_data)

    def generate_passive_response(self, content: str) -> str:
        """生成符合企微安全规范的加密响应 XML."""
        if not self.encryptor:
            _LOGGER.warning("加密助手未初始化，无法生成被动回复")
            return ""
        
        timestamp = str(int(time.time()))
        nonce = str(int(time.time()) % 1000000)
        encrypt = self.encryptor.encrypt(content)
        
        # 签名校验 (Token 来自 const.py 对应的 entry.data)
        token = self.entry.data[CONF_TOKEN]
        tmp_list = sorted([token, timestamp, nonce, encrypt])
        signature = hashlib.sha1("".join(tmp_list).encode()).hexdigest()
        
        return f"""<xml>
            <Encrypt><![CDATA[{encrypt}]]></Encrypt>
            <MsgSignature><![CDATA[{signature}]]></MsgSignature>
            <TimeStamp>{timestamp}</TimeStamp>
            <Nonce><![CDATA[{nonce}]]></Nonce>
        </xml>"""
"""企微通数据协调器."""
from __future__ import annotations

import hashlib
import time
import os
import subprocess
import shutil
from aiohttp import FormData
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import WorkChatApi
from .const import (
    LOGGER,
    API_SEND_MESSAGE,
    API_UPLOAD_MEDIA,
    CONF_RECEIVE_USER,
    CONF_TOKEN,
    DOMAIN,
    EVENT_MEDIA_UPLOADED,
    EVENT_MESSAGE_RECEIVED,
    DEFAULT_VOICE_RETENTION_DAYS
)

class WorkChatCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """集成协调器，处理服务调用、状态维护与数据同步."""

    def __init__(self, hass: HomeAssistant, api: WorkChatApi, entry, version: str) -> None:
        """初始化协调器."""
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            # 每 30 分钟轮询一次诊断信息（如 Token 状态）
            update_interval=timedelta(minutes=30),
        )
        self.api = api
        self.entry = entry
        self.version = version
        
        # --- 状态持久化变量 ---
        self.last_msg_time: str | None = None
        self.last_event: dict[str, Any] = {}
        self.last_upload: dict[str, Any] = {}
        
        # 由 __init__.py 注入的助手
        self.encryptor = None 
        self.external_url = ""

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
        
        LOGGER.error("消息发送失败: %s (代码: %s)", res.get("errmsg"), res.get("errcode"))
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
            LOGGER.error("文件上传失败：路径不存在 %s", file_path)
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
        
        LOGGER.error("媒体上传失败: %s", res.get("errmsg"))
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
        
        # 触发 HA 事件 (供自动化使用)
        self.hass.bus.async_fire(EVENT_MESSAGE_RECEIVED, event_data)
        
        if event_data.get("type") == "voice":
            media_id = event_data.get("media_id")
            # 这里的 format 对应企微 XML 里的 Format 字段
            file_format = event_data.get("format", "amr")
            
            LOGGER.debug("检测到语音消息，准备启动下载任务: %s", media_id)
            # 使用 async_create_task 异步运行，不阻塞回调
            self.hass.async_create_task(self.async_save_voice_file(media_id, file_format))

        # 实时推送数据更新到传感器 (不经过 API 请求，直接合并当前状态)
        # 这样文本传感器、图片传感器会立刻显示最新内容
        self.hass.async_create_task(self._async_push_update())

    async def _async_push_update(self):
        """内部辅助：强制刷新传感器状态显示."""
        new_data = await self._async_update_data()
        self.async_set_updated_data(new_data)

    def generate_passive_response(self, content: str) -> str:
        """生成符合企微安全规范的加密响应 XML."""
        if not self.encryptor:
            LOGGER.warning("加密助手未初始化，无法生成被动回复")
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

    async def async_save_voice_file(self, media_id: str, file_format: str = "amr") -> str | None:
        """拉取、保存并自动转码语音文件到 HA 媒体库."""
        
        # 调用 API 下载原始数据
        content = await self.api.download_media(media_id)
        if not content:
            LOGGER.error("企微语音下载失败，无法保存文件: %s", media_id)
            return None

        # 定义路径
        # 物理根目录: /config/media/workchat
        target_dir = self.hass.config.path("media", "workchat")
        
        # 原始 AMR 路径和目标 MP3 路径
        amr_filename = f"{media_id}.amr"
        mp3_filename = f"{media_id}.mp3"
        
        amr_path = os.path.join(target_dir, amr_filename)
        mp3_path = os.path.join(target_dir, mp3_filename)

        def _sync_process_io():
            """在线程池中执行的同步 IO 与转码操作."""
            try:
                # 确保目录存在 (如果没有 media/workchat 会自动创建)
                if not os.path.exists(target_dir):
                    os.makedirs(target_dir, exist_ok=True)
                    LOGGER.info("已自动创建语音存储目录: %s", target_dir)

                # 先写入原始 AMR 文件
                with open(amr_path, "wb") as f:
                    f.write(content)

                # 调用 FFmpeg 进行转码 (Docker 版 HA 内置支持)
                # -y: 覆盖输出文件
                # -i: 输入 AMR
                # -acodec libmp3lame: 使用 MP3 编码器
                # -ar 16000: 采样率 16k (最适合 ASR 识别)
                # -ac 1: 单声道
                ffmpeg_cmd = [
                    "ffmpeg", "-y",
                    "-i", amr_path,
                    "-acodec", "libmp3lame",
                    "-ar", "16000",
                    "-ac", "1",
                    mp3_path
                ]

                # 检查 ffmpeg 是否可用
                if shutil.which("ffmpeg") is None:
                    LOGGER.error("系统中未找到 FFmpeg，无法进行语音转码")
                    return amr_path # 没转码成功则返回原始路径

                # 执行转码
                result = subprocess.run(
                    ffmpeg_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30
                )

                if result.returncode == 0:
                    LOGGER.info("语音转码成功: %s -> %s", amr_filename, mp3_filename)
                    # 转码成功后，删除原始 AMR 文件以节省空间
                    if os.path.exists(amr_path):
                        os.remove(amr_path)
                    final_path = mp3_path
                else:
                    LOGGER.error("FFmpeg 转码失败: %s", result.stderr)
                    final_path = amr_path # 失败则保留 AMR 供兜底使用

                # 执行自动清理
                self._cleanup_old_files(target_dir)
                
                return final_path

            except Exception as e:
                LOGGER.error("处理语音文件时发生磁盘 IO 错误: %s", e)
                return None

        try:
            # 提交到线程池执行，避免卡顿
            result_path = await self.hass.async_add_executor_job(_sync_process_io)
            if result_path:
                LOGGER.info("企微语音处理完成，最终路径: %s", result_path)
            return result_path

        except Exception as err:
            LOGGER.error("async_save_voice_file 异步执行崩溃: %s", err)
            return None

    def _cleanup_old_files(self, path: str):
        """扫描并删除过期文件 (内部 IO 方法)."""
        now = time.time()
        # 将天数转换为秒
        retention_seconds = DEFAULT_VOICE_RETENTION_DAYS * 86400

        try:
            # 检查目录是否存在，防止清理不存在的目录
            if not os.path.exists(path):
                return

            for filename in os.listdir(path):
                file_path = os.path.join(path, filename)
                
                # 只处理文件（跳过子目录）
                if os.path.isfile(file_path):
                    file_age = os.path.getmtime(file_path)
                    
                    # 如果文件创建/修改时间超过了保留期限 (默认 7 天)
                    if (now - file_age) > retention_seconds:
                        os.remove(file_path)
                        LOGGER.debug("已自动清理过期企微语音文件: %s", filename)
        except Exception as err:
            LOGGER.error("执行自动清理任务时出错: %s", err)

    async def async_remove_media_data(self):
        """卸载集成时，清理所有的语音文件及目录."""
        
        # 获取物理路径: /config/media/workchat
        target_dir = self.hass.config.path("media", "workchat")

        def _delete_dir():
            try:
                if os.path.exists(target_dir):
                    shutil.rmtree(target_dir)
                    LOGGER.info("企微集成已卸载：已成功删除语音存储目录 %s", target_dir)
            except Exception as err:
                LOGGER.error("删除语音目录时出错: %s", err)

        # 在线程池中执行 IO 删除操作
        await self.hass.async_add_executor_job(_delete_dir)

    @property
    def device_info(self) -> DeviceInfo:
        """定义设备信息属性，供所有实体引用."""

        agent_id = self.api.agent_id
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=f"WorkChat App ({agent_id})",
            manufacturer="Tencent",
            model="WorkChat API Platform",
            configuration_url=f"https://work.weixin.qq.com/wework_admin/frame#apps/modApiApp/{agent_id}",
            sw_version=str(self.version),
            entry_type=DeviceEntryType.SERVICE,
        )
"""处理企微回调的视图."""
from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .const import CONF_TOKEN

_LOGGER = logging.getLogger(__name__)

class WorkChatCallbackView(HomeAssistantView):
    """处理企微回调的视图，包含 URL 验证、消息接收和状态诊断页."""
    
    url = "/api/workchat_callback/{token}"
    name = "api:workchat_callback"
    requires_auth = False  # 企微服务器访问不需要 HA 登录

    def __init__(self, coordinator) -> None:
        """初始化视图."""
        self.coordinator = coordinator

    def _verify_signature(self, sig: str, ts: str, nonce: str, data: str) -> bool:
        """校验企微签名."""
        try:
            # 企微校验逻辑: token(配置), timestamp, nonce, msg_encrypt(或echostr) 字典序排序
            config_token = self.coordinator.entry.data[CONF_TOKEN]
            tmp = sorted([config_token, ts, nonce, data])
            hash_str = hashlib.sha1("".join(tmp).encode()).hexdigest()
            return hash_str == sig
        except Exception as e:
            _LOGGER.error("签名校验算法异常: %s", e)
            return False

    async def get(self, request: web.Request, token: str) -> web.Response:
        """处理 GET 请求：企微 URL 验证步或诊断页."""
        
        # 1. 路径 Token 基础校验 (防止非法访问诊断页)
        config_token = self.coordinator.entry.data[CONF_TOKEN]
        if token != config_token:
            _LOGGER.error("回调 URL Token 不匹配: 收到 %s, 预期 %s", token, config_token)
            return web.Response(status=403, text="Token Mismatch")

        q = request.query
        echostr = q.get("echostr")

        # --- 情况 A: 诊断页展示 (当用户直接访问 URL 时) ---
        if not echostr:
            status_data = {
                "has_token": await self.coordinator.api.get_access_token() is not None,
                "agent_id": self.coordinator.api.agent_id,
                "external_url": self.coordinator.external_url,
                "last_msg_time": self.coordinator.last_msg_time,
                "last_event": self.coordinator.last_event
            }
            return web.Response(text=self._get_status_html(status_data), content_type="text/html")

        # --- 情况 B: 企微后台 URL 验证逻辑 ---
        sig = q.get("msg_signature")
        ts = q.get("timestamp")
        nonce = q.get("nonce")

        if self._verify_signature(sig, ts, nonce, echostr):
            try:
                # 验证通过，解密 echostr 并返回解密后的原始随机字符串
                decrypted = await self.coordinator.hass.async_add_executor_job(
                    self.coordinator.encryptor.decrypt, echostr
                )
                _LOGGER.info("企微 URL 验证成功: %s", decrypted)
                return web.Response(text=decrypted)
            except Exception as e:
                _LOGGER.error("企微解密 echostr 失败 (请检查 EncodingAESKey): %s", e)
        else:
            _LOGGER.warning("企微签名验证不通过 (GET)")
        
        return web.Response(status=400, text="Verification Failed")

    async def post(self, request: web.Request, token: str) -> web.Response:
        """处理 POST 请求：接收企微推送的加密消息."""
        
        config_token = self.coordinator.entry.data[CONF_TOKEN]
        if token != config_token:
            return web.Response(status=403)

        try:
            # 1. 提取加密体
            body = await request.text()
            root = ET.fromstring(body)
            encrypt_msg = root.find('Encrypt').text
            
            # 2. 校验签名
            q = request.query
            if not self._verify_signature(q.get("msg_signature"), q.get("timestamp"), q.get("nonce"), encrypt_msg):
                _LOGGER.warning("企微 POST 签名验证不通过")
                return web.Response(status=401)

            # 3. 解密 XML
            decrypted_xml = await self.coordinator.hass.async_add_executor_job(
                self.coordinator.encryptor.decrypt, encrypt_msg
            )
            
            # 4. 解析 XML 为字典并交给协调器
            event_data = self._parse_xml(decrypted_xml)
            self.coordinator.process_callback_data(event_data)
            
            return web.Response(text="success")
            
        except Exception as err:
            _LOGGER.error("处理企微推送消息异常: %s", err)
            return web.Response(status=500)

    def _parse_xml(self, xml_str: str) -> dict[str, Any]:
        """将解密后的 XML 解析为字典."""
        root = ET.fromstring(xml_str)
        msg_type = root.find("MsgType").text
        
        data = {
            "user": root.find("FromUserName").text,
            "type": msg_type,
            "agent_id": root.find("AgentID").text if root.find("AgentID") is not None else "",
            "timestamp": root.find("CreateTime").text,
        }

        # 详细字段提取逻辑 (100% 复刻原功能)
        if msg_type == "text":
            data["content"] = root.find("Content").text
        elif msg_type == "image":
            data["media_id"] = root.find("MediaId").text
            data["pic_url"] = root.find("PicUrl").text
        elif msg_type == "location":
            data.update({
                "lat": root.find("Location_X").text,
                "lon": root.find("Location_Y").text,
                "label": root.find("Label").text if root.find("Label") is not None else ""
            })
        elif msg_type == "event":
            event_name = root.find("Event").text
            data["type"] = "menu_click" if event_name == "click" else "event"
            data["event"] = event_name
            if (ek := root.find("EventKey")) is not None:
                data["event_key"] = ek.text
        
        return data

    def _get_status_html(self, status_data: dict[str, Any]) -> str:
        """生成诊断状态页的 HTML (完整保留并美化原版样式)."""
        has_token = status_data['has_token']
        token_status = "有效 (Ready)" if has_token else "未获取 (Error)"
        token_color = "#27ae60" if has_token else "#e74c3c"
        
        # 处理最近事件展示
        last_event = status_data.get("last_event")
        event_preview = "无"
        if last_event:
            event_preview = f"类型: {last_event.get('type')}, 发送者: {last_event.get('user')}"

        return f"""
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>企微通集成状态诊断</title>
            <style>
                body {{ font-family: -apple-system, system-ui, sans-serif; background: #f0f2f5; margin: 0; padding: 20px; color: #1c1e21; }}
                .container {{ max-width: 500px; margin: 0 auto; background: #fff; padding: 25px; border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.1); }}
                .header {{ display: flex; align-items: center; border-bottom: 1px solid #ebedf0; margin-bottom: 20px; padding-bottom: 15px; }}
                .logo {{ background: #07c160; color: white; width: 40px; height: 40px; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-weight: bold; margin-right: 12px; }}
                h2 {{ margin: 0; font-size: 1.25rem; }}
                .status-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #f0f2f5; font-size: 14px; }}
                .status-label {{ color: #65676b; }}
                .status-value {{ font-weight: 500; font-family: monospace; word-break: break-all; }}
                .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #8a8d91; }}
                .log-box {{ background: #1c1e21; color: #a3e635; padding: 12px; border-radius: 6px; font-size: 12px; margin-top: 15px; overflow-x: auto; line-height: 1.5; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div class="logo">企</div>
                    <h2>企微通集成状态</h2>
                </div>
                <div class="status-row">
                    <span class="status-label">Access Token</span>
                    <span class="status-value" style="color: {token_color}">{token_status}</span>
                </div>
                <div class="status-row">
                    <span class="status-label">Agent ID</span>
                    <span class="status-value">{status_data['agent_id']}</span>
                </div>
                <div class="status-row">
                    <span class="status-label">URL 验证状态</span>
                    <span class="status-value" style="color: #27ae60">配置正常 ✓</span>
                </div>
                <div class="status-row">
                    <span class="status-label">最近消息时间</span>
                    <span class="status-value">{status_data['last_msg_time'] or '等待首条消息...'}</span>
                </div>
                <div class="status-row">
                    <span class="status-label">最近消息预览</span>
                    <span class="status-value" style="font-size: 12px;">{event_preview}</span>
                </div>
                <div class="log-box">
                    <strong>诊断信息:</strong><br>
                    Mode: Coordinator Architecture (2026)<br>
                    Callback URL: {self.coordinator.callback_url}<br>
                    External URL: {status_data['external_url']}
                </div>
            </div>
            <div class="footer">WorkChat Integration for Home Assistant</div>
        </body>
        </html>
        """
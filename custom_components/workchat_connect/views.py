"""处理企微回调与诊断视图."""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .const import CONF_TOKEN, DOMAIN, LOGGER

class _BaseWorkChatView(HomeAssistantView):
    """企微视图基类：按 URL 中的 token 查找对应配置项的协调器."""

    def _get_coordinator(self, token: str):
        """从多配置项注册表中，按 token 找到对应的协调器."""
        registry = self.hass.data.get(DOMAIN, {})
        for coord in registry.values():
            if coord.entry.data.get(CONF_TOKEN) == token:
                return coord
        return None

    def _verify_signature(self, coordinator, sig: str, ts: str, nonce: str, data: str) -> bool:
        """校验企微签名（对 None 入参做防御，避免排序时抛异常）."""
        try:
            if not all(isinstance(x, str) for x in (sig, ts, nonce, data)):
                return False
            config_token = coordinator.entry.data[CONF_TOKEN]
            tmp = sorted([config_token, ts, nonce, data])
            return hashlib.sha1("".join(tmp).encode()).hexdigest() == sig
        except Exception as err:
            LOGGER.error("签名校验算法异常: %s", err)
            return False

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

        if msg_type == "text":
            data["content"] = root.find("Content").text
        elif msg_type == "voice":
            data["media_id"] = root.find("MediaId").text
            data["format"] = root.find("Format").text if root.find("Format") is not None else "amr"
            data["msg_id"] = root.find("MsgId").text if root.find("MsgId") is not None else ""
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

class WorkChatCallbackView(_BaseWorkChatView):
    """处理企微回调：URL 验证(GET+echostr) / 消息接收(POST) / 诊断状态页(GET 无 echostr).。"""

    url = "/api/workchat_callback/{token}"
    name = "api:workchat_callback"
    requires_auth = False  # 企微服务器访问不需要 HA 登录；诊断页同样凭机密 Token 访问

    def __init__(self, hass) -> None:
        """初始化视图."""
        self.hass = hass

    async def get(self, request: web.Request, token: str) -> web.Response:
        """处理 GET 请求：企微 URL 验证，或无 echostr 时返回诊断状态页."""
        coordinator = self._get_coordinator(token)
        if coordinator is None:
            return web.Response(status=403, text="Token Mismatch")

        q = request.query
        echostr = q.get("echostr")

        # 情况 A: 非验证请求（无 echostr）—— 渲染诊断状态页
        if not echostr:
            info = coordinator.get_diagnostic_info()
            info["entry_count"] = len(self.hass.data.get(DOMAIN, {}))
            return web.Response(text=self._get_status_html(info), content_type="text/html")

        # 情况 B: 企微后台 URL 验证逻辑
        sig = q.get("msg_signature")
        ts = q.get("timestamp")
        nonce = q.get("nonce")

        if self._verify_signature(coordinator, sig, ts, nonce, echostr):
            try:
                decrypted = await coordinator.hass.async_add_executor_job(
                    coordinator.encryptor.decrypt, echostr
                )
                LOGGER.info("企微 URL 验证成功")
                return web.Response(text=decrypted)
            except Exception as err:
                LOGGER.error("企微解密 echostr 失败 (请检查 EncodingAESKey): %s", err)
        else:
            LOGGER.warning("企微签名验证不通过 (GET)")

        return web.Response(status=400, text="Verification Failed")

    async def post(self, request: web.Request, token: str) -> web.Response:
        """处理 POST 请求：接收企微推送的加密消息."""
        coordinator = self._get_coordinator(token)
        if coordinator is None:
            return web.Response(status=403)

        try:
            body = await request.text()
            root = ET.fromstring(body)
            encrypt_msg = root.find("Encrypt").text

            q = request.query
            if not self._verify_signature(
                coordinator, q.get("msg_signature"), q.get("timestamp"), q.get("nonce"), encrypt_msg
            ):
                LOGGER.warning("企微 POST 签名验证不通过")
                return web.Response(status=401)

            decrypted_xml = await coordinator.hass.async_add_executor_job(
                coordinator.encryptor.decrypt, encrypt_msg
            )

            event_data = self._parse_xml(decrypted_xml)
            coordinator.process_callback_data(event_data)

            return web.Response(text="success")
        except Exception as err:
            LOGGER.error("处理企微推送消息异常: %s", err)
            return web.Response(status=500)

    def _get_status_html(self, info: dict[str, Any]) -> str:
        """生成丰富版诊断状态页 HTML."""
        has_token = info["token_ready"]
        token_status = "有效 (Ready)" if has_token else "未获取 (Error)"
        token_color = "#27ae60" if has_token else "#e74c3c"
        token_expires = info.get("token_expires_at") or "—"

        last_event = info.get("last_event")
        last_preview = "无"
        if last_event:
            last_preview = f"类型: {last_event.get('type')}, 发送者: {last_event.get('user')}"

        last_upload = info.get("last_upload") or {}
        upload_preview = "无"
        if last_upload.get("media_id"):
            upload_preview = (
                f"{last_upload.get('type')} / {last_upload.get('upload_time')}<br>"
                f"media_id: {last_upload.get('media_id')}"
            )

        # 最近事件列表（仅类型/发送者/时间）
        recent_rows = "".join(
            f"<tr><td>{e.get('time')}</td><td>{e.get('type')}</td><td>{e.get('user')}</td></tr>"
            for e in reversed(info.get("recent_events", []))
        ) or "<tr><td colspan='3' style='color:#8a8d91'>暂无事件</td></tr>"

        return f"""
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>企微通集成状态诊断</title>
            <style>
                body {{ font-family: -apple-system, system-ui, sans-serif; background: #f0f2f5; margin: 0; padding: 20px; color: #1c1e21; }}
                .container {{ max-width: 640px; margin: 0 auto; background: #fff; padding: 25px; border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.1); }}
                .header {{ display: flex; align-items: center; border-bottom: 1px solid #ebedf0; margin-bottom: 20px; padding-bottom: 15px; }}
                .logo {{ background: #07c160; color: white; width: 40px; height: 40px; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-weight: bold; margin-right: 12px; }}
                h2 {{ margin: 0; font-size: 1.25rem; }}
                .status-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #f0f2f5; font-size: 14px; }}
                .status-label {{ color: #65676b; }}
                .status-value {{ font-weight: 500; font-family: monospace; word-break: break-all; text-align: right; }}
                .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #8a8d91; }}
                .log-box {{ background: #1c1e21; color: #a3e635; padding: 12px; border-radius: 6px; font-size: 12px; margin-top: 15px; overflow-x: auto; line-height: 1.5; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }}
                th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #f0f2f5; }}
                th {{ color: #65676b; font-weight: 600; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div class="logo">企</div>
                    <h2>企微通集成状态</h2>
                </div>
                <div class="status-row">
                    <span class="status-label">集成版本</span>
                    <span class="status-value">{info.get('version')}</span>
                </div>
                <div class="status-row">
                    <span class="status-label">当前配置项数量</span>
                    <span class="status-value">{info.get('entry_count')}</span>
                </div>
                <div class="status-row">
                    <span class="status-label">Access Token</span>
                    <span class="status-value" style="color: {token_color}">{token_status}</span>
                </div>
                <div class="status-row">
                    <span class="status-label">Token 过期时间</span>
                    <span class="status-value">{token_expires}</span>
                </div>
                <div class="status-row">
                    <span class="status-label">Corp ID</span>
                    <span class="status-value">{info.get('corp_id')}</span>
                </div>
                <div class="status-row">
                    <span class="status-label">Agent ID</span>
                    <span class="status-value">{info.get('agent_id')}</span>
                </div>
                <div class="status-row">
                    <span class="status-label">External URL</span>
                    <span class="status-value">{info.get('external_url')}</span>
                </div>
                <div class="status-row">
                    <span class="status-label">回调地址 (Webhook)</span>
                    <span class="status-value">{info.get('callback_url')}</span>
                </div>
                <div class="status-row">
                    <span class="status-label">最近消息时间</span>
                    <span class="status-value">{info.get('last_msg_time') or '等待首条消息...'}</span>
                </div>
                <div class="status-row">
                    <span class="status-label">最近消息预览</span>
                    <span class="status-value" style="font-size: 12px;">{last_preview}</span>
                </div>
                <div class="status-row">
                    <span class="status-label">最近媒体上传</span>
                    <span class="status-value" style="font-size: 12px;">{upload_preview}</span>
                </div>

                <h3 style="margin: 18px 0 6px; font-size: 14px; color: #65676b;">最近事件 (最多 {20} 条)</h3>
                <table>
                    <thead><tr><th>时间</th><th>类型</th><th>发送者</th></tr></thead>
                    <tbody>{recent_rows}</tbody>
                </table>

                <div class="log-box">
                    <strong>诊断信息:</strong><br>
                    Mode: Multi-Entry Coordinator Architecture (2026)<br>
                    Callback URL: {info.get('callback_url')}<br>
                    External URL: {info.get('external_url')}
                </div>
            </div>
            <div class="footer">WorkChat Integration for Home Assistant · 凭回调 Token 访问</div>
        </body>
        </html>
        """

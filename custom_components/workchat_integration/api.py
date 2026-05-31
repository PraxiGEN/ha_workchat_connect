"""企微通 API 客户端 ."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from yarl import URL
from aiohttp import ClientSession

from .const import (
    API_BASE, 
    API_GET_TOKEN, 
    MSG_TYPE_TEXT,
    MSG_TYPE_MARKDOWN,
    MSG_TYPE_TEXTCARD,
    MSG_TYPE_TEMPLATE_CARD,
    MSG_TYPE_NEWS,
    MSG_TYPE_IMAGE,
    MSG_TYPE_FILE,
    MSG_TYPE_VOICE,
    MSG_TYPE_VIDEO
)

_LOGGER = logging.getLogger(__name__)

class WorkChatApi:
    """封装企业微信所有 API 请求."""

    def __init__(
        self, 
        session: ClientSession, 
        corp_id: str, 
        secret: str, 
        agent_id: str,
        proxy: str | None = None
    ) -> None:
        self.session = session
        self.corp_id = corp_id
        self.secret = secret
        self.agent_id = agent_id
        self.proxy = proxy
        self.base_url = URL(API_BASE)
        
        self._access_token: str | None = None
        self._token_expire: float = 0
        self._lock = asyncio.Lock()

    async def get_access_token(self, force_refresh: bool = False) -> str | None:
        """获取有效的 Access Token."""
        async with self._lock:
            # 如果 Token 未过期且不强制刷新，直接返回缓存
            if not force_refresh and self._access_token and time.time() < self._token_expire - 300:
                return self._access_token

            url = self.base_url / API_GET_TOKEN
            params = {"corpid": self.corp_id, "corpsecret": self.secret}

            try:
                async with self.session.get(url, params=params, proxy=self.proxy, timeout=10) as resp:
                    data = await resp.json()
                    if data.get("errcode") == 0:
                        self._access_token = data["access_token"]
                        # 企微通常返回 7200 秒有效期
                        self._token_expire = time.time() + data["expires_in"]
                        _LOGGER.debug("企微 Token 刷新成功，有效期至: %s", 
                                     time.strftime('%H:%M:%S', time.localtime(self._token_expire)))
                        return self._access_token
                    _LOGGER.error("获取企微 Token 失败: %s", data.get("errmsg"))
            except Exception as err:
                _LOGGER.error("获取企微 Token 网络异常: %s (代理: %s)", err, self.proxy)
            return None

    async def post_api(self, path: str, json_data: dict | None = None, params: dict | None = None, data: Any = None, retry: int = 1) -> dict:
            """通用的 POST 请求方法，支持自动 Token 刷新及 URL 参数."""
            token = await self.get_access_token()
            if not token:
                return {"errcode": -1, "errmsg": "no_token"}

            url = (self.base_url / path).with_query(access_token=token)
            
            if params:
                url = url.update_query(params)
            
            try:
                async with self.session.post(url, json=json_data, data=data, proxy=self.proxy, timeout=30) as resp:
                    res = await resp.json()
                    
                    # 处理 Token 失效
                    if res.get("errcode") in [40014, 42001] and retry > 0:
                        _LOGGER.info("企微 Token 失效，尝试刷新重试...")
                        await self.get_access_token(force_refresh=True)
                        return await self.post_api(path, json_data, params, data, retry=retry-1)
                    
                    return res
            except Exception as err:
                _LOGGER.error("企微 API 请求异常 [%s]: %s", path, err)
                return {"errcode": -1, "errmsg": str(err)}
            
    def build_message_payload(self, **kwargs: Any) -> dict:
        """构建消息 Payload (使用常量替换魔法字符串)."""
        msg_type = kwargs.get("msg_type", MSG_TYPE_TEXT)
        
        # 基础结构
        payload: dict[str, Any] = {
            "touser": kwargs.get("touser", "@all"),
            "agentid": self.agent_id,
            "msgtype": msg_type,
            "safe": kwargs.get("safe", 0),
            "enable_id_trans": kwargs.get("enable_id_trans", 0),
            "enable_duplicate_check": kwargs.get("enable_duplicate_check", 0)
        }

        # 具体的各种消息类型逻辑映射
        if msg_type in [MSG_TYPE_TEXT, MSG_TYPE_MARKDOWN]:
            payload[msg_type] = {"content": kwargs.get("message")}
            
        elif msg_type == MSG_TYPE_TEXTCARD:
            payload[MSG_TYPE_TEXTCARD] = {
                "title": kwargs.get("title"),
                "description": kwargs.get("message"),
                "url": kwargs.get("url", ""),
                "btntxt": kwargs.get("btntxt", "详情")
            }
            
        elif msg_type == MSG_TYPE_TEMPLATE_CARD:
            payload[MSG_TYPE_TEMPLATE_CARD] = kwargs.get("template_card_data")
            
        elif msg_type == MSG_TYPE_NEWS:
            articles = kwargs.get("articles") or [{
                "title": kwargs.get("title"),
                "description": kwargs.get("message"),
                "url": kwargs.get("url"),
                "picurl": kwargs.get("picurl")
            }]
            payload[MSG_TYPE_NEWS] = {"articles": articles}
            
        elif msg_type in [MSG_TYPE_IMAGE, MSG_TYPE_FILE, MSG_TYPE_VOICE]:
            payload[msg_type] = {"media_id": kwargs.get("media_id")}
            
        elif msg_type == MSG_TYPE_VIDEO:
            payload[MSG_TYPE_VIDEO] = {
                "media_id": kwargs.get("media_id"),
                "title": kwargs.get("title"),
                "description": kwargs.get("message")
            }
            
        return payload
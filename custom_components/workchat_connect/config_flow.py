"""企微通集成的配置流程 ."""
from __future__ import annotations

from typing import Any
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    LOGGER,
    CONF_CORP_ID,
    CONF_SECRET,
    CONF_AGENT_ID,
    CONF_TOKEN,
    CONF_AES_KEY,
    CONF_RECEIVE_USER,
    CONF_EXTERNAL_URL,
    CONF_PROXY,
    API_BASE,
)

def _normalize_proxy(proxy: str | None) -> tuple[str, str | None]:
    """规范化代理并校验格式."""
    proxy = (proxy or "").strip()
    if proxy and not proxy.startswith(("http://", "https://")):
        return proxy, "invalid_proxy_format"
    return proxy, None

class WorkChatIntegrationFlowHandler(ConfigFlow, domain=DOMAIN):
    """处理企微通集成的配置流程."""

    VERSION = 1

    async def _test_credentials(self, user_input: dict[str, Any]) -> str | None:
        """测试连接（包含可选代理测试）."""
        session = async_get_clientsession(self.hass)
        
        # 处理可选代理：如果是空字符串或全是空格，则设为 None
        proxy = user_input.get(CONF_PROXY, "").strip() or None
        
        url = f"{API_BASE}/gettoken"
        params = {
            "corpid": user_input[CONF_CORP_ID],
            "corpsecret": user_input[CONF_SECRET]
        }
        
        try:
            # 在测试时就应用用户填写的代理，确保代理本身是通的
            async with session.get(url, params=params, proxy=proxy, timeout=10) as resp:
                data = await resp.json()
                if data.get("errcode") != 0:
                    LOGGER.error("企微认证错误: %s", data.get("errmsg"))
                    return "invalid_auth"
        except Exception as err:
            LOGGER.error("连接企微失败 (代理: %s): %s", proxy, err)
            return "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """用户初始配置."""
        errors: dict[str, str] = {}

        if user_input is not None:
            proxy, proxy_error = _normalize_proxy(user_input.get(CONF_PROXY))
            if proxy_error:
                errors[CONF_PROXY] = proxy_error
            
            if not errors:
                # 唯一性校验
                await self.async_set_unique_id(f"{user_input[CONF_CORP_ID]}_{user_input[CONF_AGENT_ID]}")
                self._abort_if_unique_id_configured()

                # 认证测试
                error = await self._test_credentials(user_input)
                if error:
                    errors["base"] = error
                else:
                    # 数据清理并创建
                    user_input[CONF_EXTERNAL_URL] = user_input[CONF_EXTERNAL_URL].rstrip("/") + "/"
                    user_input[CONF_PROXY] = proxy or "" # 存入空字符串而不是 None
                    
                    return self.async_create_entry(
                        title=f"企微应用 {user_input[CONF_AGENT_ID]}",
                        data=user_input
                    )

        default_url = self.hass.config.external_url or self.hass.config.internal_url or ""

        data_schema = vol.Schema({
            vol.Required(CONF_CORP_ID): str,
            vol.Required(CONF_SECRET): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_AGENT_ID): str,
            vol.Required(CONF_TOKEN): str,
            vol.Required(CONF_AES_KEY): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(CONF_RECEIVE_USER, default="@all"): str,
            vol.Required(CONF_EXTERNAL_URL, default=default_url): str,
            vol.Optional(CONF_PROXY, default=""): str, # 设置默认值为空字符串
        })

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> WorkChatOptionsFlowHandler:
        return WorkChatOptionsFlowHandler()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """处理重新配置流程。"""
        # 获取当前正在重新配置的 Entry
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="entry_not_found")

        errors: dict[str, str] = {}
        if user_input is not None:
            proxy, proxy_error = _normalize_proxy(user_input.get(CONF_PROXY))
            if proxy_error:
                errors[CONF_PROXY] = proxy_error
            else:
                # 校验新凭据
                error = await self._test_credentials(user_input)
                if error:
                    errors["base"] = error
                else:
                    user_input[CONF_PROXY] = proxy
                    # 标准化 URL
                    user_input[CONF_EXTERNAL_URL] = user_input[CONF_EXTERNAL_URL].rstrip("/") + "/"

                    # 更新现有配置并退出流程
                    return self.async_update_reload_and_abort(
                        entry,
                        data={**entry.data, **user_input},
                        reason="reconfigure_successful"
                    )

        # 预填当前数据
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(CONF_CORP_ID, default=entry.data.get(CONF_CORP_ID)): str,
                vol.Required(CONF_SECRET, default=entry.data.get(CONF_SECRET)): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_AGENT_ID, default=entry.data.get(CONF_AGENT_ID)): str,
                vol.Required(CONF_TOKEN, default=entry.data.get(CONF_TOKEN)): str,
                vol.Required(CONF_AES_KEY, default=entry.data.get(CONF_AES_KEY)): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_EXTERNAL_URL, default=entry.data.get(CONF_EXTERNAL_URL)): str,
                vol.Optional(CONF_PROXY, default=entry.data.get(CONF_PROXY, "")): str,
            }),
            errors=errors,
        )

class WorkChatOptionsFlowHandler(OptionsFlowWithReload):
    """处理集成选项修改（保存到 entry.options，变更后自动重载）."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """管理选项菜单."""
        errors: dict[str, str] = {}

        if user_input is not None:
            proxy, proxy_error = _normalize_proxy(user_input.get(CONF_PROXY))
            if proxy_error:
                errors[CONF_PROXY] = proxy_error
            else:
                user_input[CONF_PROXY] = proxy
                # OptionsFlowWithReload 在流程结束时写入 entry.options 并自动重载
                return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_RECEIVE_USER,
                    default=self.config_entry.data.get(CONF_RECEIVE_USER, "@all"),
                ): str,
                vol.Optional(
                    CONF_PROXY,
                    default=self.config_entry.data.get(CONF_PROXY, ""),
                ): str,
            }),
            errors=errors,
        )
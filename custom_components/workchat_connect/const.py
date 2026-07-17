"""企微通集成的常量定义."""
from typing import Final
from homeassistant.const import Platform
import logging

# 集成域
DOMAIN: Final = "workchat_connect"
LOGGER = logging.getLogger(__package__)

# 支持的平台
PLATFORMS: Final = [
    Platform.NOTIFY,
    Platform.SENSOR,
]

# 配置键名
CONF_CORP_ID: Final = "corp_id"
CONF_SECRET: Final = "secret"
CONF_AGENT_ID: Final = "agent_id"
CONF_TOKEN: Final = "token"
CONF_AES_KEY: Final = "aes_key"
CONF_RECEIVE_USER: Final = "receive_user"
CONF_EXTERNAL_URL: Final = "external_url"
CONF_PROXY: Final = "proxy"

# 企业微信 API 相关
API_BASE: Final = "https://qyapi.weixin.qq.com/cgi-bin"
# 补充 API 路径常量
API_GET_TOKEN: Final = "gettoken"
API_SEND_MESSAGE: Final = "message/send"
API_UPLOAD_MEDIA: Final = "media/upload"
API_GET_MEDIA: Final = "media/get"

# 消息类型常量
MSG_TYPE_TEXT: Final = "text"
MSG_TYPE_MARKDOWN: Final = "markdown"
MSG_TYPE_TEXTCARD: Final = "textcard"
MSG_TYPE_TEMPLATE_CARD: Final = "template_card"
MSG_TYPE_NEWS: Final = "news"
MSG_TYPE_IMAGE: Final = "image"
MSG_TYPE_FILE: Final = "file"
MSG_TYPE_VOICE: Final = "voice"
MSG_TYPE_VIDEO: Final = "video"

# 定义传感器类型枚举（内部使用）
TYPE_MSG: Final = "message"
TYPE_INFO: Final = "info"
TYPE_UPLOAD: Final = "upload"

# 默认值
DEFAULT_RECEIVE_USER: Final = "@all"
DEFAULT_VOICE_RETENTION_DAYS: Final = 3

# 事件名称
EVENT_MESSAGE_RECEIVED: Final = f"{DOMAIN}_message"
EVENT_MEDIA_UPLOADED: Final = f"{DOMAIN}_media_uploaded"
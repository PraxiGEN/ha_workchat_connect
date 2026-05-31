"""企业微信加解密助手."""
from __future__ import annotations

import base64
import logging
import os
import struct
from typing import Final
from Crypto.Cipher import AES

_LOGGER = logging.getLogger(__name__)

BLOCK_SIZE: Final = 32  # 企微固定 32 字节对齐
IV_SIZE: Final = 16

class EncryptHelper:
    """处理企业微信消息加解密的助手类."""

    def __init__(self, aes_key_base64: str, receive_id: str) -> None:
        """
        初始化加密助手.
        :param aes_key_base64: 企微后台生成的 EncodingAESKey
        :param receive_id: 企微 CorpID (注意：不是 Token)
        """
        self.receive_id = receive_id
        try:
            # 企微 EncodingAESKey 固定为 43 位，Base64 解码后为 32 字节
            # 若长度不等于 44，尝试补位以符合标准 Base64 规范
            key_decode_str = aes_key_base64.strip()
            if len(key_decode_str) == 43:
                key_decode_str += "="
            
            self.key = base64.b64decode(key_decode_str)
            if len(self.key) != 32:
                raise ValueError(f"AES Key 长度错误: 预期 32 字节，实际 {len(self.key)}")
        except Exception as err:
            _LOGGER.error("EncodingAESKey 解码失败: %s", err)
            raise ValueError("Invalid EncodingAESKey") from err

    def encrypt(self, text: str) -> str:
        """加密消息内容 (用于被动回复)."""
        try:
            text_bytes = text.encode("utf-8")
            # 1. 构造包体: random(16B) + msg_len(4B) + msg + receive_id
            random_bytes = os.urandom(16)
            msg_len = struct.pack(">I", len(text_bytes))
            receive_id_bytes = self.receive_id.encode("utf-8")
            
            raw_data = random_bytes + msg_len + text_bytes + receive_id_bytes
            
            # 2. PKCS#7 填充 (32 字节块对齐)
            pad_len = BLOCK_SIZE - (len(raw_data) % BLOCK_SIZE)
            raw_data += bytes([pad_len]) * pad_len
            
            # 3. AES-CBC 加密 (企微规定 IV 为 Key 的前 16 位)
            cipher = AES.new(self.key, AES.MODE_CBC, iv=self.key[:IV_SIZE])
            encrypted_bytes = cipher.encrypt(raw_data)
            
            return base64.b64encode(encrypted_bytes).decode("utf-8")
        except Exception as err:
            _LOGGER.error("企微加密异常: %s", err)
            raise

    def decrypt(self, encrypted_base64: str) -> str:
        """解密消息内容 (用于 URL 验证和消息接收)."""
        if not encrypted_base64:
            raise ValueError("加密数据为空")

        try:
            # 1. Base64 解码
            encrypted_bytes = base64.b64decode(encrypted_base64)
            
            # 2. AES-CBC 解密
            cipher = AES.new(self.key, AES.MODE_CBC, iv=self.key[:IV_SIZE])
            decrypted_raw = cipher.decrypt(encrypted_bytes)
            
            # 3. 移除 PKCS#7 填充
            pad_len = decrypted_raw[-1]
            if pad_len < 1 or pad_len > BLOCK_SIZE:
                # 兼容性处理：如果填充长度异常，则不移除
                pad_len = 0
            content_raw = decrypted_raw[:-pad_len] if pad_len > 0 else decrypted_raw
            
            # 4. 提取结构信息: random(16B) + msg_len(4B) + msg + ReceiveID
            # content_raw[16:20] 是 4 字节的消息长度
            msg_len = struct.unpack(">I", content_raw[16:20])[0]
            
            # 5. 提取消息正文
            msg_content = content_raw[20 : 20 + msg_len].decode("utf-8")
            
            # 6. 校验 CorpID (ReceiveID)
            # 企微验证 URL (GET) 时，解密后末尾必须是 CorpID
            received_id = content_raw[20 + msg_len :].decode("utf-8").strip()
            if received_id != self.receive_id:
                _LOGGER.warning(
                    "企微加解密校验身份不匹配: 收到 CorpID %s, 预期 CorpID %s (请检查配置)", 
                    received_id, self.receive_id
                )
            
            return msg_content
        except Exception as err:
            _LOGGER.error("企微解密失败 (请检查 EncodingAESKey 是否正确): %s", err)
            raise
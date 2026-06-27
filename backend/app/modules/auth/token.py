"""无状态会话 token —— HMAC 签名，无服务端会话表。

设计理由（贴合服务器内存约束）：
无状态 token = 不维护会话表、重启不丢会话、省内存。轮换 WEBUI_AUTH_SECRET
即吊销全部旧 token（合理的安全副作用）。

token 格式：`base64url(payload_json).base64url(hmac_sig)`，点分。
payload = {"sub": subject, "iat": int, "exp": int}（epoch 秒）。
签名 = HMAC-SHA256(auth_secret, payload_b64)。

铁律：
  - 所有比较走 hmac.compare_digest（常量时间，防时序侧信道）。
  - 密钥从 settings.auth_secret（环境注入），绝不硬编码。
  - exp 到期 → 无效。iat 不校验（信任签发者即本服务自己）。
  - 不为已过期 token 提供任何信息泄露（统一返回『无效』）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

# token 形式遵循『不动态计时』原则：
# 测试需可控时间，故签发/校验的 now 由调用方注入（见 dev 的 dependency 处）。
# 生产调用时传 time.time()。脚本环境无 Date.now 风险由调用方承担。


class TokenError(Exception):
    """token 无效/过期/损坏。统一语义，不向外区分（防信息泄露）。"""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _hmac_sig(secret: str, payload_b64: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64(digest)


def issue(*, subject: str, secret: str, ttl_seconds: int, now: float) -> str:
    """签发一个无状态 token。返回 `payload_b64.sig_b64`。

    secret 为空 → ValueError（调用方 require_auth 已守 503，这里再防一道）。
    """
    if not secret:
        raise ValueError("签发 token 需要非空密钥")
    payload = {"sub": subject, "iat": int(now), "exp": int(now) + int(ttl_seconds)}
    payload_b64 = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b64}.{_hmac_sig(secret, payload_b64)}"


def verify(*, token: str, secret: str, now: float) -> str:
    """校验 token。成功返回 subject；任一失败 → TokenError（不区分原因，防探测）。

    常量时间比较签名；过期视为无效。
    """
    if not secret:
        raise TokenError("未配置密钥")
    if not token or token.count(".") != 1:
        raise TokenError("格式错误")
    payload_b64, sig_b64 = token.split(".")
    expected = _hmac_sig(secret, payload_b64)
    if not hmac.compare_digest(sig_b64, expected):
        raise TokenError("签名不匹配")
    try:
        payload = json.loads(_unb64(payload_b64))
        exp = int(payload["exp"])
        subject = str(payload["sub"])
    except (ValueError, KeyError, TypeError) as exc:
        raise TokenError("payload 损坏") from exc
    if exp <= int(now):
        raise TokenError("已过期")
    return subject


__all__ = ["TokenError", "issue", "verify"]

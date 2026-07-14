"""Authenticates API callers.

Two trust paths hit this API:
  1. The miniapp (browser JS) — proven via Telegram's WebApp initData HMAC.
  2. The bot service — proven via a shared internal token, since Telegram's
     own Bot API already authenticated the user for it (message.from_user.id
     is not forgeable by the end user).
Never trust a client-supplied tg_user_id on its own — every route resolves
the caller's real id through one of these two paths before using it.
"""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
INIT_DATA_MAX_AGE_SECONDS = int(os.getenv("INIT_DATA_MAX_AGE_SECONDS", "86400"))


def _verify_init_data(init_data: str) -> dict:
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("missing hash")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise ValueError("bad signature")

    auth_date = int(parsed.get("auth_date", "0") or "0")
    if INIT_DATA_MAX_AGE_SECONDS > 0 and time.time() - auth_date > INIT_DATA_MAX_AGE_SECONDS:
        raise ValueError("init data expired")

    return parsed


def _user_id_from_init_data(init_data: str) -> int:
    parsed = _verify_init_data(init_data)
    user = json.loads(parsed["user"])
    return int(user["id"])


def resolve_tg_user_id(
    claimed_tg_user_id: int,
    x_telegram_init_data: str | None,
    x_internal_token: str | None,
) -> int:
    """Returns the caller's real tg_user_id, or raises 401/403."""
    if x_internal_token and INTERNAL_API_TOKEN and hmac.compare_digest(x_internal_token, INTERNAL_API_TOKEN):
        return claimed_tg_user_id  # bot service — already Telegram-authenticated upstream

    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="Server misconfigured: BOT_TOKEN not set")
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Missing Telegram auth")
    try:
        verified_id = _user_id_from_init_data(x_telegram_init_data)
    except (ValueError, KeyError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="Invalid Telegram auth")
    if verified_id != claimed_tg_user_id:
        raise HTTPException(status_code=403, detail="User mismatch")
    return verified_id


def require_internal(x_internal_token: str | None) -> None:
    """For routes only the bot service may call (e.g. crediting balance)."""
    if not (x_internal_token and INTERNAL_API_TOKEN and hmac.compare_digest(x_internal_token, INTERNAL_API_TOKEN)):
        raise HTTPException(status_code=401, detail="Internal endpoint")


TelegramInitDataHeader = Header(default=None, alias="X-Telegram-Init-Data")
InternalTokenHeader = Header(default=None, alias="X-Internal-Token")

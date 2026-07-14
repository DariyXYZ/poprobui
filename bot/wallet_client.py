"""Talks to the api service's wallet endpoints over HTTP.

bot and api deploy as separate services (see README) — reading/writing
bot/data/balances.json directly from here would silently desync from
whatever the api process (and the miniapp, through it) sees. api is the
single owner of wallet state; this module is the only way bot touches it.
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
_HEADERS = {"X-Internal-Token": INTERNAL_API_TOKEN}
_TIMEOUT = 8.0


def format_money(amount: int) -> str:
    rub = amount // 100
    kop = amount % 100
    return f"{rub} ₽" if kop == 0 else f"{rub},{kop:02d} ₽"


async def balance_of(user_id: int) -> int:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{API_URL}/wallet/{user_id}", headers=_HEADERS)
            resp.raise_for_status()
            return int(resp.json()["balance"])
    except Exception:
        logger.exception("wallet_client.balance_of failed for user_id=%s", user_id)
        return 0


async def add_balance(user_id: int, amount: int) -> int | None:
    """Returns the new balance, or None if the api call failed (e.g. after a
    confirmed payment) — callers must tell the user their balance update is
    pending rather than silently reporting a wrong number."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{API_URL}/wallet/credit",
                json={"tg_user_id": user_id, "amount": amount},
                headers=_HEADERS,
            )
            resp.raise_for_status()
            return int(resp.json()["balance"])
    except Exception:
        logger.exception("wallet_client.add_balance failed for user_id=%s amount=%s", user_id, amount)
        return None


async def debit_balance(user_id: int, amount: int, test_id: str) -> int | None:
    """Returns the new balance, or None on insufficient funds OR api failure —
    fails closed: if the api is unreachable we must not let the test open for free."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{API_URL}/wallet/debit",
                json={"tg_user_id": user_id, "test_id": test_id, "amount": amount},
                headers=_HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["balance"] if data["ok"] else None
    except Exception:
        logger.exception("wallet_client.debit_balance failed for user_id=%s test_id=%s", user_id, test_id)
        return None


async def generate_pdf_bytes(user_id: int, test_id: str, scores: dict) -> bytes:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{API_URL}/pdf/generate",
            json={"tg_user_id": user_id, "test_id": test_id, "scores": scores},
            headers=_HEADERS,
        )
        resp.raise_for_status()
        return resp.content

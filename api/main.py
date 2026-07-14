import time
from collections import defaultdict, deque

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiosqlite
import asyncio
import os
import json

load_dotenv()

from pdf import generate_pdf
from prof_data import PROF_RICH
from scoring import score_test
from wallet import balance_of, add_balance, debit_balance
from telegram_auth import (
    resolve_tg_user_id,
    require_internal,
    TelegramInitDataHeader,
    InternalTokenHeader,
)

app = FastAPI(title="Попробуй API")

ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv(
        "ALLOWED_ORIGINS", "https://dariyxyz.github.io"
    ).split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Telegram-Init-Data", "X-Internal-Token"],
)

DATA_DIR = os.getenv("DATA_DIR") or os.path.dirname(__file__)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "poprobui.db")


# ── Minimal in-process rate limiting ────────────────────────────────────────
# One instance (min_machines_running = 1), so a per-process dict is enough to
# blunt brute-forcing of tg_user_id/result_id without adding a Redis dependency.
_RATE_LIMIT_WINDOW_S = 60
_RATE_LIMIT_MAX_HITS = 30
_rate_buckets: dict[str, deque] = defaultdict(deque)


def _rate_limit(request: Request, bucket: str) -> None:
    key = f"{bucket}:{request.client.host if request.client else 'unknown'}"
    now = time.monotonic()
    hits = _rate_buckets[key]
    while hits and now - hits[0] > _RATE_LIMIT_WINDOW_S:
        hits.popleft()
    if len(hits) >= _RATE_LIMIT_MAX_HITS:
        raise HTTPException(status_code=429, detail="Too many requests")
    hits.append(now)


# ── DB init ────────────────────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id INTEGER NOT NULL,
                test_id TEXT NOT NULL,
                answers TEXT NOT NULL,
                scores TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


@app.on_event("startup")
async def startup():
    await init_db()


# ── Models ─────────────────────────────────────────────────────────────────

class SubmitAnswers(BaseModel):
    tg_user_id: int
    test_id: str
    answers: dict


class ResultResponse(BaseModel):
    result_id: int
    scores: dict
    top_types: list
    recommendations: list


class WalletDebitRequest(BaseModel):
    tg_user_id: int
    test_id: str
    amount: int


class WalletDebitResponse(BaseModel):
    ok: bool
    balance: int
    required: int


class WalletCreditRequest(BaseModel):
    tg_user_id: int
    amount: int


class WalletCreditResponse(BaseModel):
    balance: int


class GeneratePdfRequest(BaseModel):
    tg_user_id: int
    test_id: str
    scores: dict


# Only these scale labels may ever appear as PDF section titles — anything
# else in a client-supplied `scores` dict (bot's "generate_pdf" action bypasses
# score_test()) is dropped rather than rendered.
KNOWN_SCALE_LABELS = set(PROF_RICH.keys())


def _sanitize_scores(scores: dict) -> dict:
    clean = {}
    for name, value in scores.items():
        if name not in KNOWN_SCALE_LABELS:
            continue
        try:
            clean[name] = float(value)
        except (TypeError, ValueError):
            continue
    return clean


# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/wallet/{tg_user_id}")
async def get_wallet(
    tg_user_id: int,
    request: Request,
    x_telegram_init_data: str | None = TelegramInitDataHeader,
    x_internal_token: str | None = InternalTokenHeader,
):
    _rate_limit(request, "wallet_read")
    resolve_tg_user_id(tg_user_id, x_telegram_init_data, x_internal_token)
    balance = await asyncio.to_thread(balance_of, tg_user_id)
    return {"balance": balance}


@app.post("/wallet/debit", response_model=WalletDebitResponse)
async def debit_wallet(
    payload: WalletDebitRequest,
    request: Request,
    x_telegram_init_data: str | None = TelegramInitDataHeader,
    x_internal_token: str | None = InternalTokenHeader,
):
    _rate_limit(request, "wallet_debit")
    resolve_tg_user_id(payload.tg_user_id, x_telegram_init_data, x_internal_token)

    expected_prices = {"yovayshi": 30000, "onett": 50000}
    required = expected_prices.get(payload.test_id)
    if required is None:
        raise HTTPException(status_code=400, detail="Unknown paid test")
    if payload.amount != required:
        raise HTTPException(status_code=400, detail="Invalid amount")

    rest = await asyncio.to_thread(debit_balance, payload.tg_user_id, required)
    if rest is None:
        balance = await asyncio.to_thread(balance_of, payload.tg_user_id)
        return WalletDebitResponse(ok=False, balance=balance, required=required)
    return WalletDebitResponse(ok=True, balance=rest, required=required)


@app.post("/wallet/credit", response_model=WalletCreditResponse)
async def credit_wallet(
    payload: WalletCreditRequest,
    request: Request,
    x_internal_token: str | None = InternalTokenHeader,
):
    # Bot-only: crediting happens after a confirmed YooKassa payment or an
    # explicit gift grant — never something an end user can trigger directly.
    _rate_limit(request, "wallet_credit")
    require_internal(x_internal_token)
    balance = await asyncio.to_thread(add_balance, payload.tg_user_id, payload.amount)
    return WalletCreditResponse(balance=balance)


@app.post("/submit", response_model=ResultResponse)
async def submit_answers(
    payload: SubmitAnswers,
    request: Request,
    x_telegram_init_data: str | None = TelegramInitDataHeader,
    x_internal_token: str | None = InternalTokenHeader,
):
    _rate_limit(request, "submit")
    resolve_tg_user_id(payload.tg_user_id, x_telegram_init_data, x_internal_token)

    scores = score_test(payload.test_id, payload.answers)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO results (tg_user_id, test_id, answers, scores) VALUES (?, ?, ?, ?)",
            (
                payload.tg_user_id,
                payload.test_id,
                json.dumps(payload.answers, ensure_ascii=False),
                json.dumps(scores, ensure_ascii=False),
            )
        )
        result_id = cursor.lastrowid
        await db.commit()

    top_types = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]
    recommendations = _get_recommendations([t for t, _ in top_types])

    return ResultResponse(
        result_id=result_id,
        scores=scores,
        top_types=[t for t, _ in top_types],
        recommendations=recommendations,
    )


@app.get("/results/{tg_user_id}")
async def get_results(
    tg_user_id: int,
    request: Request,
    x_telegram_init_data: str | None = TelegramInitDataHeader,
    x_internal_token: str | None = InternalTokenHeader,
):
    _rate_limit(request, "results")
    resolve_tg_user_id(tg_user_id, x_telegram_init_data, x_internal_token)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM results WHERE tg_user_id = ? ORDER BY created_at DESC",
            (tg_user_id,)
        )
        rows = await cursor.fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="No results found")
    return [dict(r) for r in rows]


@app.get("/pdf/{result_id}")
async def download_pdf(
    result_id: int,
    tg_user_id: int,
    request: Request,
    x_telegram_init_data: str | None = TelegramInitDataHeader,
    x_internal_token: str | None = InternalTokenHeader,
):
    _rate_limit(request, "pdf")
    resolve_tg_user_id(tg_user_id, x_telegram_init_data, x_internal_token)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM results WHERE id = ? AND tg_user_id = ?",
            (result_id, tg_user_id)
        )
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Result not found")

    scores = json.loads(row["scores"])
    path = await generate_pdf(result_id, tg_user_id, scores)
    return FileResponse(path, media_type="application/pdf", filename=f"poprobui_report_{result_id}.pdf")


@app.post("/pdf/generate")
async def generate_pdf_for_bot(
    payload: GeneratePdfRequest,
    request: Request,
    x_internal_token: str | None = InternalTokenHeader,
):
    # Bot-only: used for the "download PDF straight into the Telegram chat"
    # flow, where scores come from the miniapp's local run, not /submit.
    _rate_limit(request, "pdf_generate")
    require_internal(x_internal_token)

    scores = _sanitize_scores(payload.scores)
    if len(scores) < 3:
        raise HTTPException(status_code=400, detail="Not enough valid scale scores")
    pseudo_id = int(time.time() * 1000) % 2_147_483_647
    path = await generate_pdf(pseudo_id, payload.tg_user_id, scores, payload.test_id)
    return FileResponse(path, media_type="application/pdf", filename="poprobui_report.pdf")


# ── Helpers ────────────────────────────────────────────────────────────────

def _get_recommendations(top_types: list) -> list:
    # Recommendations come straight from prof_data's per-label profession
    # lists — this used to keep a second, separately-keyed dict here that
    # never matched scoring.py's actual labels, so it silently returned [].
    result = []
    for t in top_types:
        profile = PROF_RICH.get(t)
        if profile:
            result.extend(profile.get("profs", []))
    return result[:10]

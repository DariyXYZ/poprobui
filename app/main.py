import asyncio
import json
import os
from contextlib import asynccontextmanager

import aiosqlite
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice, Message, PreCheckoutQuery, WebAppInfo,
)

from scoring import score_test
from pdf import generate_pdf

# ── Config ─────────────────────────────────────────────────────────────────

BOT_TOKEN    = os.getenv("BOT_TOKEN")
MINIAPP_URL  = os.getenv("MINIAPP_URL", "")
YOOKASSA_PROVIDER_TOKEN = os.getenv("YOOKASSA_PROVIDER_TOKEN", "")
DATA_DIR     = os.getenv("DATA_DIR", os.path.dirname(__file__))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH      = os.path.join(DATA_DIR, "poprobui.db")

# ── Bot ────────────────────────────────────────────────────────────────────

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())


def kb_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧭 Пройти тест",
                              web_app=WebAppInfo(url=f"{MINIAPP_URL}?screen=test"))],
        [InlineKeyboardButton(text="📊 Мои результаты",
                              web_app=WebAppInfo(url=f"{MINIAPP_URL}?screen=results"))],
        [InlineKeyboardButton(text="💳 Пополнить кошелёк", callback_data="topup")],
        [InlineKeyboardButton(text="ℹ️ Как это работает",  callback_data="how_it_works")],
    ])

def kb_topup():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="299 руб - Базовый тест",    callback_data="pay_299"),
         InlineKeyboardButton(text="990 руб - Глубокий анализ", callback_data="pay_990")],
        [InlineKeyboardButton(text="<- Назад", callback_data="back_main")],
    ])

def kb_back():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="<- Назад", callback_data="back_main")]
    ])


@dp.message(CommandStart())
async def cmd_start(message: Message):
    name = message.from_user.first_name or "друг"
    await message.answer(
        f"Привет, {name}!\n\n"
        "Я помогу тебе понять — кем стать и куда двигаться.\n\n"
        "«Попробуй» — это профориентационный тест для школьников и студентов. "
        "15 минут — и ты получишь честный разбор своих интересов, способностей "
        "и подходящих профессий.\n\n"
        "Выбери с чего начать:",
        parse_mode="HTML",
        reply_markup=kb_main(),
    )

@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer("Главное меню:", reply_markup=kb_main())

@dp.callback_query(F.data == "back_main")
async def cb_back_main(call: CallbackQuery):
    await call.message.edit_text("Главное меню:", reply_markup=kb_main())
    await call.answer()

@dp.callback_query(F.data == "how_it_works")
async def cb_how_it_works(call: CallbackQuery):
    await call.message.edit_text(
        "<b>Как работает Попробуй</b>\n\n"
        "1. Проходишь тест прямо в Telegram\n\n"
        "2. Вопросы проверяют интересы, склонности и способности\n\n"
        "3. Результат — развёрнутый отчёт: профиль, профессии, данные рынка труда\n\n"
        "4. Отчёт можно сохранить в PDF\n\n"
        "<b>Базовый тест</b> — 299 руб\n"
        "<b>Глубокий анализ</b> — 990 руб (+ живой разбор с экспертом)",
        parse_mode="HTML",
        reply_markup=kb_back(),
    )
    await call.answer()

@dp.callback_query(F.data == "topup")
async def cb_topup(call: CallbackQuery):
    await call.message.edit_text(
        "<b>Выбери тариф</b>\n\nПосле оплаты тест откроется автоматически.",
        parse_mode="HTML",
        reply_markup=kb_topup(),
    )
    await call.answer()

@dp.callback_query(F.data == "pay_299")
async def cb_pay_299(call: CallbackQuery):
    if not YOOKASSA_PROVIDER_TOKEN:
        await call.answer("Платежи скоро будут подключены!", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title="Базовый тест «Попробуй»",
        description="Профориентационный тест + PDF-отчёт",
        payload="test_basic_299",
        provider_token=YOOKASSA_PROVIDER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label="Базовый тест", amount=29900)],
        start_parameter="pay_basic",
    )
    await call.answer()

@dp.callback_query(F.data == "pay_990")
async def cb_pay_990(call: CallbackQuery):
    if not YOOKASSA_PROVIDER_TOKEN:
        await call.answer("Платежи скоро будут подключены!", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title="Глубокий анализ «Попробуй»",
        description="Расширенный тест + PDF-отчёт + разбор с экспертом",
        payload="test_deep_990",
        provider_token=YOOKASSA_PROVIDER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label="Глубокий анализ", amount=99000)],
        start_parameter="pay_deep",
    )
    await call.answer()

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def payment_success(message: Message):
    payload = message.successful_payment.invoice_payload
    screen = "test&tier=basic" if payload == "test_basic_299" else "test&tier=deep"
    await message.answer(
        "Оплата прошла! Открывай тест:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🧭 Начать тест",
                web_app=WebAppInfo(url=f"{MINIAPP_URL}?screen={screen}&paid=1"),
            )
        ]]),
    )


async def _run_bot():
    await bot.delete_webhook(drop_pending_updates=True)
    print("Bot started")
    await dp.start_polling(bot)


# ── DB ─────────────────────────────────────────────────────────────────────

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


# ── FastAPI ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    task = asyncio.create_task(_run_bot())
    yield
    task.cancel()
    await bot.session.close()

app = FastAPI(title="Попробуй API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


class SubmitAnswers(BaseModel):
    tg_user_id: int
    test_id: str
    answers: dict

class ResultResponse(BaseModel):
    result_id: int
    scores: dict
    top_types: list
    recommendations: list


PROFESSIONS = {
    "Инноватор":      ["Продакт-менеджер", "UX-дизайнер", "Разработчик", "Стартапер"],
    "Специалист":     ["Программист", "Аналитик данных", "Юрист", "Врач", "Инженер"],
    "Аналитик":       ["Data Analyst", "Финансист", "Экономист", "Исследователь"],
    "Коммуникатор":   ["PR-менеджер", "Маркетолог", "Журналист", "Педагог", "Психолог"],
    "Менеджер":       ["Руководитель проекта", "Операционный директор"],
    "Предприниматель":["Основатель стартапа", "Франчайзи", "Продюсер"],
}

def _get_recommendations(top_types):
    result = []
    for t in top_types:
        result.extend(PROFESSIONS.get(t, []))
    return result[:10]


@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/submit", response_model=ResultResponse)
async def submit_answers(payload: SubmitAnswers):
    scores = score_test(payload.test_id, payload.answers)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO results (tg_user_id, test_id, answers, scores) VALUES (?, ?, ?, ?)",
            (payload.tg_user_id, payload.test_id,
             json.dumps(payload.answers, ensure_ascii=False),
             json.dumps(scores, ensure_ascii=False)),
        )
        result_id = cursor.lastrowid
        await db.commit()
    top_types = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]
    recommendations = _get_recommendations([t for t, _ in top_types])
    return ResultResponse(result_id=result_id, scores=scores,
                          top_types=[t for t, _ in top_types],
                          recommendations=recommendations)

@app.get("/results/{tg_user_id}")
async def get_results(tg_user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM results WHERE tg_user_id = ? ORDER BY created_at DESC",
            (tg_user_id,),
        )
        rows = await cursor.fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="No results found")
    return [dict(r) for r in rows]

@app.get("/pdf/{result_id}")
async def download_pdf(result_id: int, tg_user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM results WHERE id = ? AND tg_user_id = ?",
            (result_id, tg_user_id),
        )
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Result not found")
    scores = json.loads(row["scores"])
    path = await generate_pdf(result_id, tg_user_id, scores)
    return FileResponse(path, media_type="application/pdf",
                        filename=f"poprobui_report_{result_id}.pdf")

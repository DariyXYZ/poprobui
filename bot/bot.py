import asyncio
import json
import os
import sys
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    WebAppInfo, LabeledPrice, PreCheckoutQuery,
    FSInputFile,
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.storage.memory import MemoryStorage

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'api'))

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MINIAPP_URL = os.getenv("MINIAPP_URL", "https://poprobui.railway.app")
YOOKASSA_PROVIDER_TOKEN = os.getenv("YOOKASSA_PROVIDER_TOKEN", "")

# Bump this whenever miniapp/index.html changes — Telegram's in-app WebView
# caches aggressively by exact URL, so a stale query string means users
# keep seeing an old build after a redeploy. Cheap, reliable cache-bust.
MINIAPP_VERSION = "9"


def miniapp_url(extra: str = "") -> str:
    sep = "&" if extra else ""
    return f"{MINIAPP_URL}?v={MINIAPP_VERSION}{sep}{extra}"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ── Keyboards ──────────────────────────────────────────────────────────────

def kb_main() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧭 Пройти тест", web_app=WebAppInfo(url=miniapp_url()))],
            [
                KeyboardButton(text="💳 Баланс"),
                KeyboardButton(text="📊 Результаты", web_app=WebAppInfo(url=miniapp_url("screen=results"))),
                KeyboardButton(text="ℹ️ Как работает"),
            ],
        ],
        resize_keyboard=True,
    )


def kb_topup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="100 ₽ — Экспресс (ДДО · 10 мин)", callback_data="pay_100")],
        [InlineKeyboardButton(text="200 ₽ — Стандарт (Склонности · 12 мин)", callback_data="pay_200")],
        [InlineKeyboardButton(text="300 ₽ — Полный (RIASEC · 20 мин)", callback_data="pay_300")],
        [InlineKeyboardButton(text="← Назад", callback_data="back_main")],
    ])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data="back_main")]
    ])


# ── Handlers ───────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message):
    name = message.from_user.first_name or "друг"
    await message.answer(
        f"Привет, {name}! 👋\n\n"
        "Я помогу тебе понять — <b>кем стать</b> и куда двигаться.\n\n"
        "«Попробуй» — профориентационный тест для школьников и студентов. "
        "15 минут — и ты получишь честный разбор интересов, способностей и профессий.\n\n"
        "Нажми кнопку внизу чтобы начать 👇",
        parse_mode="HTML",
        reply_markup=kb_main()
    )


@dp.message(F.web_app_data)
async def handle_web_app_data(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
    except Exception:
        return
    if data.get("action") != "generate_pdf":
        return
    scores = data.get("scores")
    if not scores:
        return
    test_id = data.get("test_id", "ddo")
    processing = await message.answer("⏳ Генерируем PDF...")
    try:
        from pdf import generate_pdf
        pdf_path = await generate_pdf(message.message_id, message.from_user.id, scores, test_id)
        await bot.send_document(
            message.chat.id,
            FSInputFile(pdf_path, filename="poprobui_report.pdf"),
            caption="📄 Твой профориентационный отчёт · @poprobui_bot",
        )
        await processing.delete()
    except Exception as e:
        import traceback
        traceback.print_exc()
        await processing.edit_text(
            f"⚠️ Не удалось сгенерировать PDF.\n\nПопробуй ещё раз — нажми кнопку «📊 Результаты» внизу и затем «📨 Получить PDF в Telegram».\n\n<code>{type(e).__name__}: {e}</code>",
            parse_mode="HTML"
        )


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer("Меню:", reply_markup=kb_main())


@dp.message(F.text == "ℹ️ Как работает")
async def handle_how(message: Message):
    await message.answer(
        "📖 <b>Как работает Попробуй</b>\n\n"
        "1. Нажми «🧭 Пройти тест» — мини-апп откроется прямо в Telegram\n\n"
        "2. Выбери тариф и ответь на вопросы — от 10 до 20 минут\n\n"
        "3. Нажми «📨 Получить PDF» — отчёт придёт в этот чат\n\n"
        "4. Поделись с родителями или учителем\n\n"
        "<b>Экспресс</b> — 100 ₽ · ДДО Климова · 20 вопросов · 5 типов профессий\n"
        "<b>Стандарт</b> — 200 ₽ · Склонности · 24 вопроса · 6 склонностей + зарплаты\n"
        "<b>Полный</b> — 300 ₽ · RIASEC O*NET · 60 вопросов · международный профиль + 10 профессий",
        parse_mode="HTML",
    )


@dp.message(F.text == "💳 Баланс")
async def handle_balance(message: Message):
    await message.answer(
        "💳 <b>Выбери тариф</b>\n\n"
        "После оплаты тест откроется автоматически.",
        parse_mode="HTML",
        reply_markup=kb_topup()
    )


@dp.callback_query(F.data == "pay_100")
async def cb_pay_100(call: CallbackQuery):
    if not YOOKASSA_PROVIDER_TOKEN:
        await call.answer("Платежи скоро будут подключены!", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title="Экспресс «Попробуй»",
        description="ДДО Климова — 20 вопросов · 5 типов профессий · PDF-отчёт",
        payload="test_express_100",
        provider_token=YOOKASSA_PROVIDER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label="Экспресс-тест", amount=10000)],
        start_parameter="pay_express",
    )
    await call.answer()


@dp.callback_query(F.data == "pay_200")
async def cb_pay_200(call: CallbackQuery):
    if not YOOKASSA_PROVIDER_TOKEN:
        await call.answer("Платежи скоро будут подключены!", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title="Стандарт «Попробуй»",
        description="Склонности Йовайши — 24 вопроса · 6 склонностей · зарплаты · PDF",
        payload="test_standard_200",
        provider_token=YOOKASSA_PROVIDER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label="Стандартный тест", amount=20000)],
        start_parameter="pay_standard",
    )
    await call.answer()


@dp.callback_query(F.data == "pay_300")
async def cb_pay_300(call: CallbackQuery):
    if not YOOKASSA_PROVIDER_TOKEN:
        await call.answer("Платежи скоро будут подключены!", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title="Полный тест «Попробуй»",
        description="O*NET RIASEC — 60 вопросов · международный профиль · ТОП-10 профессий · PDF",
        payload="test_full_300",
        provider_token=YOOKASSA_PROVIDER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label="Полный тест", amount=30000)],
        start_parameter="pay_full",
    )
    await call.answer()


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def payment_success(message: Message):
    payload = message.successful_payment.invoice_payload
    if payload == "test_express_100":
        screen = "test&tier=ddo"
    elif payload == "test_standard_200":
        screen = "test&tier=yovayshi"
    else:
        screen = "test&tier=onett"
    await message.answer(
        "✅ Оплата прошла! Открывай тест:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🧭 Начать тест",
                web_app=WebAppInfo(url=miniapp_url(f"screen={screen}&paid=1"))
            )
        ]])
    )


# ── Main ───────────────────────────────────────────────────────────────────

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    print("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

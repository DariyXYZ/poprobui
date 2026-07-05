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

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ── Keyboards ──────────────────────────────────────────────────────────────

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧭 Пройти тест",
                              web_app=WebAppInfo(url=MINIAPP_URL))],
        [InlineKeyboardButton(text="📊 Мои результаты",
                              web_app=WebAppInfo(url=f"{MINIAPP_URL}?screen=results"))],
        [InlineKeyboardButton(text="💳 Пополнить кошелёк", callback_data="topup")],
        [InlineKeyboardButton(text="ℹ️ Как это работает",  callback_data="how_it_works")],
    ])

def kb_test() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🧭 Пройти тест", web_app=WebAppInfo(url=MINIAPP_URL))]],
        resize_keyboard=True,
    )


def kb_topup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="299 ₽ — Базовый тест", callback_data="pay_299"),
            InlineKeyboardButton(text="990 ₽ — Глубокий анализ", callback_data="pay_990"),
        ],
        [
            InlineKeyboardButton(text="← Назад", callback_data="back_main")
        ],
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
        reply_markup=kb_test()
    )
    await message.answer("Другие опции:", reply_markup=kb_main())


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
    processing = await message.answer("⏳ Генерируем PDF...")
    try:
        from pdf import generate_pdf
        pdf_path = await generate_pdf(message.message_id, message.from_user.id, scores)
        await bot.send_document(
            message.chat.id,
            FSInputFile(pdf_path, filename="poprobui_report.pdf"),
            caption="📄 Твой профориентационный отчёт · @poprobui_bot",
        )
        await processing.delete()
    except Exception as e:
        await processing.edit_text(f"Ошибка генерации PDF: {e}")


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer("Опции:", reply_markup=kb_main())


@dp.callback_query(F.data == "back_main")
async def cb_back_main(call: CallbackQuery):
    await call.message.edit_text("Главное меню:", reply_markup=kb_main())
    await call.answer()


@dp.callback_query(F.data == "how_it_works")
async def cb_how_it_works(call: CallbackQuery):
    await call.message.edit_text(
        "📖 <b>Как работает Попробуй</b>\n\n"
        "1. Ты проходишь тест прямо здесь в Telegram — удобный интерфейс, никаких сторонних сайтов\n\n"
        "2. Вопросы проверяют твои интересы, склонности и способности — честно, без воды\n\n"
        "3. Результат — развёрнутый отчёт: твой профиль, подходящие профессии, "
        "данные о рынке труда (зарплаты, спрос)\n\n"
        "4. Отчёт можно сохранить в PDF и поделиться с родителями или учителем\n\n"
        "<b>Базовый тест</b> — 299 ₽\n"
        "<b>Глубокий анализ</b> — 990 ₽ (+ живой разбор с экспертом)",
        parse_mode="HTML",
        reply_markup=kb_back()
    )
    await call.answer()


@dp.callback_query(F.data == "topup")
async def cb_topup(call: CallbackQuery):
    await call.message.edit_text(
        "💳 <b>Выбери тариф</b>\n\n"
        "После оплаты тест откроется автоматически.",
        parse_mode="HTML",
        reply_markup=kb_topup()
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
        description="Профориентационный тест + PDF-отчёт с профилем и профессиями",
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
    if payload == "test_basic_299":
        screen = "test&tier=basic"
    else:
        screen = "test&tier=deep"
    await message.answer(
        "✅ Оплата прошла! Открывай тест:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🧭 Начать тест",
                web_app=WebAppInfo(url=f"{MINIAPP_URL}?screen={screen}&paid=1")
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

import asyncio
import json
import logging
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MINIAPP_URL = os.getenv("MINIAPP_URL", "https://poprobui.railway.app")
YOOKASSA_PROVIDER_TOKEN = os.getenv("YOOKASSA_PROVIDER_TOKEN", "")

# Bump this whenever miniapp/index.html changes — Telegram's in-app WebView
# caches aggressively by exact URL, so a stale query string means users
# keep seeing an old build after a redeploy. Cheap, reliable cache-bust.
MINIAPP_VERSION = "11"


def miniapp_url(extra: str = "") -> str:
    sep = "&" if extra else ""
    return f"{MINIAPP_URL}?v={MINIAPP_VERSION}{sep}{extra}"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ── Pricing ──────────────────────────────────────────────────────────────
# ddo has no entry — it's free, no invoice needed. Prices are in kopecks.
TEST_INVOICES = {
    "yovayshi": {
        "title": "Стандарт «Попробуй»",
        "description": "Склонности Йовайши — 24 вопроса · 6 склонностей · зарплаты · PDF",
        "amount": 30000,  # 300 ₽
    },
    "onett": {
        "title": "Полный тест «Попробуй»",
        "description": "O*NET RIASEC — 60 вопросов · международный профиль · ТОП-10 профессий · PDF",
        "amount": 50000,  # 500 ₽
    },
}


async def send_test_invoice(chat_id: int, test_id: str):
    """Sends a Telegram payment invoice for a paid test. No-op for the free tier."""
    info = TEST_INVOICES.get(test_id)
    if not info:
        return
    if not YOOKASSA_PROVIDER_TOKEN:
        await bot.send_message(chat_id, "Платежи скоро будут подключены!")
        return
    # provider_data receipt is REQUIRED by YooKassa when auto-receipts
    # (Мой налог / онлайн-касса) are enabled — without it the payment is
    # rejected at creation. Note the unit mismatch is intentional per
    # YooKassa docs: prices[] is in kopecks, receipt value is in rubles.
    # need_email + send_email_to_provider: YooKassa needs the payer's
    # email/phone to deliver the receipt.
    receipt = {
        "receipt": {
            "items": [{
                "description": info["title"],
                "quantity": "1.00",
                "amount": {
                    "value": f"{info['amount'] / 100:.2f}",
                    "currency": "RUB",
                },
                "vat_code": 1,
                "payment_mode": "full_payment",
                "payment_subject": "service",
            }]
        }
    }
    try:
        await bot.send_invoice(
            chat_id=chat_id,
            title=info["title"],
            description=info["description"],
            payload=f"test_{test_id}",
            provider_token=YOOKASSA_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=info["title"], amount=info["amount"])],
            start_parameter=f"pay_{test_id}",
            need_email=True,
            send_email_to_provider=True,
            provider_data=json.dumps(receipt),
        )
    except Exception as e:
        logger.exception("Failed to send invoice for test_id=%s", test_id)
        await bot.send_message(
            chat_id,
            "⚠️ Не удалось создать счёт ЮKassa.\n\n"
            f"<code>{type(e).__name__}: {e}</code>",
            parse_mode="HTML",
        )


# ── Keyboards ──────────────────────────────────────────────────────────────

def kb_main(test_url_extra: str = "") -> ReplyKeyboardMarkup:
    # test_url_extra lets the primary button carry state (e.g. after payment,
    # "screen=test&tier=X&paid=1") while staying a ReplyKeyboardMarkup button —
    # sendData() only works for Mini Apps opened this way, not via inline
    # buttons, so every entry point into a test must go through this keyboard.
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧭 Пройти тест", web_app=WebAppInfo(url=miniapp_url(test_url_extra)))],
            [
                KeyboardButton(text="💳 Баланс"),
                KeyboardButton(text="📊 Результаты", web_app=WebAppInfo(url=miniapp_url("screen=results"))),
                KeyboardButton(text="ℹ️ Как работает"),
            ],
        ],
        resize_keyboard=True,
    )


def kb_topup() -> InlineKeyboardMarkup:
    # Note: no web_app button here for the free tier on purpose — sendData()
    # (used for both PDF delivery and paid-tier invoice requests) only works
    # when the Mini App was opened via the ReplyKeyboardMarkup button, not an
    # inline one. Keep every entry into the test flow going through kb_main().
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧭 Экспресс — бесплатно, кнопкой «Пройти тест»", callback_data="hint_free")],
        [InlineKeyboardButton(text="300 ₽ — Стандарт (Склонности · 12 мин)", callback_data="pay_yovayshi")],
        [InlineKeyboardButton(text="500 ₽ — Полный (RIASEC · 20 мин)", callback_data="pay_onett")],
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
    action = data.get("action")

    if action == "request_invoice":
        test_id = data.get("test_id")
        if test_id:
            await send_test_invoice(message.chat.id, test_id)
        return

    if action != "generate_pdf":
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
        "<b>Экспресс</b> — бесплатно · ДДО Климова · 20 вопросов · 5 типов профессий\n"
        "<b>Стандарт</b> — 300 ₽ · Склонности · 24 вопроса · 6 склонностей + зарплаты\n"
        "<b>Полный</b> — 500 ₽ · RIASEC O*NET · 60 вопросов · международный профиль + 10 профессий",
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


@dp.callback_query(F.data == "hint_free")
async def cb_hint_free(call: CallbackQuery):
    await call.answer("Открой мини-апп кнопкой «🧭 Пройти тест» внизу чата — Экспресс уже бесплатный", show_alert=True)


@dp.callback_query(F.data == "back_main")
async def cb_back_main(call: CallbackQuery):
    await call.message.delete()
    await call.answer()


@dp.callback_query(F.data.startswith("pay_"))
async def cb_pay(call: CallbackQuery):
    test_id = call.data.split("_", 1)[1]
    if not YOOKASSA_PROVIDER_TOKEN:
        await call.answer("Платежи скоро будут подключены!", show_alert=True)
        return
    await send_test_invoice(call.from_user.id, test_id)
    await call.answer()


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    logger.info("Pre-checkout query received: id=%s payload=%s currency=%s total=%s",
                query.id, query.invoice_payload, query.currency, query.total_amount)
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def payment_success(message: Message):
    payload = message.successful_payment.invoice_payload
    test_id = payload.replace("test_", "", 1)
    logger.info("Successful payment: payload=%s currency=%s total=%s provider_charge_id=%s telegram_charge_id=%s",
                payload,
                message.successful_payment.currency,
                message.successful_payment.total_amount,
                message.successful_payment.provider_payment_charge_id,
                message.successful_payment.telegram_payment_charge_id)
    await message.answer(
        "✅ Оплата прошла! Жми «🧭 Пройти тест» внизу — тест откроется сразу.",
        reply_markup=kb_main(f"screen=test&tier={test_id}&paid=1"),
    )


# ── Main ───────────────────────────────────────────────────────────────────

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    provider_mode = "unset"
    if ":TEST:" in YOOKASSA_PROVIDER_TOKEN:
        provider_mode = "TEST"
    elif ":LIVE:" in YOOKASSA_PROVIDER_TOKEN:
        provider_mode = "LIVE"
    logger.info("Bot started. YooKassa provider token mode=%s", provider_mode)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

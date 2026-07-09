import asyncio
import json
import logging
import os
import re
import sys
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    WebAppInfo, LabeledPrice, PreCheckoutQuery, BotCommand,
    FSInputFile,
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.storage.memory import MemoryStorage

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'api'))
from wallet import add_balance, balance_of, debit_balance, format_money

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MINIAPP_URL = os.getenv("MINIAPP_URL", "https://poprobui.railway.app")
YOOKASSA_PROVIDER_TOKEN = os.getenv("YOOKASSA_PROVIDER_TOKEN", "")
GIFT_USERNAMES = {
    u.strip().lower().lstrip("@")
    for u in os.getenv("GIFT_USERNAMES", "dariy_nazarov").split(",")
    if u.strip()
}
GIFT_USER_IDS = {
    int(u.strip())
    for u in os.getenv("GIFT_USER_IDS", "").split(",")
    if u.strip().isdigit()
}
# Bump this whenever miniapp/index.html changes — Telegram's in-app WebView
# caches aggressively by exact URL, so a stale query string means users
# keep seeing an old build after a redeploy. Cheap, reliable cache-bust.
MINIAPP_VERSION = "20"


def miniapp_url(extra: str = "") -> str:
    sep = "&" if extra else ""
    return f"{MINIAPP_URL}?v={MINIAPP_VERSION}{sep}{extra}"


def wallet_extra(user_id: int | None, extra: str = "") -> str:
    parts = []
    if extra:
        parts.append(extra)
    if user_id is not None:
        parts.append(f"bal={balance_of(user_id)}")
    return "&".join(parts)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
AWAITING_TOPUP_USERS: set[int] = set()

BOT_COMMANDS = [
    BotCommand(command="start", description="Открыть меню и начать заново"),
    BotCommand(command="menu", description="Показать кнопки мини-аппа"),
    BotCommand(command="test", description="Пройти тест"),
    BotCommand(command="reset", description="Сбросить тест на этом устройстве"),
    BotCommand(command="balance", description="Баланс и пополнение"),
    BotCommand(command="help", description="Как работает Попробуй"),
    BotCommand(command="privacy", description="Согласие и политика данных"),
]


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
TOPUP_AMOUNTS = [30000, 50000, 100000]


def parse_money_input(text: str) -> int | None:
    cleaned = (text or "").lower().replace(",", ".")
    cleaned = re.sub(r"[^0-9.]", "", cleaned)
    if not cleaned:
        return None
    try:
        return int(round(float(cleaned) * 100))
    except ValueError:
        return None


def has_gift_access(message: Message) -> bool:
    username = (message.from_user.username or "").lower()
    return message.from_user.id in GIFT_USER_IDS or username in GIFT_USERNAMES


async def open_paid_test_from_balance(message: Message, test_id: str) -> bool:
    info = TEST_INVOICES.get(test_id)
    if not info:
        return False
    rest = debit_balance(message.from_user.id, info["amount"])
    if rest is None:
        return False
    await message.answer(
        f"Списал {format_money(info['amount'])} со счета.\n"
        f"Баланс: <b>{format_money(rest)}</b>.\n\n"
        "Жми «🧭 Пройти тест» — платная версия откроется сразу.",
        parse_mode="HTML",
        reply_markup=kb_main(f"screen=test&tier={test_id}&paid=1", message.from_user.id),
    )
    return True


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


async def send_topup_invoice(chat_id: int, amount: int):
    if amount < 10000 or amount > 1500000:
        await bot.send_message(chat_id, "Сумма пополнения: от 100 ₽ до 15 000 ₽.")
        return
    if not YOOKASSA_PROVIDER_TOKEN:
        await bot.send_message(chat_id, "Платежи скоро будут подключены!")
        return

    receipt = {
        "receipt": {
            "items": [{
                "description": "Пополнение баланса Попробуй",
                "quantity": "1.00",
                "amount": {
                    "value": f"{amount / 100:.2f}",
                    "currency": "RUB",
                },
                "vat_code": 1,
                "payment_mode": "advance",
                "payment_subject": "payment",
            }]
        }
    }
    try:
        await bot.send_invoice(
            chat_id=chat_id,
            title="Пополнение баланса",
            description=f"Баланс Попробуй: {format_money(amount)}",
            payload=f"topup_{amount}",
            provider_token=YOOKASSA_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label="Пополнение баланса", amount=amount)],
            start_parameter=f"topup_{amount}",
            need_email=True,
            send_email_to_provider=True,
            provider_data=json.dumps(receipt),
        )
    except Exception as e:
        logger.exception("Failed to send topup invoice for amount=%s", amount)
        await bot.send_message(
            chat_id,
            "⚠️ Не удалось создать счёт ЮKassa.\n\n"
            f"<code>{type(e).__name__}: {e}</code>",
            parse_mode="HTML",
        )


# ── Keyboards ──────────────────────────────────────────────────────────────

def kb_main(test_url_extra: str = "", user_id: int | None = None) -> ReplyKeyboardMarkup:
    # test_url_extra lets the primary button carry state (e.g. after payment,
    # "screen=test&tier=X&paid=1") while staying a ReplyKeyboardMarkup button —
    # sendData() only works for Mini Apps opened this way, not via inline
    # buttons, so every entry point into a test must go through this keyboard.
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧭 Пройти тест", web_app=WebAppInfo(url=miniapp_url(wallet_extra(user_id, test_url_extra))))],
            [
                KeyboardButton(text="💳 Баланс"),
                KeyboardButton(text="📊 Результаты", web_app=WebAppInfo(url=miniapp_url(wallet_extra(user_id, "screen=results")))),
                KeyboardButton(text="ℹ️ Как работает"),
            ],
        ],
        resize_keyboard=True,
    )


def kb_topup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="+300 ₽", callback_data="topup_30000"),
            InlineKeyboardButton(text="+500 ₽", callback_data="topup_50000"),
            InlineKeyboardButton(text="+1000 ₽", callback_data="topup_100000"),
        ],
        [InlineKeyboardButton(text="Ввести другую сумму", callback_data="topup_custom")],
        [InlineKeyboardButton(text="Открыть тесты", callback_data="hint_free")],
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
        reply_markup=kb_main(user_id=message.from_user.id)
    )


@dp.message(F.web_app_data)
async def handle_web_app_data(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
    except Exception:
        return
    action = data.get("action")

    if action in {"request_purchase", "request_invoice"}:
        test_id = data.get("test_id")
        consent = data.get("consent") or {}
        logger.info("Purchase requested: test_id=%s consent_version=%s consent_at=%s",
                    test_id, consent.get("version"), consent.get("accepted_at"))
        if test_id:
            paid_from_balance = await open_paid_test_from_balance(message, test_id)
            if not paid_from_balance:
                info = TEST_INVOICES.get(test_id)
                balance = balance_of(message.from_user.id)
                need = info["amount"] if info else 0
                await message.answer(
                    f"На балансе {format_money(balance)}. "
                    f"Для этого теста нужно {format_money(need)}.\n\n"
                    "Пополни счет и нажми тест еще раз.",
                    reply_markup=kb_topup(),
                )
        return

    if action != "generate_pdf":
        return
    scores = data.get("scores")
    if not scores:
        return
    test_id = data.get("test_id", "ddo")
    consent = data.get("consent") or {}
    logger.info("PDF requested: test_id=%s consent_version=%s consent_at=%s",
                test_id, consent.get("version"), consent.get("accepted_at"))
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


@dp.message(Command("menu", "test"))
async def cmd_menu(message: Message):
    await message.answer(
        "Меню открыто. Если кнопки пропали, используй /menu или /balance.",
        reply_markup=kb_main(user_id=message.from_user.id),
    )


@dp.message(Command("reset"))
async def cmd_reset(message: Message):
    await message.answer(
        "Готово. Открой кнопку «🧭 Пройти тест» ниже — мини-апп очистит локальную карту, историю и согласие на этом устройстве.",
        reply_markup=kb_main("reset=1", message.from_user.id),
    )


@dp.message(Command("gift"))
async def cmd_gift(message: Message):
    if not has_gift_access(message):
        return
    balance = add_balance(message.from_user.id, 100000)
    await message.answer(
        f"Баланс пополнен на 1000 ₽.\n"
        f"На счете: <b>{format_money(balance)}</b>.",
        parse_mode="HTML",
    )


@dp.message(Command("topup"))
async def cmd_topup(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Напиши сумму пополнения так: /topup 750")
        return
    amount = parse_money_input(parts[1])
    if amount is None:
        await message.answer("Не понял сумму. Пример: /topup 750")
        return
    await send_topup_invoice(message.chat.id, amount)


@dp.message(lambda message: message.from_user and message.from_user.id in AWAITING_TOPUP_USERS)
async def handle_custom_topup_amount(message: Message):
    amount = parse_money_input(message.text or "")
    if amount is None:
        await message.answer("Напиши только сумму числом, например: 750")
        return
    AWAITING_TOPUP_USERS.discard(message.from_user.id)
    await send_topup_invoice(message.chat.id, amount)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await send_how_it_works(message)


@dp.message(Command("privacy"))
async def cmd_privacy(message: Message):
    base_url = MINIAPP_URL.rstrip("/")
    await message.answer(
        "Документы по персональным данным:\n\n"
        f"Согласие: {base_url}/consent.html\n"
        f"Политика: {base_url}/privacy.html",
        reply_markup=kb_main(user_id=message.from_user.id),
    )


async def send_how_it_works(message: Message):
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


@dp.message(F.text == "ℹ️ Как работает")
async def handle_how(message: Message):
    await send_how_it_works(message)


@dp.message(Command("balance"))
async def cmd_balance(message: Message):
    await send_balance_menu(message)


async def send_balance_menu(message: Message):
    balance = balance_of(message.from_user.id)
    await message.answer(
        "💳 <b>Личный кабинет</b>\n\n"
        f"Баланс: <b>{format_money(balance)}</b>\n\n"
        "Пополняй счет через ЮKassa. Тест выбирается в мини-аппе, а стоимость списывается с баланса при старте.",
        parse_mode="HTML",
        reply_markup=kb_topup()
    )


@dp.message(F.text == "💳 Баланс")
async def handle_balance(message: Message):
    await send_balance_menu(message)


@dp.callback_query(F.data == "hint_free")
async def cb_hint_free(call: CallbackQuery):
    await call.message.answer(
        "Открой мини-апп кнопкой «🧭 Пройти тест» внизу чата и выбери нужный тест.",
        reply_markup=kb_main(user_id=call.from_user.id),
    )
    await call.answer()


@dp.callback_query(F.data == "back_main")
async def cb_back_main(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        logger.exception("Failed to delete balance menu message")
    await call.message.answer("Вернул основное меню.", reply_markup=kb_main(user_id=call.from_user.id))
    await call.answer()


@dp.callback_query(F.data.startswith("pay_"))
async def cb_pay(call: CallbackQuery):
    await call.message.answer(
        "Теперь тесты выбираются в мини-аппе, а списание происходит при старте.",
        reply_markup=kb_main(user_id=call.from_user.id),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("topup_"))
async def cb_topup(call: CallbackQuery):
    if call.data == "topup_custom":
        AWAITING_TOPUP_USERS.add(call.from_user.id)
        await call.message.answer("Напиши сумму пополнения одним числом, например: 750")
        await call.answer()
        return
    try:
        amount = int(call.data.split("_", 1)[1])
    except (IndexError, ValueError):
        await call.answer("Не понял сумму", show_alert=True)
        return
    await send_topup_invoice(call.from_user.id, amount)
    await call.answer()


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    logger.info("Pre-checkout query received: id=%s payload=%s currency=%s total=%s",
                query.id, query.invoice_payload, query.currency, query.total_amount)
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def payment_success(message: Message):
    payload = message.successful_payment.invoice_payload
    logger.info("Successful payment: payload=%s currency=%s total=%s provider_charge_id=%s telegram_charge_id=%s",
                payload,
                message.successful_payment.currency,
                message.successful_payment.total_amount,
                message.successful_payment.provider_payment_charge_id,
                message.successful_payment.telegram_payment_charge_id)
    if payload.startswith("topup_"):
        try:
            amount = int(payload.split("_", 1)[1])
        except (IndexError, ValueError):
            amount = message.successful_payment.total_amount
        balance = add_balance(message.from_user.id, amount)
        await message.answer(
            "✅ Баланс пополнен.\n\n"
            f"На счете: <b>{format_money(balance)}</b>.\n"
            "Теперь выбери тест в личном кабинете.",
            parse_mode="HTML",
            reply_markup=kb_main(user_id=message.from_user.id),
        )
        return

    test_id = payload.replace("test_", "", 1)
    await message.answer(
        "✅ Оплата прошла! Жми «🧭 Пройти тест» внизу — тест откроется сразу.",
        reply_markup=kb_main(f"screen=test&tier={test_id}&paid=1", message.from_user.id),
    )


# ── Main ───────────────────────────────────────────────────────────────────

async def main():
    await bot.set_my_commands(BOT_COMMANDS)
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

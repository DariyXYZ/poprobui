# Попробуй — Профориентационный бот

Telegram Mini App для профориентации школьников и студентов.

## Структура

```
poprobui/
├── bot/              # Telegram бот (aiogram 3)
│   ├── bot.py
│   ├── wallet_client.py  — HTTP-клиент к кошельку/PDF в api (bot не хранит состояние)
│   ├── .env
│   └── requirements.txt
├── api/              # FastAPI бэкенд — единственный владелец БД, кошелька и PDF
│   ├── main.py           — роуты
│   ├── telegram_auth.py  — проверка Telegram initData (HMAC) + internal-token auth
│   ├── scoring.py        — скоринг тестов
│   ├── pdf.py            — генерация PDF
│   ├── wallet.py         — баланс (JSON-файл + FileLock), только для api
│   ├── fonts/            — DejaVu Sans (кириллица в PDF, лицензия в fonts/)
│   └── requirements.txt
└── miniapp/          # Telegram Mini App (single-file HTML), деплоится на GitHub Pages
    └── index.html
```

bot и api — два разных процесса. Сейчас оба крутятся локально на одной
машине (см. «Деплой» ниже), но bot всё равно никогда не трогает кошелёк или
БД напрямую — только через HTTP-эндпоинты api, авторизуясь общим
`INTERNAL_API_TOKEN`. Если когда-нибудь разъедутся на разные машины/контейнеры,
это уже не сломает баланс молча.

## Авторизация API

Каждый вызов `/wallet/*`, `/submit`, `/results/*`, `/pdf/*` требует один из:
- `X-Telegram-Init-Data` — сырой `tg.initData` из миниаппа, api проверяет HMAC-подпись
  и достаёт настоящий `tg_user_id` из неё (клиентскому `tg_user_id` в теле/пути не доверяет)
- `X-Internal-Token` — для вызовов от самого bot-сервиса (Telegram уже
  аутентифицировал пользователя на уровне Bot API, второй раз проверять нечем)

`/wallet/credit` и `/pdf/generate` принимают только `X-Internal-Token` — их не
может дёрнуть браузер ни при каких обстоятельствах.

## Деплой — всё локально, без облачного хостинга

bot и api оба крутятся на одном личном компьютере, 24/7, пока комп включён и
онлайн. Никакого Railway/Fly/VPS — сознательный выбор, чтобы не платить за
хостинг. Единственное, что реально в облаке — статика `miniapp/` на GitHub
Pages (бесплатно, GitHub же и раздаёт).

Из этого следует одно жёсткое ограничение: **если комп спит, выключен или без
сети — весь бот недоступен для всех пользователей одновременно**, включая тех,
кто в этот момент платит через ЮKassa. Это осознанный компромисс ради нулевой
стоимости инфраструктуры, не забытый баг.

### Почему нужен туннель

`miniapp/index.html` — статика на GitHub Pages, открывается в браузере/Telegram
у любого пользователя где угодно в мире. Ей нужен HTTPS-адрес api, доступный
из интернета — `http://localhost:8000` снаружи компа не виден. bot сам эту
проблему не имеет (он ходит НАРУЖУ к Telegram, входящих подключений от
миниаппа ему не нужно — общается с api через `http://localhost:8000` напрямую).

**Туннель нужен ТОЛЬКО ради api**, и он обязан быть на статичном домене —
рандомный адрес (как было с `lhr.life`) означает переписывать `API_URL` в
`miniapp/index.html` и передеплоивать GitHub Pages при каждом перезапуске
туннеля. Бесплатный вариант со статичным доменом: **ngrok** (free-план даёт
один reserved static domain, без карты и подписки).

### Настройка (сделано 2026-07-15)

Готово: аккаунт на ngrok.com, authtoken прописан локально
(`ngrok config add-authtoken ...`), бесплатный статичный домен уже выдан
аккаунтом — `bootlace-upcoming-glimmer.ngrok-free.dev`. Он прописан в
`miniapp/index.html` → `API_URL`. Меняется только если пересоздать домен в
dashboard.ngrok.com → Domains — тогда поправить и здесь, и в `API_URL`.

Осталось прописать `ALLOWED_ORIGINS` в `api/.env` — origin миниаппа (GitHub
Pages URL).

### Запуск (каждый раз)

```bash
# 1. API
cd api && uvicorn main:app --host 0.0.0.0 --port 8000

# 2. Туннель на статичный домен (другой терминал)
ngrok http --url=https://bootlace-upcoming-glimmer.ngrok-free.dev 8000

# 3. Bot (третий терминал) — ходит к api локально, туннель ему не нужен
cd bot && python bot.py
```

Все три процесса должны быть живы одновременно, пока бот в работе.

### Первичная настройка окружения

```bash
# API
cd api && pip install -r requirements.txt && cp .env.example .env  # заполнить BOT_TOKEN, INTERNAL_API_TOKEN

# Bot
cd bot && pip install -r requirements.txt && cp .env.example .env  # тот же INTERNAL_API_TOKEN
```

`BOT_TOKEN` нужен теперь и в `api/.env` (для проверки initData), не только в
`bot/.env`. `INTERNAL_API_TOKEN` — общий секрет, сгенерировать один раз
(`openssl rand -hex 32`) и вписать одинаковое значение в оба `.env`.

Подключить ЮКассу: `YOOKASSA_PROVIDER_TOKEN` из BotFather → Bot Settings →
Payments → ЮКасса → в `bot/.env`.

## Тесты в Mini App

| test_id    | Название                    | Вопросов | Шкалы |
|------------|-----------------------------|----------|-------|
| ddo        | ДДО Климова                 | 20       | 5     |
| yovayshi   | Йовайши/Резапкина           | 24       | 6     |
| onett      | O*NET RIASEC                | 60       | 6     |
| golomshtok | Карта интересов Голомштока  | 174      | 29    |

## ЮКасса

Пока токен не заполнен — кнопки оплаты показывают заглушку.
После подключения: BotFather → выбрать бота → Payments → ЮКасса → вставить токен в `.env`.

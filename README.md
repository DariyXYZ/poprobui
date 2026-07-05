# Попробуй — Профориентационный бот

Telegram Mini App для профориентации школьников и студентов.

## Структура

```
poprobui/
├── bot/          # Telegram бот (aiogram 3)
│   ├── bot.py
│   ├── .env
│   └── requirements.txt
├── api/          # FastAPI бэкенд
│   ├── main.py       — роуты
│   ├── scoring.py    — скоринг тестов
│   ├── pdf.py        — генерация PDF
│   ├── templates/    — Jinja2 шаблон отчёта
│   └── requirements.txt
└── miniapp/      # Telegram Mini App (single-file HTML)
    └── index.html
```

## Деплой (Railway)

1. Создать два сервиса в Railway: **api** и **bot**
2. Для **api**: root dir = `api/`, start = `uvicorn main:app --host 0.0.0.0 --port $PORT`
3. Для **bot**: root dir = `bot/`, start = `python bot.py`
4. Mini App: задеплоить `miniapp/index.html` на Vercel/Netlify/Railway Static — получить HTTPS URL
5. Прописать HTTPS URL в `bot/.env` → `MINIAPP_URL`
6. Подключить ЮКассу: получить `YOOKASSA_PROVIDER_TOKEN` из BotFather → Bot Settings → Payments → ЮКасса

## Локальный запуск

```bash
# API
cd api && pip install -r requirements.txt && uvicorn main:app --reload

# Bot (другой терминал)
cd bot && pip install -r requirements.txt && python bot.py
```

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

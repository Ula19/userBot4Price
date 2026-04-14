# UserBot4Price

Telegram userbot на Telethon для автоматического поиска цен iPhone / Samsung / MacBook / Dyson / аксессуаров.

## Как работает

1. Слушает сообщения от бота-источника
2. Извлекает запрос клиента и передаёт в GPT-4.1-mini для нормализации (`17 прошечка мах 256 блу` → структурный JSON)
3. Если в запросе есть артикул Apple (типа `MC6T4`) — ищет напрямую в прайсе, минуя AI
4. Ищет товар в чате с прайс-листом и отвечает клиенту в ЛС с имитацией живого человека (задержки, typing)

## Установка

```bash
git clone <repo-url>
cd userBot4Price
pip install -r requirements.txt
cp .env.example .env
```

## Настройка `.env`

| Переменная | Описание |
|------------|----------|
| `API_ID`, `API_HASH` | С [my.telegram.org](https://my.telegram.org) |
| `PHONE` | Номер userbot-аккаунта |
| `SOURCE_BOT` | Бот-источник запросов (username или ID) |
| `PRICE_CHAT_ID` | Чат с прайс-листом (`me` — для Saved Messages) |
| `OWNER_USERNAME` | Кому слать уведомления о ненайденных товарах |
| `OPENAI_API_KEY` | Для AI-нормализации (опционально) |
| `PROXY_URL` | SOCKS5 прокси для обхода блокировок DC2/DC4 (опционально) |

## Запуск

### Локально
```bash
python3 bot.py
```

### Docker
```bash
docker compose up -d --build
docker compose logs -f userbot
```

При первом запуске Telegram пришлёт SMS-код — ввести в терминал. Сессия сохранится в `data/userbot_session.session`.

## Тестовый запрос

Требует `BOT_TOKEN` (от @BotFather) и `USERBOT_CHAT_ID` (от @userinfobot) в `.env`:

```bash
python3 test_bot_send.py "17 Pro Max 256 Orange eSIM"
```

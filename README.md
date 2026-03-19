# UserBot4Price

Telegram userbot для автоматического поиска цен на товары.

## Как работает
1. Читает запросы от бота-источника (например @GorbushkinBot) в ЛС
2. Ищет цену в чате с прайс-листом
3. Отвечает юзеру в личные сообщения

## Установка

```bash
# клонируем репо
git clone <repo-url>
cd userBot4Price

# ставим зависимости
pip install -r requirements.txt

# копируем конфиг и заполняем
cp .env.example .env
```

## Настройка .env

1. Получи `API_ID` и `API_HASH` на [my.telegram.org](https://my.telegram.org)
2. Укажи номер телефона
3. Укажи username бота-источника и ID чата с прайсом

## Запуск

```bash
python bot.py
```

При первом запуске попросит ввести код из SMS.

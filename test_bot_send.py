"""
тестовый скрипт - отправляет сообщение от тестового бота юзерботу
имитирует формат сообщений GorbushkinBot

использование:
  python3 test_bot_send.py "17 Pro Max 256 Orange eSIM"
  python3 test_bot_send.py  (отправит дефолтный тестовый запрос)

перед запуском:
  1. впиши BOT_TOKEN в .env
  2. напиши /start тестовому боту в телеграме
"""
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
# ID юзербот-аккаунта (куда отправляем)
USERBOT_CHAT_ID = os.getenv('USERBOT_CHAT_ID')

if not BOT_TOKEN:
    print('Ошибка: впиши BOT_TOKEN в .env')
    sys.exit(1)

if not USERBOT_CHAT_ID:
    print('Ошибка: впиши USERBOT_CHAT_ID в .env')
    print('Это ID аккаунта юзербота, узнать можно через @userinfobot')
    sys.exit(1)

# если передали запрос через аргумент - используем его
if len(sys.argv) > 1:
    query = ' '.join(sys.argv[1:])
else:
    query = '17 Pro Max 256 Orange eSIM'

# формируем сообщение в формате GorbushkinBot
message = f'TestUser @great_85_76_65 · ➡️\nКуплю ‼️\n{query}'

# отправляем через Telegram Bot API
url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
response = requests.post(url, json={
    'chat_id': USERBOT_CHAT_ID,
    'text': message
})

if response.ok:
    print(f'Отправлено: "{query}"')
else:
    print(f'Ошибка: {response.json()}')

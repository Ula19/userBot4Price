import os
import logging
from dotenv import load_dotenv
from telethon import TelegramClient, events
import price_parser
import handlers
import aliases

# загружаем переменные из .env
load_dotenv()

# настраиваем логирование чтобы видеть что происходит
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# читаем настройки из .env
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE = os.getenv('PHONE')
PRICE_CHAT_ID = os.getenv('PRICE_CHAT_ID')
SOURCE_BOT = os.getenv('SOURCE_BOT')
OWNER_USERNAME = os.getenv('OWNER_USERNAME')

# путь к сессии — в Docker монтируется ./data, локально в текущей папке
import os as _os
SESSION_PATH = 'data/userbot_session' if _os.path.isdir('data') else 'userbot_session'

# создаем клиент телеграма (userbot)
client = TelegramClient(SESSION_PATH, API_ID, API_HASH)


# при любом изменении в чате прайса - перезагружаем весь прайс
# но игнорируем наши собственные сообщения (event.out)
@client.on(events.NewMessage(chats='me' if PRICE_CHAT_ID == 'me' else int(PRICE_CHAT_ID)))
async def on_price_new(event):
    """новое сообщение в чате прайса - перезагружаем всё"""
    if event.out:
        return
    await price_parser.reload_prices()
    await aliases.reload_aliases()


@client.on(events.MessageEdited(chats='me' if PRICE_CHAT_ID == 'me' else int(PRICE_CHAT_ID)))
async def on_price_edit(event):
    """сообщение отредактировано в чате прайса - перезагружаем всё"""
    if event.out:
        return
    await price_parser.reload_prices()
    await aliases.reload_aliases()


async def main():
    """запуск бота и авторизация"""
    await client.start(phone=PHONE)

    # проверяем что авторизация прошла успешно
    me = await client.get_me()
    logger.info(f'Бот запущен как: {me.first_name} (@{me.username})')

    # загружаем прайс-лист и алиасы при старте
    await price_parser.load_prices(client, PRICE_CHAT_ID)
    await aliases.load_aliases(client, PRICE_CHAT_ID)

    # подключаем обработчик запросов от бота-источника
    handlers.register_handlers(client, SOURCE_BOT, OWNER_USERNAME)
    logger.info(f'Слушаю запросы от @{SOURCE_BOT}')
    if OWNER_USERNAME:
        logger.info(f'Уведомления о ненайденном → @{OWNER_USERNAME}')

    logger.info('Для остановки нажми Ctrl+C')

    # бот работает пока не остановим
    await client.run_until_disconnected()


if __name__ == '__main__':
    client.loop.run_until_complete(main())

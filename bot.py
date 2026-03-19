import os
import logging
from dotenv import load_dotenv
from telethon import TelegramClient, events
import price_parser

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

# создаем клиент телеграма (userbot)
client = TelegramClient('userbot_session', API_ID, API_HASH)


# слушаем обновления в чате прайса (новые и отредактированные сообщения)
@client.on(events.NewMessage(chats='me' if PRICE_CHAT_ID == 'me' else int(PRICE_CHAT_ID)))
async def on_price_update(event):
    """когда в чате прайса появляется новое сообщение - обновляем базу"""
    if event.text:
        price_parser.update_prices(event.text)


@client.on(events.MessageEdited(chats='me' if PRICE_CHAT_ID == 'me' else int(PRICE_CHAT_ID)))
async def on_price_edit(event):
    """когда сообщение в чате прайса редактируется - обновляем базу"""
    if event.text:
        logger.info('Обнаружено изменение в прайсе, обновляю...')
        price_parser.update_prices(event.text)


async def main():
    """запуск бота и авторизация"""
    await client.start(phone=PHONE)

    # проверяем что авторизация прошла успешно
    me = await client.get_me()
    logger.info(f'Бот запущен как: {me.first_name} (@{me.username})')

    # загружаем прайс-лист при старте
    await price_parser.load_prices(client, PRICE_CHAT_ID)

    logger.info('Для остановки нажми Ctrl+C')

    # бот работает пока не остановим
    await client.run_until_disconnected()


if __name__ == '__main__':
    client.loop.run_until_complete(main())

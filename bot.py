import os
import logging
import socks
from urllib.parse import urlparse
from dotenv import load_dotenv
from telethon import TelegramClient, events
import price_parser
import handlers
import aliases
import examples

# загружаем переменные из .env
load_dotenv()

# настраиваем логирование чтобы видеть что происходит
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# убираем мусорные INFO-логи Telethon (Got difference, Connecting, etc.)
logging.getLogger('telethon').setLevel(logging.WARNING)

# читаем настройки из .env
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE = os.getenv('PHONE')
PRICE_CHAT_ID = os.getenv('PRICE_CHAT_ID')
SOURCE_BOT = os.getenv('SOURCE_BOT')
OWNER_USERNAME = os.getenv('OWNER_USERNAME')
PROXY_URL = os.getenv('PROXY_URL')
GROUP_CHATS = os.getenv('GROUP_CHATS', '')

# путь к сессии — в Docker монтируется ./data, локально в текущей папке
import os as _os
SESSION_PATH = 'data/userbot_session' if _os.path.isdir('data') else 'userbot_session'

# парсим прокси из .env если задан
def _parse_proxy(proxy_url: str | None):
    """Преобразует 'socks5://host:port' или 'socks5://user:pass@host:port' в tuple для Telethon."""
    if not proxy_url:
        return None
    p = urlparse(proxy_url)
    proxy_type = socks.SOCKS5 if p.scheme == 'socks5' else socks.SOCKS4
    if p.username and p.password:
        return (proxy_type, p.hostname, p.port, True, p.username, p.password)
    return (proxy_type, p.hostname, p.port)

_proxy = _parse_proxy(PROXY_URL)
if _proxy:
    logger.info(f'Прокси: {PROXY_URL.split("@")[-1]}')

# создаем клиент телеграма (userbot)
client = TelegramClient(SESSION_PATH, API_ID, API_HASH, proxy=_proxy)


# при любом изменении в чате прайса - перезагружаем весь прайс
# но игнорируем наши собственные сообщения (event.out)
@client.on(events.NewMessage(chats='me' if PRICE_CHAT_ID == 'me' else int(PRICE_CHAT_ID)))
async def on_price_new(event):
    """новое сообщение в чате прайса - перезагружаем всё"""
    await price_parser.reload_prices()
    await aliases.reload_aliases()
    await examples.reload_examples()


@client.on(events.MessageEdited(chats='me' if PRICE_CHAT_ID == 'me' else int(PRICE_CHAT_ID)))
async def on_price_edit(event):
    """сообщение отредактировано в чате прайса - перезагружаем всё"""
    await price_parser.reload_prices()
    await aliases.reload_aliases()
    await examples.reload_examples()


async def main():
    """запуск бота и авторизация"""
    await client.start(phone=PHONE)

    # проверяем что авторизация прошла успешно
    me = await client.get_me()
    logger.info(f'Бот запущен как: {me.first_name} (@{me.username})')

    # загружаем прайс-лист и алиасы при старте
    await price_parser.load_prices(client, PRICE_CHAT_ID)
    await aliases.load_aliases(client, PRICE_CHAT_ID)
    await examples.load_examples(client, PRICE_CHAT_ID)

    # резолвим username'ы в числовые ID (один раз при старте)
    # если в .env уже числовой ID — используем напрямую (без API)
    # если username — резолвим один раз
    if SOURCE_BOT.isdigit():
        source_id = int(SOURCE_BOT)
        logger.info(f'Бот-источник: ID {source_id} (из .env)')
    else:
        source_entity = await client.get_input_entity(SOURCE_BOT)
        source_id = source_entity.user_id
        logger.info(f'Бот-источник: @{SOURCE_BOT} → ID {source_id}')

    owner_id = None
    if OWNER_USERNAME:
        if OWNER_USERNAME.isdigit():
            owner_id = int(OWNER_USERNAME)
            logger.info(f'Заказчик: ID {owner_id} (из .env)')
        else:
            try:
                owner_entity = await client.get_input_entity(OWNER_USERNAME)
                owner_id = owner_entity.user_id
                logger.info(f'Заказчик: @{OWNER_USERNAME} → ID {owner_id}')
            except Exception as e:
                logger.error(f'Не удалось найти @{OWNER_USERNAME}: {e}')

    # подключаем обработчик запросов от бота-источника
    handlers.register_handlers(client, source_id, owner_id)
    logger.info(f'Слушаю запросы от ID {source_id}')
    if owner_id:
        logger.info(f'Уведомления о ненайденном → ID {owner_id}')

    # режим групп: слушаем указанные в GROUP_CHATS чаты
    group_entities = []
    for raw in (item.strip() for item in GROUP_CHATS.split(',')):
        if not raw:
            continue
        try:
            if raw.lstrip('-').isdigit():
                entity = await client.get_input_entity(int(raw))
            else:
                entity = await client.get_input_entity(raw)
            group_entities.append(entity)
            logger.info(f'Группа: {raw} → ок')
        except Exception as e:
            logger.error(f'Не удалось найти группу {raw}: {e}')

    if group_entities:
        handlers.register_group_handlers(client, group_entities, owner_id)
        logger.info(f'Слушаю {len(group_entities)} групп(ы)')

    logger.info('Для остановки нажми Ctrl+C')

    # бот работает пока не остановим
    await client.run_until_disconnected()


if __name__ == '__main__':
    client.loop.run_until_complete(main())

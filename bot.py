import os
import asyncio
import logging
import socks
from urllib.parse import urlparse
from dotenv import load_dotenv
from telethon import TelegramClient, events, utils
import price_parser
import handlers
import aliases
import examples
import group_rules

# загружаем переменные из .env
load_dotenv()

# настраиваем логирование чтобы видеть что происходит
# %(source)s — откуда запрос: [бот] или [группа -100…] (пусто для служебных логов)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(source)s%(message)s'
)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(handlers.LogSourceFilter())
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


PRICE_CHAT = 'me' if PRICE_CHAT_ID == 'me' else int(PRICE_CHAT_ID)


# перезагрузки чата прайса не должны идти параллельно (иначе лишние запросы к Telegram)
_reload_lock = asyncio.Lock()
_reload_again = False


async def _reload_price_chat():
    """
    перечитываем из чата прайса всё: фильтры групп, товары, алиасы и примеры.
    пачку событий подряд схлопываем: пока идёт перезагрузка, новые события просят ещё одну.
    """
    global _reload_again
    if _reload_lock.locked():
        _reload_again = True
        return

    async with _reload_lock:
        while True:
            _reload_again = False
            # каждый шаг отдельно: ошибка в одном не отменяет остальные
            for name, reload in (('фильтры групп', group_rules.reload_rules),
                                 ('прайс', price_parser.reload_prices),
                                 ('алиасы', aliases.reload_aliases),
                                 ('примеры', examples.reload_examples)):
                try:
                    await reload()
                except Exception as e:
                    logger.error(f'Не удалось перезагрузить {name}: {e}')
            if not _reload_again:
                break


# при любом изменении в чате прайса - перезагружаем весь прайс
@client.on(events.NewMessage(chats=PRICE_CHAT))
async def on_price_new(event):
    """новое сообщение в чате прайса - перезагружаем всё"""
    await _reload_price_chat()


@client.on(events.MessageEdited(chats=PRICE_CHAT))
async def on_price_edit(event):
    """сообщение отредактировано в чате прайса - перезагружаем всё"""
    await _reload_price_chat()


@client.on(events.MessageDeleted())
async def on_message_delete(event):
    """
    удалили сообщение (например, фильтр группы) - перезагружаем всё.
    для канала Telegram присылает chat_id; для обычной группы и 'me' chat_id нет —
    тогда сверяем ID удалённых сообщений с сообщениями чата прайса.
    """
    if event.chat_id is not None:
        if PRICE_CHAT == 'me' or event.chat_id != PRICE_CHAT:
            return
    elif not group_rules.knows_message(event.deleted_ids):
        return
    await _reload_price_chat()


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
    await group_rules.load_rules(client, PRICE_CHAT_ID)

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
            brands = group_rules.get_brands(utils.get_peer_id(entity))
            if brands:
                logger.info(f'Группа: {raw} → ок, фильтр: {", ".join(sorted(brands))}')
            else:
                logger.warning(f'Группа: {raw} → нет фильтра в канале прайса, в ней бот молчит')
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

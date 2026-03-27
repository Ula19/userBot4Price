"""
Модуль для получения числового ID пользователя по username.

Цепочка fallback:
1. Кэш (data/user_id_cache.json)
2. @raw_data_bot
3. @username_to_id_bot
4. @username_to_id_test_bot
5. Прямой API (ResolveUsernameRequest) — крайний случай
"""
import re
import json
import os
import asyncio
import logging
from telethon import errors

logger = logging.getLogger(__name__)

# файл кэша
USER_ID_CACHE_FILE = 'data/user_id_cache.json'

# боты для резолва username → ID (в порядке приоритета)
RESOLVER_BOTS = [
    'giveme_id_bot',
    'raw_data_bot',
    'username_to_id_bot',
    'username_to_id_test_bot',
]

# кэш в памяти
_cache = None


def _load_cache():
    """загружает кэш с диска"""
    global _cache
    if _cache is not None:
        return _cache

    if os.path.exists(USER_ID_CACHE_FILE):
        try:
            with open(USER_ID_CACHE_FILE, 'r') as f:
                _cache = json.load(f)
                return _cache
        except Exception:
            pass

    _cache = {}
    return _cache


def _save_cache():
    """сохраняет кэш на диск"""
    global _cache
    if _cache is None:
        return

    os.makedirs(os.path.dirname(USER_ID_CACHE_FILE), exist_ok=True)
    try:
        with open(USER_ID_CACHE_FILE, 'w') as f:
            json.dump(_cache, f, indent=2)
    except Exception as e:
        logger.error(f'  [ID] Ошибка записи кэша: {e}')


def _parse_id_from_response(text):
    """
    парсит числовой ID из ответа бота
    ищет паттерн 'id: 123456' в тексте
    """
    match = re.search(r'id:\s*(\d+)', text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


async def _ask_bot(client, bot_username, target_username):
    """
    отправляет @username боту и ждёт ответ с ID
    использует событийный подход (event handler) вместо поллинга
    возвращает числовой ID или None
    """
    from telethon import events

    result = {'user_id': None, 'text': ''}
    got_response = asyncio.Event()

    async def on_bot_response(event):
        """обработчик входящего сообщения от бота"""
        text = event.text or ''
        logger.info(f'  [ID][EVENT] Получено от @{bot_username}: "{text[:80]}"')

        user_id = _parse_id_from_response(text)
        if user_id:
            result['user_id'] = user_id
            result['text'] = text
            got_response.set()

    try:
        # регистрируем обработчик ПЕРЕД отправкой
        handler = client.add_event_handler(
            on_bot_response,
            events.NewMessage(from_users=bot_username, incoming=True)
        )

        # отправляем боту запрос
        sent_msg = await client.send_message(bot_username, f'@{target_username}')
        logger.info(f'  [ID] Отправлено сообщение #{sent_msg.id} боту @{bot_username}')

        # ждём ответ (максимум 15 секунд)
        try:
            await asyncio.wait_for(got_response.wait(), timeout=15)
        except asyncio.TimeoutError:
            logger.warning(f'  [ID] Таймаут 15с — @{bot_username} не прислал ID')

        # убираем обработчик
        client.remove_event_handler(handler)

        if result['user_id']:
            logger.info(f'  [ID] @{bot_username} ответил: "{result["text"][:80]}"')
            return result['user_id']

        return None

    except errors.FloodWaitError as e:
        logger.warning(f'  [ID] FloodWait {e.seconds}с при обращении к @{bot_username}')
        return None
    except Exception as e:
        logger.warning(f'  [ID] Ошибка @{bot_username}: {e}')
        return None


async def _resolve_via_api(client, username):
    """
    последний шанс — прямой API запрос (ResolveUsernameRequest)
    может вызвать бан, используется только когда все боты недоступны
    """
    try:
        entity = await client.get_input_entity(username)
        return entity.user_id
    except errors.FloodWaitError as e:
        logger.error(f'  [ID] FloodWait {e.seconds}с при прямом API для @{username}')
        return None
    except Exception as e:
        logger.error(f'  [ID] Прямой API: ошибка для @{username}: {e}')
        return None


async def resolve_user_id(client, username):
    """
    получает числовой ID пользователя по username

    цепочка:
    1. Кэш
    2. @raw_data_bot
    3. @username_to_id_bot
    4. @username_to_id_test_bot
    5. Прямой API (крайний случай)

    возвращает (user_id, source) или (None, 'не удалось')
    """
    cache = _load_cache()

    # 1. Кэш
    if username in cache:
        user_id = cache[username]
        logger.info(f'  [ID] @{username} → {user_id} (источник: кэш)')
        return user_id, 'кэш'

    # 2-4. Боты
    for bot_username in RESOLVER_BOTS:
        logger.info(f'  [ID] Пробуем @{bot_username} для @{username}...')
        user_id = await _ask_bot(client, bot_username, username)

        if user_id:
            # кэшируем
            cache[username] = user_id
            _save_cache()
            logger.info(f'  [ID] @{username} → {user_id} (источник: @{bot_username})')
            return user_id, f'@{bot_username}'

        logger.warning(f'  [ID] @{bot_username} не ответил для @{username}')

    # 5. Прямой API — крайний случай
    logger.warning(f'  [ID] Все боты не ответили, пробуем прямой API для @{username}...')
    user_id = await _resolve_via_api(client, username)

    if user_id:
        # кэшируем
        cache[username] = user_id
        _save_cache()
        logger.info(f'  [ID] @{username} → {user_id} (источник: прямой API ⚠️)')
        return user_id, 'прямой API'

    logger.error(f'  [ID] @{username} → не удалось получить ID никаким способом!')
    return None, 'не удалось'

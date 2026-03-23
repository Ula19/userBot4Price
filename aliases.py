"""
загрузка алиасов от заказчика из Телеграма

заказчик пишет сообщение с маркером 📝АЛИАСЫ в чате прайса:
    📝АЛИАСЫ
    мах, макс = max
    силвер, серебро = silver
    оранж = orange

несколько слов через запятую = один и тот же перевод
"""
import re
import logging

logger = logging.getLogger(__name__)

# хранилище алиасов из Телеграма
telegram_aliases = {}

# ссылки на клиент и чат
_client = None
_chat_id = None


def parse_aliases_message(text):
    """
    парсит сообщение с алиасами
    формат: слово1, слово2 = перевод
    """
    found = {}

    for line in text.split('\n'):
        line = line.strip()

        # пропускаем заголовок и пустые строки
        if not line or '📝' in line or 'АЛИАС' in line.upper():
            continue

        # ищем формат: слово1, слово2 = перевод
        match = re.match(r'^(.+?)\s*=\s*(.+)$', line)
        if match:
            keys_part = match.group(1).strip()
            value = match.group(2).strip().lower()

            # разбиваем левую часть по запятым
            keys = [k.strip().lower() for k in keys_part.split(',')]
            for key in keys:
                if key:
                    found[key] = value

    return found


async def load_aliases(client, chat_id):
    """
    загружает алиасы из чата при старте
    ищет сообщение с маркером 📝АЛИАСЫ
    """
    global telegram_aliases, _client, _chat_id
    _client = client
    _chat_id = chat_id
    telegram_aliases = {}

    entity = 'me' if chat_id == 'me' else int(chat_id)

    async for message in client.iter_messages(entity, limit=100):
        if message.text and (
            '📝' in message.text or 'АЛИАС' in message.text.upper()
        ):
            found = parse_aliases_message(message.text)
            telegram_aliases.update(found)

    if telegram_aliases:
        logger.info(f'Загружено алиасов: {len(telegram_aliases)}')
        for key, value in telegram_aliases.items():
            logger.info(f'  {key} → {value}')
    else:
        logger.info('Алиасов из Телеграма нет')


async def reload_aliases():
    """перезагрузка алиасов при изменении в чате"""
    if _client and _chat_id:
        logger.info('Алиасы изменились, перезагружаю...')
        await load_aliases(_client, _chat_id)


def get_aliases():
    """возвращает текущие алиасы из Телеграма"""
    return telegram_aliases

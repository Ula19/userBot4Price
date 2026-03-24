"""
загрузка примеров запросов от заказчика из Телеграма

заказчик пишет сообщение с маркером 📝ПРИМЕРЫ в чате прайса.
каждый товар — отдельное сообщение, формат:

📝ПРИМЕРЫ
[ 17 Pro 256 Blue (eSim) =
17 пм 256 блу есим,
17 п 256 синий есим,
17 pro 256 blue esim ]

всё между [ и ] — один товар
слева от = — название товара из прайса
справа от = (через запятую) — варианты запросов
"""
import re
import logging

logger = logging.getLogger(__name__)

# хранилище примеров: {нормализованный_вариант: название_товара}
telegram_examples = {}

# ссылки на клиент и чат
_client = None
_chat_id = None


def parse_examples_message(text):
    """
    парсит сообщение с примерами
    формат: [ Товар = вариант1, вариант2, вариант3 ]
    может быть несколько блоков [ ] в одном сообщении
    """
    found = {}

    # ищем все блоки между [ и ]
    blocks = re.findall(r'\[([^\]]+)\]', text, flags=re.DOTALL)

    for block in blocks:
        # убираем переносы строк — всё в одну строку
        block = ' '.join(block.split())

        # ищем формат: Товар = вариант1, вариант2
        match = re.match(r'^(.+?)\s*=\s*(.+)$', block)
        if not match:
            continue

        product_name = match.group(1).strip()
        variants_part = match.group(2).strip()

        # разбиваем варианты по запятым
        variants = [v.strip().lower() for v in variants_part.split(',')]

        for variant in variants:
            # убираем лишние пробелы внутри варианта
            variant = ' '.join(variant.split())
            if variant:
                found[variant] = product_name

    return found


async def load_examples(client, chat_id):
    """
    загружает примеры из чата при старте
    ищет сообщения с маркером 📝ПРИМЕРЫ
    """
    global telegram_examples, _client, _chat_id
    _client = client
    _chat_id = chat_id
    telegram_examples = {}

    entity = 'me' if chat_id == 'me' else int(chat_id)

    async for message in client.iter_messages(entity, limit=200):
        if message.text and (
            '📝' in message.text and 'ПРИМЕР' in message.text.upper()
        ):
            found = parse_examples_message(message.text)
            telegram_examples.update(found)

    if telegram_examples:
        logger.info(f'Загружено примеров: {len(telegram_examples)}')
    else:
        logger.info('Примеров из Телеграма нет')


async def reload_examples():
    """перезагрузка примеров при изменении в чате"""
    if _client and _chat_id:
        logger.info('Примеры изменились, перезагружаю...')
        await load_examples(_client, _chat_id)


def find_by_example(normalized_query):
    """
    ищет запрос в таблице примеров
    normalized_query — уже нормализованный запрос (нижний регистр, без лишних пробелов)
    возвращает название товара или None
    """
    # убираем лишние пробелы и приводим к нижнему регистру
    query = ' '.join(normalized_query.lower().split())

    if query in telegram_examples:
        product_name = telegram_examples[query]
        logger.info(f'  [Примеры] Найден: "{query}" → "{product_name}"')
        return product_name

    return None


def get_examples():
    """возвращает текущие примеры из Телеграма"""
    return telegram_examples

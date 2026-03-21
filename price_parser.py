import re
import logging

logger = logging.getLogger(__name__)

# тут храним все товары из прайса
# каждый товар - словарь с name и price
products = []

# сохраняем ссылку на клиент и chat_id чтобы можно было перезагрузить
_client = None
_chat_id = None


def parse_price_message(text):
    """
    парсит одно сообщение из чата прайса
    возвращает список товаров найденных в сообщении

    реальный формат строки из телеграма:
    `Название товара — Цена` /Количество
    бэктики, эмодзи флагов, пробелы - всё учитываем
    """
    found = []

    # убираем бэктики - они мешают парсингу
    text = text.replace('`', '')

    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue

        # пропускаем заголовки с эмодзи (🔤🔤🔤🔤🔤)
        if '🔤' in line:
            continue

        # убираем эмодзи флагов
        line = re.sub(
            r'[\U0001F1E0-\U0001F1FF\U0001F3F4\U000E0067-\U000E007F]+',
            '', line
        ).strip()

        # ищем строку с ценой: "название — цена /кол-во"
        # /кол-во обязательно - это отличает прайс от обычного текста
        match = re.match(
            r'^(.+?)\s*[—–\-]\s*([\d]+[.\d]*)\s*/\s*(\d+)\s*$',
            line
        )

        if match:
            name = match.group(1).strip()
            price = match.group(2).strip()

            found.append({'name': name, 'price': price})
            logger.info(f'  {name} — {price}')

    return found


async def load_prices(client, chat_id):
    """
    загружает все цены из чата
    вызывается при запуске и при любом изменении в чате прайса
    """
    global products, _client, _chat_id
    _client = client
    _chat_id = chat_id
    products = []

    # определяем чат
    entity = 'me' if chat_id == 'me' else int(chat_id)

    logger.info(f'Загружаю прайс из чата: {chat_id}')

    # читаем последние сообщения из чата
    async for message in client.iter_messages(entity, limit=100):
        if message.text:
            found = parse_price_message(message.text)
            products.extend(found)

    logger.info(f'Загружено товаров: {len(products)}')
    return products


async def reload_prices():
    """
    полная перезагрузка прайса
    вызывается когда в чате прайса что-то изменилось
    """
    if _client and _chat_id:
        logger.info('Прайс изменился, перезагружаю...')
        await load_prices(_client, _chat_id)


def get_all_products():
    """возвращает текущий список товаров"""
    return products

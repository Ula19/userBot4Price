import re
import logging

logger = logging.getLogger(__name__)

# тут храним все товары из прайса
# каждый товар - словарь с name, price, category
products = []

# соответствие количества 🔤 эмодзи к категориям
# пока определяем категорию по содержимому строк после заголовка
KNOWN_BRANDS = ['apple', 'iphone', 'redmi', 'xiaomi', 'dyson', 'samsung', 'huawei']


def _detect_category(name):
    """определяет категорию по названию товара"""
    name_lower = name.lower()

    if any(word in name_lower for word in ['dyson']):
        return 'DYSON'
    elif any(word in name_lower for word in ['redmi', 'xiaomi']):
        return 'REDMI'
    elif any(word in name_lower for word in ['samsung', 'galaxy']):
        return 'SAMSUNG'
    elif any(word in name_lower for word in ['huawei']):
        return 'HUAWEI'
    else:
        # по умолчанию apple (т.к. большинство запросов - айфоны)
        return 'APPLE'


def parse_price_message(text):
    """
    парсит одно сообщение из чата прайса
    возвращает список товаров найденных в сообщении

    реальный формат строки из телеграма:
    `Название товара — Цена` /Количество
    или
    `Название товара — Цена `/Количество
    бэктики, эмодзи флагов, пробелы - всё учитываем
    """
    found = []

    # убираем бэктики из текста - они мешают парсингу
    text = text.replace('`', '')

    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue

        # пропускаем строки с эмодзи-заголовками (🔤🔤🔤🔤🔤)
        if '🔤' in line:
            continue

        # убираем эмодзи флагов
        line = re.sub(
            r'[\U0001F1E0-\U0001F1FF\U0001F3F4\U000E0067-\U000E007F]+',
            '', line
        ).strip()

        # ищем строку с ценой: "название — цена /кол-во"
        # тире может быть: —, –, -
        # перед ценой может быть пробел или нет
        match = re.match(
            r'^(.+?)\s*[—–\-]\s*([\d]+[.\d]*)\s*/?(\d+)?\s*$',
            line
        )

        if match:
            name = match.group(1).strip()
            price = match.group(2).strip()

            # определяем категорию по названию
            category = _detect_category(name)

            found.append({
                'name': name,
                'price': price,
                'category': category
            })
            logger.info(f'  [{category}] {name} — {price}')

    return found


async def load_prices(client, chat_id):
    """
    загружает все цены из чата при запуске бота
    chat_id может быть 'me' (избранное) или числовой ID
    """
    global products
    products = []

    # определяем куда подключаться
    if chat_id == 'me':
        entity = 'me'
    else:
        entity = int(chat_id)

    logger.info(f'Загружаю прайс из чата: {chat_id}')

    # читаем последние сообщения из чата
    async for message in client.iter_messages(entity, limit=100):
        if message.text:
            found = parse_price_message(message.text)
            products.extend(found)

    logger.info(f'Загружено товаров: {len(products)}')
    return products


def update_prices(text):
    """
    обновляет прайс когда приходит новое или измененное сообщение
    полностью перепарсивает сообщение
    """
    global products

    # парсим новые товары из сообщения
    new_products = parse_price_message(text)

    if new_products:
        # удаляем старые товары с такими же названиями
        new_names = {p['name'] for p in new_products}
        products = [p for p in products if p['name'] not in new_names]

        # добавляем обновленные
        products.extend(new_products)
        logger.info(f'Прайс обновлен, всего товаров: {len(products)}')


def get_all_products():
    """возвращает текущий список товаров"""
    return products

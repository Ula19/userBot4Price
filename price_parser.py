import re
import logging

logger = logging.getLogger(__name__)

# тут храним все товары из прайса
# каждый товар - словарь с name, price, category
products = []


def parse_price_message(text):
    """
    парсит одно сообщение из чата прайса
    возвращает список товаров найденных в сообщении
    
    формат строки прайса:
    Название товара — Цена /Количество
    или
    Название товара — Цена/Количество
    """
    found = []
    current_category = "OTHER"

    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue

        # проверяем не заголовок ли это категории (APPLE, DYSON и тп)
        # заголовки обычно написаны большими буквами с пробелами между букв
        clean_line = line.replace(' ', '').upper()
        if clean_line in ['APPLE', 'REDMI', 'DYSON', 'SAMSUNG', 'XIAOMI', 'HUAWEI']:
            current_category = clean_line
            logger.info(f'  категория: {current_category}')
            continue

        # ищем строку с ценой: "название — цена /кол-во"
        # тире может быть разным: —, –, -
        match = re.match(
            r'^(.+?)\s*[—–\-]\s*([\d.,]+)\s*/?\s*(\d+)?\s*$',
            line
        )

        if match:
            name = match.group(1).strip()
            price = match.group(2).strip()

            # убираем эмодзи флагов и лишние символы из названия
            name = re.sub(r'[🇷🇺🇮🇳🇺🇸🇨🇳🇰🇷🇯🇵🇬🇧🇩🇪🇫🇷🇪🇸🇮🇹🏴󠁧󠁢󠁥󠁮󠁧󠁿]+', '', name).strip()

            found.append({
                'name': name,
                'price': price,
                'category': current_category
            })
            logger.info(f'  товар: {name} — {price}')

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
        logger.info(f'Прайс обновлен, товаров: {len(products)}')


def get_all_products():
    """возвращает текущий список товаров"""
    return products

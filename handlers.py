import re
import logging
from datetime import datetime, timezone, timedelta
from telethon import events
import search

logger = logging.getLogger(__name__)

# московское время (UTC+3)
MSK = timezone(timedelta(hours=3))

# рабочие часы (по МСК)
WORK_START = 10  # 10:00
WORK_END = 20    # 20:00


def is_work_time():
    """проверяет что сейчас рабочее время (10:00-20:00 МСК)"""
    now = datetime.now(MSK)
    return WORK_START <= now.hour < WORK_END


def extract_username(text):
    """
    извлекает username юзера из сообщения бота
    в сообщении обычно есть строка вида: "Имя @username · ➡️"
    """
    match = re.search(r'@(\w+)', text)
    if match:
        return match.group(1)
    return None


def extract_queries(text):
    """
    извлекает запросы из сообщения бота
    убирает строку с username и мусорные строки
    поддерживает запятые: "17 pro 256, 17 pro max 256, sim-esim"
    """
    # слова которые не являются запросом сами по себе
    stop_words = {
        'куплю', 'купить', 'нужен', 'нужна', 'нужно',
        'предложите', 'есть', 'ищу', 'хочу', 'надо',
    }

    raw_lines = []

    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue

        # пропускаем строку с username
        if re.search(r'@\w+\s*·', line):
            continue

        # убираем эмодзи и спецсимволы для проверки
        clean = re.sub(r'[^\w\s]', '', line).strip()
        if not clean or len(clean) < 3:
            continue

        # пропускаем строки только из стоп-слов
        words = clean.lower().split()
        if all(word in stop_words for word in words):
            continue

        raw_lines.append(line)

    # разбиваем по запятым если есть
    queries = []
    for line in raw_lines:
        if ',' in line:
            parts = [p.strip() for p in line.split(',') if p.strip()]
            queries.extend(parts)
        else:
            queries.append(line)

    return queries


def _detect_shared_sim(queries):
    """
    проверяет не является ли последний элемент типом SIM для всех запросов
    пример: ["17 pro 256", "17 pro max 256", "sim-esim"]
    → sim-esim применяется ко всем, убираем из списка запросов
    """
    if len(queries) < 2:
        return queries, None

    last = queries[-1].lower().strip()
    sim_type = search._detect_sim_type(last)

    if sim_type:
        # проверяем что последний элемент - ТОЛЬКО SIM тип (не товар)
        clean = search._remove_sim_words(last)
        clean = search.normalize_query(clean)
        if not clean.strip():
            # последний элемент - чисто SIM тип, применяем ко всем
            return queries[:-1], sim_type

    return queries, None


def format_response(results):
    """
    форматирует ответ юзеру
    возвращает только НАЙДЕННЫЕ товары (без ❌ сообщений)
    формат как в прайсе, без количества
    """
    lines = []

    for product in results:
        lines.append(f'{product["name"]} — {product["price"]}')

    return '\n'.join(lines) if lines else None


def register_handlers(client, source_bot, owner_username=None):
    """
    регистрирует обработчик сообщений от бота-источника
    source_bot - username бота (без @)
    owner_username - username заказчика для уведомлений (без @)
    """

    @client.on(events.NewMessage(from_users=source_bot))
    async def on_bot_message(event):
        """пришло сообщение от бота - ищем цены и отвечаем юзеру"""
        text = event.text
        if not text:
            return

        # проверяем рабочее время
        if not is_work_time():
            logger.info('Запрос вне рабочего времени, пропускаю')
            return

        logger.info(f'Новый запрос от {source_bot}')

        # достаем username юзера и запросы
        username = extract_username(text)
        queries = extract_queries(text)

        if not queries:
            logger.info('  Нет запросов в сообщении')
            return

        # проверяем общий SIM тип (последний элемент через запятую)
        queries, shared_sim = _detect_shared_sim(queries)

        logger.info(f'  Юзер: @{username}')
        logger.info(f'  Запросов: {len(queries)}')
        if shared_sim:
            logger.info(f'  Общий SIM: {shared_sim}')

        # ищем цены по каждому запросу
        all_found = []
        notify_queries = []

        for query in queries:
            result = search.find_products(query, sim_override=shared_sim)

            if result['exact']:
                all_found.extend(result['exact'])
            elif result['similar']:
                # не нашли точно, но есть похожие - уведомить заказчика
                notify_queries.append({
                    'query': query,
                    'similar': result['similar']
                })

        # отправляем юзеру ТОЛЬКО найденные товары
        if all_found and username:
            response = format_response(all_found)
            try:
                await client.send_message(username, response)
                await client.send_message(username, 'как дали')
                logger.info(f'  Ответ отправлен @{username}')
            except Exception as e:
                logger.error(f'  Не удалось отправить @{username}: {e}')
        elif not all_found:
            logger.info('  Ничего не найдено, юзеру не пишем')

        # уведомляем заказчика о запросах с похожими (но не точными)
        if notify_queries and owner_username:
            notify_lines = ['🔔 Не удалось точно найти:']
            notify_lines.append(f'Юзер: @{username or "неизвестен"}')
            for item in notify_queries:
                notify_lines.append(f'\n❌ Запрос: "{item["query"]}"')
                notify_lines.append('Похожие:')
                for p in item['similar'][:3]:
                    notify_lines.append(f'  • {p["name"]} — {p["price"]}')

            try:
                await client.send_message(
                    owner_username, '\n'.join(notify_lines)
                )
                logger.info(f'  Уведомление → @{owner_username}')
            except Exception as e:
                logger.error(f'  Не удалось уведомить @{owner_username}: {e}')

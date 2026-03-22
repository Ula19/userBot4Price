import re
import logging
from telethon import events
import search

logger = logging.getLogger(__name__)


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
    извлекает строки запроса из сообщения бота
    убирает строку с username и мусорные строки
    каждая непустая строка = отдельный запрос
    """
    # слова которые не являются запросом сами по себе
    stop_words = {
        'куплю', 'купить', 'нужен', 'нужна', 'нужно',
        'предложите', 'есть', 'ищу', 'хочу', 'надо',
    }

    queries = []

    for line in text.split('\n'):
        line = line.strip()

        # пропускаем пустые строки
        if not line:
            continue

        # пропускаем строку с username (содержит @username)
        if re.search(r'@\w+\s*·', line):
            continue

        # убираем эмодзи и спецсимволы чтобы проверить что осталось
        clean = re.sub(r'[^\w\s]', '', line).strip()
        if not clean:
            continue

        # пропускаем слишком короткие строки (меньше 3 символов)
        if len(clean) < 3:
            continue

        # пропускаем строки которые состоят только из стоп-слов
        words = clean.lower().split()
        if all(word in stop_words for word in words):
            continue

        queries.append(line)

    return queries


def format_response(query, result):
    """
    форматирует ответ юзеру

    - если есть точные совпадения: показываем их
    - если нет точных но есть похожие: говорим что не нашли + показываем похожие
    - если вообще ничего: говорим что не нашли
    """
    lines = []

    if result['exact']:
        # точные совпадения
        if len(result['exact']) == 1:
            p = result['exact'][0]
            lines.append(f'{p["name"]} — {p["price"]}')
        else:
            for p in result['exact']:
                lines.append(f'• {p["name"]} — {p["price"]}')

    elif result['similar']:
        # нет точных, но есть похожие
        lines.append(f'❌ По запросу "{query}" точных совпадений нет.')
        lines.append('Похожие товары:')
        for p in result['similar']:
            lines.append(f'• {p["name"]} — {p["price"]}')

    else:
        # вообще ничего
        lines.append(f'❌ "{query}" — не найдено в прайсе')

    return '\n'.join(lines)


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

        logger.info(f'Новый запрос от {source_bot}')

        # достаем username юзера и запросы
        username = extract_username(text)
        queries = extract_queries(text)

        if not queries:
            logger.info('  Нет запросов в сообщении')
            return

        logger.info(f'  Юзер: @{username}')
        logger.info(f'  Запросов: {len(queries)}')

        # ищем цены по каждому запросу
        responses = []
        not_found_queries = []

        for query in queries:
            result = search.find_products(query)
            response = format_response(query, result)
            responses.append(response)

            # запоминаем запросы без точных совпадений
            if not result['exact']:
                not_found_queries.append(query)

        # собираем итоговый ответ
        full_response = '\n\n'.join(responses)

        # отправляем юзеру в ЛС
        if username:
            try:
                await client.send_message(username, full_response)
                logger.info(f'  Ответ отправлен @{username}')
            except Exception as e:
                logger.error(f'  Не удалось отправить @{username}: {e}')
        else:
            logger.warning('  Username не найден, некуда отправлять')

        # уведомляем заказчика о ненайденных запросах
        if not_found_queries and owner_username:
            notify_lines = ['🔔 Не найдено в прайсе:']
            notify_lines.append(f'Юзер: @{username or "неизвестен"}')
            for q in not_found_queries:
                notify_lines.append(f'  ❌ {q}')

            try:
                await client.send_message(
                    owner_username, '\n'.join(notify_lines)
                )
                logger.info(f'  Уведомление отправлено @{owner_username}')
            except Exception as e:
                logger.error(f'  Не удалось уведомить @{owner_username}: {e}')

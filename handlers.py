import re
import time
import random
import asyncio
import json
import os
import logging
from datetime import datetime, timezone, timedelta
from telethon import events, errors
import search
import id_resolver
import ai_parser

logger = logging.getLogger(__name__)

# анти-спам кулдаун
user_last_reply = {}

# юзеры которым уже писали (для детекции нового чата без API)
known_users = set()

# флаги стран → тип SIM (только для iPhone)
FLAG_TO_SIM = {
    '🇭🇰': 'sim_esim',   # Hong Kong — Sim + eSim
    '🇨🇳': 'sim_sim',    # China — Dual SIM
    '🇺🇸': 'esim',       # USA — eSIM only
    '🇯🇵': 'esim',       # Japan — eSIM only
    '🇪🇺': 'sim_esim',   # EU — Sim + eSim
    '🇷🇺': 'sim_esim',   # Russia — Sim + eSim
}


def _detect_flag_sim(text):
    """Ищет флаг страны в тексте и возвращает соответствующий тип SIM."""
    for flag, sim_type in FLAG_TO_SIM.items():
        if flag in text:
            return sim_type
    return None


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

    # раскрываем слеш-опции: "blue/orange" → два отдельных запроса
    expanded = []
    for q in queries:
        parts = _expand_slash_options(q)
        expanded.extend(parts)

    return expanded


def _expand_slash_options(query):
    """
    раскрывает слеш-опции в запросе
    '17 pro max 256 blue/orange eSIM' → ['17 pro max 256 blue eSIM', '17 pro max 256 orange eSIM']
    '17 pro max 256/512 blue' → ['17 pro max 256 blue', '17 pro max 512 blue']
    без слешей → возвращает как есть
    """
    # ищем паттерн "слово/слово" (может быть несколько вариантов через /)
    match = re.search(r'(\S+(?:/\S+)+)', query)
    if not match:
        return [query]

    slash_part = match.group(1)

    # не трогаем дроби вроде "16/256" (RAM/Storage) и "6/128GB"
    # дробь = первое число НАМНОГО меньше второго (16/256 = 16x, 6/128 = 21x)
    # опции памяти = числа близки (256/512 = 2x)
    options = slash_part.split('/')
    if all(re.match(r'^\d+\w*$', opt) for opt in options):
        nums = [int(re.match(r'^(\d+)', opt).group(1)) for opt in options if re.match(r'^(\d+)', opt)]
        if len(nums) >= 2 and max(nums) / min(nums) >= 8:
            # ratio ≥ 8 → это дробь (16/256, 6/128, 8/256) — оставляем как есть
            return [query]

    # раскрываем: заменяем слеш-часть каждым вариантом
    result = []
    for option in options:
        expanded = query[:match.start()] + option + query[match.end():]
        result.append(expanded.strip())

    return result


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
    случайно выбирает один из 3 шаблонов обёртки
    """
    lines = []

    for product in results:
        lines.append(f'{product["name"]} — {product["price"]}')

    if not lines:
        return None

    price_text = '\n'.join(lines)

    # случайный шаблон обёртки
    template = random.randint(1, 3)
    if template == 1:
        return f'{price_text}\n\nкак дали?'
    elif template == 2:
        return f'В наличии:\n\n{price_text}'
    else:
        return f'{price_text}\n\nИнтересно?'


def register_handlers(client, source_bot, owner_username=None):
    """
    регистрирует обработчик сообщений от бота-источника
    source_bot - числовой ID бота (резолвится один раз при запуске)
    owner_username - числовой ID заказчика для уведомлений
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

        if username:
            now = time.time()
            if username in user_last_reply:
                elapsed = now - user_last_reply[username]
                if elapsed < 60:
                    logger.warning(f'  [Анти-спам] Игнорируем запрос от @{username}. Прошло {int(elapsed)}с из 60с.')
                    return
            user_last_reply[username] = now

        # проверяем общий SIM тип (последний элемент через запятую)
        queries, shared_sim = _detect_shared_sim(queries)

        logger.info(f'  Юзер: @{username}')
        logger.info(f'  Запросов: {len(queries)}')
        if shared_sim:
            logger.info(f'  Общий SIM: {shared_sim}')

        # === ПОИСК ТОВАРОВ ===
        all_found = []
        notify_queries = []

        # ШАГ 1: нормализуем запрос через ИИ
        full_query = '\n'.join(queries)
        normalized = await ai_parser.normalize_queries(full_query)

        if normalized is not None:
            # Проверяем флаги стран → SIM-тип (только для iPhone)
            flag_sim = _detect_flag_sim(text)
            if flag_sim:
                logger.info(f'  [Флаг] Обнаружен флаг → SIM: {flag_sim}')

            for item in normalized:
                # Флаг применяется только к iPhone и только если AI не определил SIM
                if flag_sim and not item.get('sim'):
                    model = item.get('model', '').lower()
                    if re.match(r'^\d', model):
                        item['sim'] = flag_sim
                        logger.info(f'  [Флаг] Применяем SIM={flag_sim} к {item["model"]}')

                result = search.find_by_normalized(item)
                if result['exact']:
                    all_found.extend(result['exact'])
                elif result['similar']:
                    notify_queries.append({
                        'query': ai_parser.build_search_query(item),
                        'similar': result['similar']
                    })
        else:
            # AI недоступен — уведомляем владельца
            logger.error('  [AI] OpenAI недоступен! Уведомляем владельца.')
            if owner_username:
                try:
                    await client.send_message(
                        owner_username,
                        '🚨 AI-нормализатор недоступен!\n'
                        f'Запрос от @{username or "неизвестен"} не обработан.\n'
                        'Проверьте OPENAI_API_KEY и соединение.'
                    )
                except Exception:
                    pass

        # дедупликация — убираем одинаковые товары
        seen = set()
        unique_found = []
        for p in all_found:
            if p['name'] not in seen:
                seen.add(p['name'])
                unique_found.append(p)
        all_found = unique_found

        # отправляем юзеру ТОЛЬКО найденные товары
        if all_found and username:
            response = format_response(all_found)
            try:
                # проверяем писали ли мы уже этому юзеру (без API вызовов)
                is_new_user = username not in known_users

                # Имитируем человека: ждём случайное время перед ответом
                # Моментальный ответ — частая причина спам-бана
                delay = random.uniform(30, 90)
                logger.info(f'  Жду {delay:.1f}с перед ответом @{username} (анти-спам)...')
                await asyncio.sleep(delay)

                # Получаем числовой ID через цепочку fallback
                user_id, source = await id_resolver.resolve_user_id(client, username)

                if user_id is None:
                    logger.error(f'  Не удалось получить ID для @{username}, пропускаем')
                    return

                # отправляем по числовому ID (без ResolveUsernameRequest!)
                # если Telethon не знает access_hash для этого ID — шлём по @username
                recipient = user_id

                try:
                    typing_time = random.uniform(10, 30)
                    logger.info(f'  Имитирую набор текста для @{username} ({typing_time:.1f}с)...')
                    async with client.action(recipient, 'typing'):
                        await asyncio.sleep(typing_time)
                    await client.send_message(recipient, response)
                except ValueError as e:
                    # Стухший кэш: ID сохранён, но текущая сессия не знает access_hash
                    # Это бывает после смены сессии или если юзер давно в кэше
                    if 'input entity' in str(e).lower() or 'peeruser' in str(e).lower():
                        logger.warning(
                            f'  [ID] Стухший кэш: {user_id} не работает для @{username}. '
                            f'Удаляем кэш и шлём по @username...'
                        )
                        # удаляем невалидный кэш — следующий раз заново спросим через бота
                        id_resolver.invalidate_cache(username)

                        recipient = username
                        async with client.action(recipient, 'typing'):
                            await asyncio.sleep(5)
                        await client.send_message(recipient, response)
                    else:
                        raise
                except errors.FloodWaitError as e:
                    if e.seconds > 300:
                        logger.error(f'  [Анти-спам] Бан {e.seconds}с (~{e.seconds // 3600}ч) для @{username}. Пропускаем.')
                        raise
                    logger.warning(f'  [Анти-спам] Телеграм просит подождать {e.seconds}с для @{username}. Жду...')
                    await asyncio.sleep(e.seconds + 2)
                    await client.send_message(recipient, response)


                known_users.add(username)

                if is_new_user:
                    logger.info(f'  Ответ отправлен @{username} (НОВЫЙ юзер)')
                else:
                    logger.info(f'  Ответ отправлен @{username} (чат существует)')

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

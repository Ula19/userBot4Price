import re
import time
import random
import asyncio
import json
import os
import logging
from collections import Counter
from datetime import datetime, timezone, timedelta
from telethon import events, errors
from telethon.tl.types import User
import search
import id_resolver
import ai_parser
import group_rules
import brand_detector

logger = logging.getLogger(__name__)

# имитация человека перед ответом (одинаково для SOURCE_BOT и групп): пауза + набор текста, секунды
REPLY_DELAY = (10, 20)
TYPING_TIME = (5, 10)

# анти-спам кулдаун
user_last_reply = {}

# кулдаун для group-режима (ключ — sender.id)
group_user_last_reply = {}

# юзеры которым уже писали (для детекции нового чата без API)
known_users = set()

# буфер уведомлений владельцу (отправляются дайджестом раз в час)
_owner_notify_buffer = []
_owner_flusher_started = False
_OWNER_FLUSH_INTERVAL = 3600  # раз в час
_TELEGRAM_MSG_LIMIT = 4000    # запас от лимита 4096

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
MSK = timezone(timedelta(hours=3))  # +3

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


# SIM-ключевые слова (вынесено сюда после рефакторинга search.py)
_SIM_PATTERNS = {
    'sim_esim': ['sim+esim', 'sim/esim', 'dual sim', 'sim esim', 'simesim'],
    'esim':     ['esim', 'e-sim', 'е-сим', 'есим'],
    'sim':      ['sim', 'сим', '1sim', '2sim', 'dualsim'],
}


def _local_detect_sim_type(text):
    """Определяет тип SIM из строки. Возвращает 'esim'/'sim'/'sim_esim' или None."""
    t = text.lower().strip()
    for sim_type, keywords in _SIM_PATTERNS.items():
        for kw in keywords:
            if kw in t:
                return sim_type
    return None


def _detect_shared_sim(queries):
    """
    проверяет не является ли последний элемент типом SIM для всех запросов
    пример: ["17 pro 256", "17 pro max 256", "sim-esim"]
    → sim-esim применяется ко всем, убираем из списка запросов
    """
    if len(queries) < 2:
        return queries, None

    last = queries[-1].lower().strip()
    sim_type = _local_detect_sim_type(last)

    if sim_type:
        # убираем SIM-слова и проверяем что больше ничего не осталось
        cleaned = re.sub(r'(esim|e-sim|sim|сим|есим|\+|/|-)', '', last).strip()
        if not cleaned:
            # последний элемент — чисто SIM тип, применяем ко всем
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
        line = f'{product["name"]} — {product["price"]}'
        # хвост из прайса (заметки вроде "(с царапиной на коробке)")
        tail = product.get('tail')
        if tail:
            line += f' {tail}'
        lines.append(line)

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


async def _search_products(queries_list, raw_text):
    """
    Общее ядро поиска: артикул → AI-нормализация → категорийный поиск → дедуп.

    queries_list — список запросов (для find_by_article, разбитых по запятым/строкам).
    raw_text — исходный текст целиком (для детекции флага страны → SIM и full_query для AI).

    Возвращает (all_found, notify_queries, ai_ok).
    ai_ok=False если AI-нормализатор недоступен (None).
    """
    all_found = []
    notify_queries = []

    found_by_article, remaining = search.find_by_article(queries_list)
    if found_by_article:
        all_found.extend(found_by_article)
        logger.info(f'  [Артикул] Найдено по артикулу: {len(found_by_article)}')

    if not remaining:
        logger.info('  Все запросы обработаны по артикулу, AI пропускаем')
        normalized = []
    else:
        full_query = '\n'.join(remaining)
        normalized = await ai_parser.normalize_queries(full_query)

    if normalized is None:
        logger.error('  [AI] OpenAI недоступен!')
        return _dedup(all_found), notify_queries, False

    flag_sim = _detect_flag_sim(raw_text)
    if flag_sim:
        logger.info(f'  [Флаг] Обнаружен флаг → SIM: {flag_sim}')

    for item in normalized:
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
                'raw': raw_text,
                'ai': ai_parser.build_search_query(item),
                'item': item,
                'similar': result['similar']
            })

    return _dedup(all_found), notify_queries, True


def _dedup(products):
    seen = set()
    out = []
    for p in products:
        # ключ = название + цена + хвост: разные варианты одной модели
        # (например "с царапиной" дешевле) не должны схлопываться в один
        key = (p['name'], p['price'], p.get('tail', ''))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


async def _notify_owner_similar(client, owner_id, who_label, notify_queries):
    """
    Кладёт уведомления о похожих в буфер. Сам флушер отправит дайджестом раз в час.
    Не отправляет мгновенно, чтобы не флудить владельцу.
    """
    if not notify_queries or not owner_id:
        return
    _owner_notify_buffer.append({
        'ts': time.time(),
        'who': who_label,
        'entries': notify_queries,
    })
    logger.info(f'  В дайджест: {who_label} → {len(notify_queries)} похож. (всего в буфере: {len(_owner_notify_buffer)})')


def _build_digest_chunks(batch):
    """Собирает сообщения дайджеста с учётом лимита Telegram (4096)."""
    header = f'📊 Дайджест за час — {len(batch)} запрос(ов) с похожими\n'
    chunks = []
    current = header

    for record in batch:
        ts_str = datetime.fromtimestamp(record['ts'], MSK).strftime('%H:%M')
        block_lines = [f'\n── {ts_str}  {record["who"]} ──']
        for entry in record['entries']:
            block_lines.append(f'📝 "{entry["raw"]}"')
            block_lines.append(f'🤖 AI: {entry["ai"]}')
            block_lines.append('⚠️ Похожие:')
            for p in entry['similar'][:3]:
                reason = p.get('_reason', '?')
                block_lines.append(f'  • {p["name"]} — {p["price"]}  [{reason}]')
        block = '\n'.join(block_lines) + '\n'

        if len(current) + len(block) > _TELEGRAM_MSG_LIMIT:
            chunks.append(current.rstrip())
            current = '(продолжение)\n' + block
        else:
            current += block

    if current.strip():
        chunks.append(current.rstrip())
    return chunks


async def _owner_flusher(client, owner_id):
    """Раз в час сливает буфер уведомлений владельцу одним дайджестом."""
    logger.info(f'[Дайджест] флушер запущен, интервал {_OWNER_FLUSH_INTERVAL}с')
    while True:
        try:
            await asyncio.sleep(_OWNER_FLUSH_INTERVAL)
            if not _owner_notify_buffer:
                continue
            # snapshot + clear (asyncio single-threaded — атомарно)
            batch = _owner_notify_buffer.copy()
            _owner_notify_buffer.clear()
            chunks = _build_digest_chunks(batch)
            for chunk in chunks:
                try:
                    await client.send_message(owner_id, chunk)
                except Exception as e:
                    logger.error(f'[Дайджест] не отправилось: {e}')
            logger.info(f'[Дайджест] отправлено {len(chunks)} сообщ. ({len(batch)} записей)')
        except Exception as e:
            logger.error(f'[Дайджест] ошибка цикла: {e}')


def _ensure_owner_flusher(client, owner_id):
    """Запускает флушер один раз. Безопасно вызывать из любого register_*."""
    global _owner_flusher_started
    if _owner_flusher_started or not owner_id:
        return
    asyncio.create_task(_owner_flusher(client, owner_id))
    _owner_flusher_started = True


# ═══════════════════════════════════════════════════════════════════
# GROUP-РЕЖИМ: мониторинг обычных Telegram-групп
# ═══════════════════════════════════════════════════════════════════

def _split_for_article(text):
    """Разбивает сырой текст по переводам строк и запятым для find_by_article."""
    parts = []
    for line in (text or '').split('\n'):
        for p in line.split(','):
            p = p.strip()
            if p:
                parts.append(p)
    return parts or [text]


# статистика групп за час: сколько пришло, сколько и почему отсеяно, сколько ответили
_group_stats = Counter()


async def _group_stats_logger():
    """Раз в час пишет в лог статистику групп — видно, что режет фильтр брендов."""
    while True:
        try:
            await asyncio.sleep(_OWNER_FLUSH_INTERVAL)
            if not _group_stats:
                continue
            parts = ', '.join(f'{k}={v}' for k, v in _group_stats.most_common())
            logger.info(f'[Группы за час] {parts} | из кеша ИИ (с запуска, все режимы): {ai_parser.cache_hits}')
            _group_stats.clear()
        except Exception as e:
            logger.error(f'[Группы] ошибка статистики: {e}')


def _group_test_mode():
    """GROUP_TEST_MODE=1 в .env — локальный тест групп: без рабочих часов и с причиной отсева в логе."""
    return os.getenv('GROUP_TEST_MODE', '').strip().lower() in ('1', 'true', 'yes', 'да')


def _group_skip(reason, chat_id, test_mode):
    """Считает отсев в статистике; в тестовом режиме ещё и пишет причину в лог."""
    _group_stats[f'отсев:{reason}'] += 1
    if test_mode:
        logger.info(f'  [Группа/тест] {chat_id}: не отвечаем — {reason}')


async def _send_group_dm(client, sender, who_label, response):
    """
    Ответ в ЛС с набором текста.
    True — отправлено, False — флуд-бан (не отвечаем совсем), None — не вышло, пробуем reply в группе.
    """
    try:
        typing_time = random.uniform(*TYPING_TIME)
        logger.info(f'  Имитирую набор текста в ЛС {who_label} ({typing_time:.1f}с)...')
        async with client.action(sender, 'typing'):
            await asyncio.sleep(typing_time)
        await client.send_message(sender, response)
        logger.info(f'  Ответ отправлен в ЛС {who_label}')
        return True
    except errors.FloodWaitError as e:
        if e.seconds > 300:
            logger.error(f'  [Флуд] бан {e.seconds}с — пропускаем {who_label}')
            _group_stats['ошибка:флуд'] += 1
            return False
        logger.warning(f'  [Флуд] ждём {e.seconds}с для {who_label}')
        await asyncio.sleep(e.seconds + 2)
        try:
            await client.send_message(sender, response)
            return True
        except Exception as e2:
            logger.warning(f'  [Группа] повтор ЛС не удался: {e2}')
    except (errors.UserPrivacyRestrictedError, errors.UserIsBlockedError) as e:
        logger.info(f'  [Группа] ЛС недоступно ({type(e).__name__}), fallback в группу')
    except Exception as e:
        logger.warning(f'  [Группа] ЛС не вышло ({type(e).__name__}: {e}), fallback в группу')
    return None


async def _send_group_reply(client, event, sender, who_label, response):
    """
    Отвечает на запрос из группы с короткой имитацией человека (как в режиме SOURCE_BOT).
    Живому человеку — в ЛС; боту, каналу или если ЛС закрыто — reply в группе.
    Возвращает True, если ответ ушёл.
    """
    delay = random.uniform(*REPLY_DELAY)
    logger.info(f'  Жду {delay:.1f}с перед ответом {who_label}...')
    await asyncio.sleep(delay)

    # боту или каналу в ЛС писать бесполезно — их владелец ответ не увидит
    if isinstance(sender, User) and not sender.bot:
        sent = await _send_group_dm(client, sender, who_label, response)
        if sent is not None:
            return sent

    try:
        async with client.action(event.chat_id, 'typing'):
            await asyncio.sleep(random.uniform(*TYPING_TIME))
        await event.reply(response)
        logger.info(f'  Ответ отправлен reply в группу {event.chat_id}')
        return True
    except Exception as e:
        logger.error(f'  [Группа] reply не удался: {e}')
        return False


def register_group_handlers(client, group_chats, owner_id=None):
    """
    Регистрирует обработчик сообщений в обычных группах.

    group_chats — список entity/ID/username, который Telethon примет в chats=.
    owner_id — числовой ID заказчика для уведомлений о похожих.
    """
    _ensure_owner_flusher(client, owner_id)
    asyncio.create_task(_group_stats_logger())
    if _group_test_mode():
        logger.warning('ТЕСТОВЫЙ РЕЖИМ ГРУПП (GROUP_TEST_MODE): без рабочих часов, причина отсева в логе — на проде выключить!')

    @client.on(events.NewMessage(chats=group_chats, incoming=True))
    async def on_group_message(event):
        text = event.raw_text
        if not text or event.out:
            return

        test_mode = _group_test_mode()
        if not test_mode and not is_work_time():
            return

        _group_stats['всего'] += 1

        # фильтр брендов из канала прайса — проверяем ДО запросов к Telegram и ИИ
        brands = group_rules.get_brands(event.chat_id)
        if not brands:
            _group_skip('нет_фильтра', event.chat_id, test_mode)
            return
        if group_rules.is_too_long(text):
            _group_skip('длинное', event.chat_id, test_mode)
            return
        brand = brand_detector.find_brand(text, brands)
        if not brand:
            _group_skip('не_тот_бренд', event.chat_id, test_mode)
            return

        try:
            sender = await event.get_sender()
        except Exception as e:
            logger.warning(f'  [Группа] get_sender: {e}')
            sender = None

        # отвечаем всем: людям, ботам, каналам и анонимным отправителям
        sender_id = getattr(sender, 'id', None) or event.sender_id or event.chat_id
        username = getattr(sender, 'username', None)
        who_label = f'@{username}' if username else f'id:{sender_id}'

        now = time.time()
        last = group_user_last_reply.get(sender_id)
        if last and now - last < 60:
            _group_skip('кулдаун', event.chat_id, test_mode)
            logger.warning(f'  [Группа/Анти-спам] {who_label}: прошло {int(now-last)}с из 60с')
            return
        group_user_last_reply[sender_id] = now

        _group_stats[f'прошло:{brand}'] += 1
        logger.info(f'Новый запрос из группы {event.chat_id} от {who_label} (бренд: {brand})')

        queries_list = _split_for_article(text)
        all_found, notify_queries, ai_ok = await _search_products(queries_list, text)

        if not ai_ok and owner_id:
            try:
                await client.send_message(
                    owner_id,
                    f'🚨 AI-нормализатор недоступен!\n'
                    f'Запрос от {who_label} в группе {event.chat_id} не обработан.'
                )
            except Exception:
                pass

        if all_found:
            response = format_response(all_found)
            if await _send_group_reply(client, event, sender, who_label, response):
                _group_stats['ответов'] += 1
        else:
            logger.info('  [Группа] ничего не найдено, не отвечаем')

        await _notify_owner_similar(
            client, owner_id,
            f'{who_label} (группа {event.chat_id})',
            notify_queries
        )


def register_handlers(client, source_bot, owner_username=None):
    """
    регистрирует обработчик сообщений от бота-источника
    source_bot - числовой ID бота (резолвится один раз при запуске)
    owner_username - числовой ID заказчика для уведомлений
    """
    _ensure_owner_flusher(client, owner_username)

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

        all_found, notify_queries, ai_ok = await _search_products(queries, text)

        if not ai_ok and owner_username:
            try:
                await client.send_message(
                    owner_username,
                    '🚨 AI-нормализатор недоступен!\n'
                    f'Запрос от @{username or "неизвестен"} не обработан.\n'
                    'Проверьте OPENAI_API_KEY и соединение.'
                )
            except Exception:
                pass

        # отправляем юзеру ТОЛЬКО найденные товары
        if all_found and username:
            response = format_response(all_found)
            try:
                # проверяем писали ли мы уже этому юзеру (без API вызовов)
                is_new_user = username not in known_users

                # Имитируем человека: ждём случайное время перед ответом
                # Моментальный ответ — частая причина спам-бана
                delay = random.uniform(*REPLY_DELAY)
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
                    typing_time = random.uniform(*TYPING_TIME)
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
        who_label = f'@{username}' if username else 'неизвестен'
        await _notify_owner_similar(client, owner_username, who_label, notify_queries)

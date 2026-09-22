"""
ИИ-нормализатор запросов через OpenAI API.

Получает сырой текст запроса клиента,
возвращает список структурированных JSON-объектов {model, memory, color, sim}.
Python (search.py) сам ищет товары по этим полям — без прайса в промпте.
"""
import os
import re
import json
import time
import logging
import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)

# ленивая инициализация — чтобы load_dotenv() успел отработать до первого вызова
_client = None

# кеш ответов ИИ: один и тот же запрос часто кидают в несколько групп подряд
_CACHE_TTL = 1800   # 30 минут
_CACHE_MAX = 500
_result_cache = {}  # нормализованный текст → (время, результат)
cache_hits = 0


def _cache_key(text):
    return ' '.join(text.lower().split())


def is_cached(text):
    """True, если на этот текст есть свежий ответ ИИ (вызова API не будет)."""
    entry = _result_cache.get(_cache_key(text))
    return entry is not None and time.time() - entry[0] < _CACHE_TTL


def _cache_put(text, result):
    """Кладёт ответ в кеш; при переполнении выкидывает самую старую запись."""
    key = _cache_key(text)
    _result_cache.pop(key, None)
    if len(_result_cache) >= _CACHE_MAX:
        del _result_cache[next(iter(_result_cache))]
    _result_cache[key] = (time.time(), result)


def _get_client():
    global _client
    if _client is None:
        api_key = os.getenv('OPENAI_API_KEY')
        if api_key:
            # IP сервера в неподдерживаемой OpenAI стране → 403.
            # Гоним трафик к OpenAI через тот же прокси, что и Telegram (Германия).
            proxy = os.getenv('PROXY_URL')
            http_client = httpx.Client(proxy=proxy, timeout=30) if proxy else None
            _client = OpenAI(api_key=api_key, http_client=http_client)
    return _client

SYSTEM_PROMPT = """Ты — парсер запросов для магазина электроники.
Твоя единственная задача: извлечь характеристики товара из текста клиента.

═══════════════════════════════════════
ГЛАВНОЕ ПРАВИЛО — САМОЕ ВАЖНОЕ:
═══════════════════════════════════════
НИКОГДА не выдумывай параметры. Только то, что ЯВНО написано клиентом.
Не написал SIM  → sim: null
Не написал цвет → color: null
Не написал память → memory: null

═══════════════════════════════════════
ФОРМАТ ОТВЕТА
═══════════════════════════════════════
Строго JSON-массив. Каждый товар — объект с 4 полями:
{
  "model":  "<модель на англ, без памяти и цвета>",
  "memory": "<число: 256, 128, или 8/256. null если не указано>",
  "color":  "<цвет на англ: Blue, Black, Orange... null если не указано>",
  "sim":    "<esim | sim_esim | sim_sim | null>"
}

═══════════════════════════════════════
SIM-СЛОТ:
═══════════════════════════════════════
Все современные iPhone имеют 3 варианта конфигурации SIM-лотка:

  esim     = Нет физического лотка. Все SIM виртуальные.
  sim_esim = Один физический лоток + один виртуальный eSIM.
  sim_sim  = Два физических лотка. Нет eSIM.

Как определить (рассуждай логически):
- Клиент упомянул И физическую SIM И eSIM → sim_esim
  (примеры: "sim eSIM", "сим+есим", "нано+эсим", "Sim+eSim")
- Клиент говорит об ОДНОМ физическом слоте → sim_esim
  (один физический лоток на iPhone ВСЕГДА дополняется виртуальным eSIM)
  (примеры: "1 сим", "1сим", "одна сим", "нано сим", "1 sim", "nano sim")
- Клиент говорит ТОЛЬКО про eSIM → esim
  (примеры: "есим", "esim", "только есим", "2esim", "eSIM only")
- Клиент говорит о ДВУХ физических → sim_sim
  (примеры: "2 сим", "2сим", "dual sim", "два физических", "нано+нано")
- Клиент НЕ упомянул SIM вообще → null

ЗАПОМНИ: 1 физ. слот ≠ 1 SIM. На iPhone 1 физ. лоток
ВСЕГДА дополняется виртуальным eSIM = sim_esim.

═══════════════════════════════════════
ПРАВИЛА ИЗВЛЕЧЕНИЯ
═══════════════════════════════════════
МОДЕЛЬ:
- НЕСКОЛЬКО товаров (через запятую / "и" / с новой строки): парси КАЖДЫЙ независимо.
  НЕ переноси бренд или модель одного товара на другой.
  Пример: "Dyson HS08 и A07 4/128" → [{"model":"Dyson HS08"...}, {"model":"A07"...}]  (A07 это Samsung, НЕ "Dyson A07")
- iPhone: ОБЯЗАТЕЛЬНО сохраняй номер поколения. "17 Pro Max" → model:"17 Pro Max", НЕ "Pro Max"
- Исправляй опечатки: "прошечка"→"17 Pro", "макс"→"Pro Max", "мах"→"Pro Max", "пм"→"Pro Max"
- КАЖДАЯ строка и КАЖДАЯ позиция — отдельный объект. Позиций может быть 30 — верни ВСЕ, ничего не пропускай
- Игнорируй: приветствия, вопросы ("есть?", "почём?", "привет брат", "куплю"),
  слова "Предложите", "Смартфон", "Телефон", количество в конце ("A37 128 черный 2" → количество 2 НЕ цвет и НЕ память)
- Заводской код в скобках — НЕ цвет, выбрось его: "A27 5G 8/256GB Black (A276B)" → color:"Black"
  Но название цвета в скобках — это цвет: "8Gb 256Gb (Cloud Blush)" → color:"Cloud Blush"
- Samsung A-серия: любая "A07", "A17", "A27", "A36", "A37", "A56" — это Galaxy, НЕ iPhone и НЕ Apple chip
- Redmi и Xiaomi — ОДИН бренд: "Xiaomi Note 17" = "Redmi Note 17", "Xiaomi 15" = "Redmi 15"
- Redmi/Xiaomi: ОБЯЗАТЕЛЬНО сохраняй "Note" и "Pro"/"Pro Max" — это разные телефоны:
    "redmi note 17 pro max 8/512" → "Redmi Note 17 Pro Max"
    "Xiaomi Redmi Note 17 Pro 8/256" → "Redmi Note 17 Pro"
    "Redmi Note 17 4/128" → "Redmi Note 17"
    "Redmi 17 4/128" → "Redmi 17"   (без Note — другая модель!)
  "5G" в модель НЕ добавляй
- Dyson: "дайсон в12" → "Dyson V12s"
- Адаптер: всегда включай мощность → "Apple 20W Adapter"
- AirPods (наушники Apple):
  Любые написания (рус/жаргон/англ) → каноничная форма "AirPods N" / "AirPods Pro N" / "AirPods Max"
    "аирподс/эирподс/эирпотс/наушники эпл/наушники apple" → "AirPods"
  Если юзер УПОМЯНУЛ шумоподавление — ОБЯЗАТЕЛЬНО добавь суффикс "ANC":
    "anc/шумодав/шумоподавление/с шумкой/noise cancelling/нойз" → "ANC" в конце
  Примеры:
    "аирподс 4" → "AirPods 4"
    "airpods 4 anc" / "аирподс 4 с шумкой" → "AirPods 4 ANC"
    "аирподс про 2" / "airpods pro 2" → "AirPods Pro 2"
    "аирподс макс" → "AirPods Max"
  memory/color/sim — всегда null
- PlayStation/Xbox/Nintendo (игровые консоли — это товар, НЕ отвергай):
  Любые написания (рус/жаргон/англ) приводи к английскому виду магазина:
    "плойка/плейстейшен/сони/пс" → "PS"
    "иксбокс/xbox" → "Xbox"
    "свитч/нинтендо/switch" → "Nintendo Switch"
  БАЗОВАЯ модель обязательна (PS4/PS5, Xbox Series X/S):
    "плойка 5" → "PS5",  "пс4" → "PS4"
    "иксбокс series x" → "Xbox Series X"
    "плойка"/"сони"/"пс" БЕЗ номера → []
  ПОД-версию (Slim/Pro/OLED/Disk/Digital) добавляй ТОЛЬКО если юзер написал.
  Не дописывай то, чего не было в запросе:
    "пс5" → "PS5"           (без под-версии — найдём все PS5* в прайсе)
    "пс5 слим" → "PS5 Slim"
    "пс5 про" → "PS5 Pro"
    "пс5 с диском"/"пс5 диск" → "PS5 Disk"
  Для консолей memory/color/sim — всегда null
- Honor (смартфоны):
  Распознавай бренд в любом написании: "хонор/honor".
  В ответе модель ВСЕГДА с префиксом "Honor". Кириллицу в коде модели приводи к латинице:
    "хонор икс 8 д" / "honor x8d" / "икс8д хонор" → "Honor X8D"
    "хонор х6с" / "honor x6c" → "Honor X6C"   (кирилл. 'х'→X, 'с'→C)
    "хонор икс 9 д" → "Honor X9D"
    "хонор 600 лайт" / "honor 600 lite" / "600 лайт" → "Honor 600 Lite"
  Память — RAM/Storage ("8/256"). Цвет — англ:
    серый→Gray, беж→Beige, океан блю→Ocean Blue, терракота→Terracotta, графит→Graphite
  sim — всегда null
- Sony DualSense (геймпад/контроллер — это АКСЕССУАР, НЕ консоль):
  "дуалсенс / dualsense / геймпад сони / джойстик от пс / контроллер playstation" → "Sony DualSense"
  "дуалсенс эдж / dualsense edge" → "Sony DualSense Edge"
  ВАЖНО: "джойстик/геймпад/контроллер" для PS — это DualSense, а НЕ сама консоль PS5.
  Цвет — только если явно назван (на англ). memory/sim — null
- Яндекс Станция / Алиса (умная колонка — товар на РУССКОМ, НЕ переводи на англ!):
  "яндекс станция / алиса / колонка яндекс" → "Яндекс Станция <подмодель>"
    "станция миди" / "алиса миди" → "Яндекс Станция Миди"
    "алиса лайт 2" / "станция лайт 2" → "Яндекс Станция Лайт 2"
    просто "алиса" / "станция" без подмодели → "Яндекс Станция"
  model оставляй на КИРИЛЛИЦЕ. Цвет — на русском как сказал юзер
    (оранжевая→Оранжевый, синяя→Синий, фиолетовая→Фиолетовый). memory/sim — null

ПАМЯТЬ:
- iPhone: только Storage → "17 Pro 256" → memory:"256"
- Терабайт: "1тб"/"1 терабайт"/"терабайт"/"1tb"/"1024" → memory:"1TB"; "2тб"/"2 терабайта" → "2TB". Просто "терабайт" без числа → "1TB"
- Android/Samsung/Xiaomi/Redmi: RAM/Storage → "A36 8/256" → memory:"8/256"
- Любое написание памяти сводй к RAM/Storage: "8Gb 256Gb" / "8/256GB" / "8 + 256" / "8gb/256gb" → "8/256"
- Одно число у Android — это Storage: "A37 128 черный" → memory:"128"
- Исправляй очевидные опечатки: 257→256, 513→512
- КРИТИЧНО: "8/256" принадлежит строго своему товару.
  Если запрос "A36 8/256 и 17 Pro черный" — у 17 Pro memory:null

ЦВЕТ:
- Переводи на англ стандарт: "оранжевый"→Orange, "синий"→Blue, "космический оранжевый"→"Cosmic Orange"
- iPhone 18 Pro / 18 Pro Max — официальные цвета: Burgundy, Glacier Blue, Deep Black, Silver.
  Любое написание клиента сводй к ним:
    "бордовый/бордо/бургунди/вишнёвый/вишня/винный/марсала/красный/cherry/wine" → "Burgundy"
    "голубой/ледяной/глейшер/глетчер/гласиер/айс/синий/блю/ice blue/light blue" → "Glacier Blue"
    "черный/блэк/спейс блэк" → "Deep Black"
    "серебро/серебристый/сильвер/белый" → "Silver"   (белого у 18 Pro нет — это Silver)
  Эти замены — ТОЛЬКО для iPhone 18. У iPhone 17 и старше цвета как раньше ("синий" → Blue, "белый" → White)
- Dyson: слеш в цвете — ЧАСТЬ официального названия → "Vinca Blue/Topaz" = один объект
- Телефоны: слеш в цвете — два варианта → "Black/Blue" = два объекта

СЛЕШ "/" В ЗАПРОСЕ (только для телефонов):
- "Blue/Orange" → два отдельных объекта
- "esim/sim_esim" → два объекта по SIM
- "256/512" (числа близки, до 4x разницы) → два объекта по памяти
- "8/256" (разница ≥8x) → это RAM/Storage, один объект

"ПО НАЛИЧИЮ" / "ЛЮБОЙ":
- "любой цвет", "не важно", "по наличию" → color:null
- "любой сим" → sim:null

Формат ответа: только JSON-массив, без текста до и после.
Каждый объект ОБЯЗАН содержать все 4 поля: model, memory, color, sim.
Пример: [{"model": "17 Pro Max", "memory": "256", "color": "Orange", "sim": "sim_esim"}]
Если запрос не о товарах → []"""


def _salvage_items(content: str):
    """Вытаскивает целые объекты {..} из обрезанного ответа ИИ (лучше часть товаров, чем ничего)."""
    items = []
    for chunk in re.findall(r'\{[^{}]*\}', content):
        try:
            item = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get('model'):
            items.append(item)
    return items


def build_search_query(item: dict) -> str:
    """Собирает строку поиска из нормализованных полей ИИ."""
    parts = []
    if item.get('model'):
        parts.append(item['model'])
    if item.get('memory'):
        parts.append(item['memory'])
    if item.get('color'):
        parts.append(item['color'])
    return ' '.join(parts)


async def normalize_queries(text: str):
    """
    Нормализует сырой текст запроса клиента через ИИ.

    ИИ НЕ видит прайс-лист — он только переводит кривой запрос
    в структурированный JSON. Python потом сам ищет товары.

    Args:
        text: сырой текст от клиента (может содержать несколько товаров)

    Returns:
        list[dict] — [{model, memory, color, sim}, ...] — нормализованные товары
        None — если ИИ недоступен (для fallback на прямой поиск)
    """
    global cache_hits

    if not _get_client():
        logger.warning('OpenAI API ключ не настроен, используем прямой поиск')
        return None

    if is_cached(text):
        cache_hits += 1
        logger.info(f'  [ИИ] Из кеша, без вызова API: "{text[:80]}"')
        # копии, чтобы правки в handlers (sim по флагу) не портили кеш
        return [dict(item) for item in _result_cache[_cache_key(text)][1]]

    user_message = f'ЗАПРОС КЛИЕНТА: "{text}"'

    try:
        logger.info(f'  [ИИ] >>> Нормализуем запрос: "{text[:80]}"')

        response = _get_client().chat.completions.create(
            model='gpt-4.1-mini',
            messages=[
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': user_message},
            ],
            temperature=0,
            max_tokens=4000,   # большие запросы: 30+ позиций в одном сообщении
            timeout=30,
        )

        content = response.choices[0].message.content.strip()
        # finish_reason может отсутствовать (прокси, нестандартный ответ) — не роняем разбор
        truncated = getattr(response.choices[0], 'finish_reason', None) == 'length'
        if truncated:
            logger.error('  [ИИ] Ответ обрезан по лимиту токенов — беру то, что успело прийти')

        usage = response.usage
        logger.info(f'  [ИИ] <<< Ответ: {content}')
        logger.info(f'  [ИИ] <<< Токены: вход={usage.prompt_tokens}, выход={usage.completion_tokens}, всего={usage.total_tokens}')

        # убираем markdown-обёртку если ИИ добавил ```json ... ```
        if content.startswith('```'):
            content = content.split('\n', 1)[1]
            content = content.rsplit('```', 1)[0]

        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            # обрезанный или битый JSON: вытаскиваем товары, которые пришли целиком
            result = _salvage_items(content)
            if not result:
                raise
            logger.warning(f'  [ИИ] JSON битый, спасли {len(result)} товар(ов) из ответа')

        if not isinstance(result, list):
            logger.error(f'ИИ вернул не массив: {content}')
            return None

        # фильтруем объекты без модели
        result = [item for item in result if isinstance(item, dict) and item.get('model')]

        logger.info(f'  [ИИ] Нормализовано: {len(result)} товар(ов)')
        for item in result:
            q = build_search_query(item)
            logger.info(f'    → "{q}" | sim={item.get("sim")}')

        if not truncated:
            _cache_put(text, result)   # обрезанный ответ неполный — кешировать нельзя
        return [dict(item) for item in result]

    except json.JSONDecodeError as e:
        logger.error(f'ИИ вернул невалидный JSON: {e}')
        return None
    except Exception as e:
        logger.error(f'Ошибка OpenAI API: {e}')
        return None

"""
ИИ-нормализатор запросов через OpenAI API.

Получает сырой текст запроса клиента,
возвращает список структурированных JSON-объектов {model, memory, color, sim}.
Python (search.py) сам ищет товары по этим полям — без прайса в промпте.
"""
import os
import json
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

# инициализация клиента OpenAI
_api_key = os.getenv('OPENAI_API_KEY')
_client = OpenAI(api_key=_api_key) if _api_key else None

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

Значения sim (только если клиент ЯВНО упомянул слово sim/esim/сим):
  esim     → только eSIM ("есим", "2esim", "dual esim", "два есима")
  sim_esim → физическая + виртуальная ("sim+esim", "1сим+есим", "nano+esim")
  sim_sim  → только физические ("2sim", "dual sim", "нано+нано")
  null     → слов SIM/eSIM/сим НЕТ в тексте → ВСЕГДА null

═══════════════════════════════════════
ПРАВИЛА ИЗВЛЕЧЕНИЯ
═══════════════════════════════════════
МОДЕЛЬ:
- Исправляй опечатки: "прошечка"→"17 Pro", "макс"→"Pro Max", "мах"→"Pro Max", "пм"→"Pro Max"
- Игнорируй: приветствия, вопросы ("есть?", "почём?", "привет брат", "куплю")
- Samsung A-серия: "A17", "A36", "A55" — это Galaxy, НЕ iPhone и НЕ Apple chip
- Dyson: "дайсон в12" → "Dyson V12s"
- Адаптер: всегда включай мощность → "Apple 20W Adapter"

ПАМЯТЬ:
- iPhone: только Storage → "17 Pro 256" → memory:"256"
- Android/Samsung/Xiaomi/Redmi: RAM/Storage → "A36 8/256" → memory:"8/256"
- Исправляй очевидные опечатки: 257→256, 513→512
- КРИТИЧНО: "8/256" принадлежит строго своему товару.
  Если запрос "A36 8/256 и 17 Pro черный" — у 17 Pro memory:null

ЦВЕТ:
- Переводи на англ стандарт: "оранжевый"→Orange, "синий"→Blue, "космический оранжевый"→"Cosmic Orange"
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
Пример: [{"model": "17 Pro", "memory": "256", "color": "Blue", "sim": "esim"}]
Если запрос не о товарах → []"""


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
    if not _client:
        logger.warning('OpenAI API ключ не настроен, используем прямой поиск')
        return None

    user_message = f'ЗАПРОС КЛИЕНТА: "{text}"'

    try:
        logger.info(f'  [ИИ] >>> Нормализуем запрос: "{text[:80]}"')

        response = _client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': user_message},
            ],
            temperature=0,
            max_tokens=600,
            timeout=30,
        )

        content = response.choices[0].message.content.strip()

        usage = response.usage
        logger.info(f'  [ИИ] <<< Ответ: {content}')
        logger.info(f'  [ИИ] <<< Токены: вход={usage.prompt_tokens}, выход={usage.completion_tokens}, всего={usage.total_tokens}')

        # убираем markdown-обёртку если ИИ добавил ```json ... ```
        if content.startswith('```'):
            content = content.split('\n', 1)[1]
            content = content.rsplit('```', 1)[0]

        result = json.loads(content)

        if not isinstance(result, list):
            logger.error(f'ИИ вернул не массив: {content}')
            return None

        # фильтруем объекты без модели
        result = [item for item in result if isinstance(item, dict) and item.get('model')]

        logger.info(f'  [ИИ] Нормализовано: {len(result)} товар(ов)')
        for item in result:
            q = build_search_query(item)
            logger.info(f'    → "{q}" | sim={item.get("sim")}')

        return result

    except json.JSONDecodeError as e:
        logger.error(f'ИИ вернул невалидный JSON: {e}')
        return None
    except Exception as e:
        logger.error(f'Ошибка OpenAI API: {e}')
        return None

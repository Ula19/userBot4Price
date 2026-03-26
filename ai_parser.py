"""
ИИ-парсер запросов через OpenAI API.

Получает весь прайс + запрос юзера,
возвращает список товаров из прайса которые подходят под запрос.
"""
import os
import json
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

# инициализация клиента OpenAI
_api_key = os.getenv('OPENAI_API_KEY')
_client = OpenAI(api_key=_api_key) if _api_key else None

SYSTEM_PROMPT = """Ты — помощник магазина электроники. 
Тебе дан прайс-лист и запрос клиента.

Найди в прайсе товары, которые ТОЧНО соответствуют запросу клиента.
Клиент может писать на русском, сокращать слова, использовать сленг — 
ты должен понять что он имеет в виду и сопоставить с прайсом.

Если клиент указал конкретные характеристики (цвет, память, тип SIM) — 
товар должен совпадать по ВСЕМ указанным характеристикам.
Если характеристика не указана — покажи все варианты.
Если товара нет в прайсе — НЕ придумывай, верни пустой массив.

ПРАВИЛА ДЛЯ SIM-КАРТ (КРИТИЧНО):
1. ВИРТУАЛЬНЫЕ (eSim, 2есим, э-сим, esimonly) → ищи товары строго с пометкой (eSim).
2. ДВЕ ФИЗИЧЕСКИЕ (2сим, 2 физ, dual sim, сим сим) → ищи товары строго с пометкой (Dual Sim) или (2 Sim).
3. ОДНА ФИЗИЧЕСКАЯ + eSIM (1физ, сим есим, обычная сим, sim) → ищи товары БЕЗ пометок (eSim) и (Dual Sim). Это стандартные версии телефонов.

Ответ: только JSON массив.
Каждый элемент: {"name": "точное название из прайса", "price": "цена из прайса"}
Если ничего не найдено: []
"""


def _format_price_list(products):
    """Форматирует список товаров в текст для промпта."""
    lines = []
    for p in products:
        lines.append(f'- {p["name"]} — {p["price"]}')
    return '\n'.join(lines)


async def find_in_price(query, products):
    """
    Ищет товары в прайсе с помощью ИИ.
    
    Args:
        query: текст запроса юзера (сырой, без обработки)
        products: список товаров из price_parser.get_all_products()
    
    Returns:
        list[dict] — найденные товары [{"name": "...", "price": "..."}]
        None — если ИИ недоступен (для fallback на старый поиск)
    """
    if not _client:
        logger.warning('OpenAI API ключ не настроен, используем старый поиск')
        return None

    if not products:
        return []

    price_text = _format_price_list(products)

    user_message = f"""НАШ ПРАЙС:
{price_text}

ЗАПРОС КЛИЕНТА: "{query}"

Найди подходящие товары из прайса."""

    try:
        # === ВРЕМЕННЫЕ ЛОГИ (убрать перед деплоем) ===
        logger.info(f'  [ИИ] >>> ОТПРАВЛЯЕМ В OpenAI:')
        logger.info(f'  [ИИ] >>> Прайс: {len(products)} товаров')
        logger.info(f'  [ИИ] >>> Запрос: "{query}"')
        # === КОНЕЦ ВРЕМЕННЫХ ЛОГОВ ===

        response = _client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': user_message}
            ],
            temperature=0,  # без креатива, строго по факту
            max_tokens=500,
            timeout=30  # не ждём дольше 30 секунд
        )

        content = response.choices[0].message.content.strip()

        # === ВРЕМЕННЫЕ ЛОГИ (убрать перед деплоем) ===
        usage = response.usage
        logger.info(f'  [ИИ] <<< Сырой ответ: {content}')
        logger.info(f'  [ИИ] <<< Токены: вход={usage.prompt_tokens}, выход={usage.completion_tokens}, всего={usage.total_tokens}')
        # === КОНЕЦ ВРЕМЕННЫХ ЛОГОВ ===

        # убираем markdown обёртку если ИИ добавил ```json ... ```
        if content.startswith('```'):
            content = content.split('\n', 1)[1]  # убираем первую строку
            content = content.rsplit('```', 1)[0]  # убираем последнюю

        result = json.loads(content)

        if not isinstance(result, list):
            logger.error(f'ИИ вернул не массив: {content}')
            return None

        logger.info(f'  ИИ нашёл: {len(result)} товар(ов)')
        for item in result:
            logger.info(f'    → {item.get("name")} — {item.get("price")}')

        return result

    except json.JSONDecodeError as e:
        logger.error(f'ИИ вернул невалидный JSON: {e}')
        return None
    except Exception as e:
        logger.error(f'Ошибка OpenAI API: {e}')
        return None

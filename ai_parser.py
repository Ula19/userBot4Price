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

Твоя задача: найти в прайсе товары, которые ТОЧНО соответствуют запросу клиента.
Клиент пишет на русском, часто использует сленг и опечатки (особенно для SIM-карт).

ПРАВИЛА ПОИСКА:
1. Если клиент просит конкретные характеристики (цвет, память, тип SIM) — товар должен совпадать по ВСЕМ пунктам.
2. Если характеристика не указана (например, не указан цвет или тип SIM) — верни ВСЕ варианты этой модели, какие есть в прайсе.
3. Если товара вообще нет в прайсе — верни пустой массив []. Не придумывай названия.

ПРИМЕРЫ ЛОГИКИ ДЛЯ SIM-КАРТ (учись на них):
- Запрос клиента: "17 про макс 256 2есим" 
  Твоя логика: клиент хочет только виртуальные симки. Ищем товары с пометкой (eSim).
- Запрос клиента: "17 128 э-сим"
  Твоя логика: клиент хочет электронную симку. Ищем товары с пометкой (eSim).
- Запрос клиента: "17 про 512 2сим" или "две физ"
  Твоя логика: клиент хочет две физические симки. Ищем товары с пометкой (Dual Sim) или (2 Sim).
- Запрос клиента: "17 128 1физ" или "обычная сим" или "сим есим"
  Твоя логика: клиент хочет стандартную версию (1 физическая + 1 eSIM). Ищем товары с пометкой (Sim eSim) или БЕЗ пометок (eSim)/(Dual Sim).
- Запрос клиента: "17 про макс 256" (без упоминания SIM)
  Твоя логика: клиент не указал тип SIM. Ищем ВСЕ варианты (и eSim, и Dual Sim, и обычные).

Ответ: строго JSON-массив.
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

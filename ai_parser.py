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

SYSTEM_PROMPT = """Ты — помощник магазина электроники. Твоя задача: найти в нашем прайсе товары, которые ТОЧНО соответствуют запросу клиента.

ПРАВИЛА:
1. Сравни запрос клиента с каждым товаром в прайсе
2. Верни ТОЛЬКО товары, которые точно совпадают по ВСЕМ указанным характеристикам (модель, память, цвет, тип SIM)
3. Если клиент не указал какую-то характеристику (например цвет) — покажи все варианты этого товара
4. Если клиент просит товар которого НЕТ в прайсе — НЕ показывай ничего похожего
5. Если запрос про аксессуар (чехол, стекло, кейс, наушники) — верни пустой массив []

СЛОВАРЬ:
- "про", "п" = Pro
- "макс", "мах", "пм" = Pro Max
- "белый", "вайт", "white" = Silver (для iPhone)
- "синий", "блу" = Blue
- "оранж", "оранжевый" = Orange
- "черный", "блек" = Black
- "зеленый" = Green
- "серый" = Gray
- "розовый" = Pink
- "лаванда", "лавандовый" = Lavender
- "sim", "1 сим", "1физ", "физ+есим" = тип Sim eSim
- "esim", "есим", "е-сим" = тип eSim
- "2 sim", "2 сим", "dual sim" = тип Sim Sim

ФОРМАТ ОТВЕТА: только JSON массив, без пояснений.
Каждый элемент: {"name": "точное название из прайса", "price": "цена из прайса"}
Если ничего не найдено: []"""


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
        response = _client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': user_message}
            ],
            temperature=0,  # без креатива, строго по факту
            max_tokens=500,
            timeout=10  # не ждём дольше 10 секунд
        )

        content = response.choices[0].message.content.strip()

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

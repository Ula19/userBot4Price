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

SYSTEM_PROMPT = """Ты — нормализатор запросов для магазина электроники.
Клиент пишет неформально: с опечатками, сленгом, на русском и латинице вперемешку.

Товары в магазине: смартфоны (iPhone, Samsung, Xiaomi и другие), техника Dyson, аксессуары (адаптеры, кабели, зарядки), ноутбуки и прочая электроника.

Твоя задача: разумно понять что хочет клиент и вернуть структурированный результат.

Для каждого товара верни JSON-объект с полями:
- "model": модель товара на английском. Исправляй опечатки, переводи сленг ("прошечка"→"17 Pro", "макс"→"Pro Max", "дайсон в12"→"Dyson V12s"). Только модель — без памяти и цвета.
- "memory": объём памяти числом (например "256", "128"). Исправляй очевидные опечатки (257→256, 513→512). null если не указано.
- "color": цвет на английском стандартном названии (Blue, Black, White, Silver, Orange, Pink, Green, Gray, Lime и т.д.). null если не указано.
- "sim": тип SIM-слота если клиент упомянул. Значения строго:
  - "esim" — только виртуальная SIM (без физической). Сюда входит: один eSIM, два eSIM ("2есим", "2esim", "dual esim", "два есима") и любые вариации виртуальных слотов.
  - "sim_esim" — одна физическая SIM + одна виртуальная eSIM. Например: "1сим+есим", "sim+esim", "физ+виртуал", "nano+esim".
  - "sim_sim" — только физические SIM-карты, без eSIM. Например: "2сим", "2sim", "dual sim", "два физических", "нано+нано".
  - null — SIM не упомянут вообще.
  Ключ: если клиент говорит о "есим" / "esim" — это всегда "esim", даже если их два. "Два есима" ≠ два физических слота.

Принципы:
- Игнорируй приветствия, вопросы и вводные слова ("привет", "брат", "у тебя есть", "покажи" и т.п.) — они не характеристики товара
- Если несколько товаров — верни массив объектов. НЕ переноси параметры одного товара (цвет, память, SIM) на другой
- НЕ додумывай то чего клиент не писал: нет цвета → color: null, нет памяти → memory: null
- Символ "/" означает "или": клиент допускает любой из вариантов. Верни отдельный объект для каждой комбинации. Пример: "черный/серый эсим/сим-эсим" → 4 объекта: {Black, esim}, {Black, sim_esim}, {Gray, esim}, {Gray, sim_esim}
- Фразы "по наличию", "любой", "не важно", "любой цвет", "любой сим" означают что параметр клиенту не важен → ставь null для этого поля
- Samsung Galaxy A-серия: модели вида "A07", "A17", "A26", "A36", "A55" и т.п. — это Samsung Galaxy, НЕ iPhone и НЕ Dyson. Сохраняй название как есть: model = "A17", "A36". НЕ путай с чипами Apple (Apple A17 chip ≠ Samsung Galaxy A17).
- Слеш в цвете для Dyson-продуктов: ТОЛЬКО у продуктов Dyson слеш "/" в цвете может быть частью официального названия ("Vinca Blue/Topaz" → один объект, color: "Vinca Blue/Topaz"). Для ВСЕХ телефонов (iPhone, Samsung, Redmi, Xiaomi и др.) слеш в цвете — это ВСЕГДА два варианта → два отдельных объекта. Пример: "A36 Lime/White" → {A36, Lime} и {A36, White}.
- Формат памяти "RAM/Storage": для ноутбуков (MacBook) и Android-смартфонов (Samsung, Xiaomi, Redmi и др.) память записывается как "RAM/Storage". Оба числа важны — сохраняй целиком: "A26 5G 8/256" → memory: "8/256", "Redmi 15 6/128GB" → memory: "6/128". ИСКЛЮЧЕНИЕ: iPhone — только Storage: "17 Pro 256" → memory: "256". КРИТИЧНО: число из RAM/Storage принадлежит СТРОГО своему товару. Если в запросе "A36 8/256 ... 17 Pro black" — у 17 Pro память НЕ указана (memory: null), "256" из "8/256" — это память A36, а не iPhone.
- Адаптеры и зарядки: мощность (ватты) — обязательная часть model. "20w adapter", "20в", "20w apple" → model: "Apple 20W Adapter". Никогда не теряй мощность из названия.

Формат ответа: строго JSON-массив без текста до/после.
ОБЯЗАТЕЛЬНО: каждый объект должен содержать ВСЕ 4 поля: model, memory, color, sim — даже если значение null.
Пример: [{"model": "17 Pro", "memory": "256", "color": "Blue", "sim": "esim"}]
Если запрос не о товарах: []"""


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

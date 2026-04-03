import re
import logging
from rapidfuzz import fuzz
import price_parser

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# НОВЫЙ ПОИСК: Структурный поиск по нормализованному JSON от AI
# ═══════════════════════════════════════════════════════════════════

# Маппинг цветов iPhone: маркетинговые/нестандартные → как в нашем прайсе
# Ключ: что может вернуть AI (lowercase). Значение: что реально есть в прайсе (lowercase).
IPHONE_COLOR_MAP = {
    # === iPhone 17 Pro / Pro Max (офиц: Cosmic Orange, Deep Blue, Silver) ===
    'blue': 'blue',
    'deep blue': 'blue',
    'mist blue': 'blue',
    'orange': 'orange',
    'cosmic orange': 'orange',
    'silver': 'silver',

    # === iPhone 17 base (офиц: Black, White, Mist Blue, Sage, Lavender) ===
    'white': 'white',
    'black': 'black',
    'lavender': 'lavender',
    'sage': 'sage',

    # === Общие маркетинговые синонимы ===
    'space black': 'black',
    'midnight': 'black',
    'midnight black': 'black',
    'starlight': 'white',
    'natural titanium': 'silver',
    'titanium': 'silver',
    'gray': 'silver',
    'grey': 'silver',

    # === Прошлые поколения (могут попасть в прайс) ===
    'green': 'green',
    'pink': 'pink',
    'rose': 'pink',
    'gold': 'gold',
    'purple': 'purple',
    'deep purple': 'purple',
    'red': 'red',
    'product red': 'red',
    'yellow': 'yellow',
}


def _detect_category(model: str) -> str:
    """Определяет категорию товара по полю model из AI-ответа."""
    m = model.lower().strip()
    if m.startswith('dyson'):                           return 'dyson'
    if m.startswith('redmi') or m.startswith('xiaomi'):  return 'redmi'
    if m.startswith('macbook'):                          return 'macbook'
    # Samsung: A-серия (A07, A36), S-серия (S25, S25 Ultra), Galaxy/Samsung префикс
    if re.match(r'^[as]\d', m) or 'galaxy' in m or 'samsung' in m:
        return 'samsung'
    if re.match(r'^\d', m):                             return 'iphone'
    if 'adapter' in m or 'адаптер' in m or re.search(r'\d+w', m, re.IGNORECASE):
        return 'adapter'
    return 'generic'


def _detect_product_category(name: str) -> str:
    """Определяет категорию товара из прайса по его названию."""
    n = name.lower().strip()
    if n.startswith('dyson'):                           return 'dyson'
    if n.startswith('redmi') or n.startswith('xiaomi'):  return 'redmi'
    if n.startswith('macbook'):                          return 'macbook'
    # Samsung: A-серия и S-серия, Galaxy/Samsung префикс
    if re.match(r'^[as]\d', n) or n.startswith('galaxy') or n.startswith('samsung'):
        return 'samsung'
    if re.match(r'^\d', n):                             return 'iphone'
    if 'адаптер' in n:                                  return 'adapter'
    return 'generic'


def _normalize_iphone_color(color):
    """Нормализует цвет iPhone к формату прайса через IPHONE_COLOR_MAP."""
    if not color:
        return None
    return IPHONE_COLOR_MAP.get(color.lower(), color.lower())


def _parse_iphone_product(name):
    """
    Парсит название iPhone из прайса.
    '17 Pro 256 Blue (eSim)' → {num:'17', series:'pro', storage:'256', color:'blue', sim:'esim'}
    '17 Pro Max 256 Orange (Sim eSim)' → {num:'17', series:'pro_max', storage:'256', color:'orange', sim:'sim_esim'}
    """
    # Шаг 1: извлечь SIM из скобок
    sim = None
    sim_match = re.search(r'\(([^)]+)\)', name)
    if sim_match:
        sim_text = sim_match.group(1).lower().replace(' ', '')
        if 'simesim' in sim_text:
            sim = 'sim_esim'
        elif 'dualsim' in sim_text or 'simsim' in sim_text:
            sim = 'sim_sim'
        elif 'esim' in sim_text:
            sim = 'esim'
        name = name[:sim_match.start()].strip()

    # Шаг 2: NUM [Pro] [Max] STORAGE COLOR
    match = re.match(
        r'^(\d+\w*)\s*(Pro\s*Max|Pro)?\s*(\d+(?:tb)?)\s+(.+)$',
        name, re.IGNORECASE
    )
    if not match:
        return None

    num = match.group(1).lower()
    series_raw = (match.group(2) or '').lower().replace(' ', '')
    storage = match.group(3).lower()
    color = match.group(4).strip().lower()

    if 'promax' in series_raw:
        series = 'pro_max'
    elif 'pro' in series_raw:
        series = 'pro'
    else:
        series = 'base'

    return {
        'num': num,
        'series': series,
        'storage': storage,
        'color': color,
        'sim': sim,
    }


def _parse_iphone_query_model(model):
    """
    Парсит модель iPhone из AI-ответа.
    '17 Pro'     → ('17', 'pro')
    '16e'        → ('16e', 'base')
    '17 Pro Max' → ('17', 'pro_max')
    """
    m = model.lower().strip()
    if 'pro max' in m:
        series = 'pro_max'
    elif 'pro' in m:
        series = 'pro'
    else:
        series = 'base'

    num_match = re.match(r'^(\d+\w*)', m)
    num = num_match.group(1) if num_match else m
    return (num, series)


def _parse_samsung_product(name):
    """
    Парсит название Samsung из прайса.
    'A36 8/256 Lime'          → {model:'a36', sub:None, memory:'8/256', color:'lime'}
    'A26 5G 6/128 Black'      → {model:'a26', sub:'5g', memory:'6/128', color:'black'}
    'S25 Ultra 12/512 Black'  → {model:'s25', sub:'ultra', memory:'12/512', color:'black'}
    'S25+ 12/256 Blue'        → {model:'s25+', sub:None, memory:'12/256', color:'blue'}
    """
    match = re.match(
        r'^([AS]\d+\+?)\s*(5G|Ultra)?\s*(\d+/\d+)\s+(.+)$',
        name, re.IGNORECASE
    )
    if not match:
        return None
    return {
        'model': match.group(1).lower(),
        'sub': (match.group(2) or '').lower() or None,
        'memory': match.group(3),
        'color': match.group(4).strip().lower(),
    }


def _parse_redmi_product(name):
    """
    Парсит название Redmi из прайса.
    'Redmi 15 6/128GB Midnight Black' → {model:'redmi 15', memory:'6/128', color:'midnight black'}
    """
    match = re.match(
        r'^(Redmi\s+\S+)\s+(\d+/\d+)(?:GB)?\s+(.+)$',
        name, re.IGNORECASE
    )
    if not match:
        return None
    return {
        'model': match.group(1).lower(),
        'memory': match.group(2),
        'color': match.group(3).strip().lower(),
    }


def _search_iphone(item):
    """Поиск iPhone по нормализованным полям из AI."""
    products = price_parser.get_all_products()

    q_num, q_series = _parse_iphone_query_model(item['model'])
    q_storage = item.get('memory')
    q_color = _normalize_iphone_color(item.get('color'))
    q_sim = item.get('sim')

    exact = []
    similar = []

    for product in products:
        if _detect_product_category(product['name']) != 'iphone':
            continue

        parsed = _parse_iphone_product(product['name'])
        if not parsed:
            continue

        # ФИЛЬТР 1: Номер модели (точное совпадение: 17==17, 16e==16e)
        if q_num != parsed['num']:
            continue

        # ФИЛЬТР 2: Серия (Pro / Pro Max / base — строго)
        if q_series != parsed['series']:
            continue

        # ФИЛЬТР 3: Память (если указана — точное совпадение)
        if q_storage is not None:
            if q_storage.lower() != parsed['storage']:
                similar.append({**product, '_reason': f'память: просили {q_storage}, есть {parsed["storage"]}'})
                continue

        # ФИЛЬТР 4: Цвет (через IPHONE_COLOR_MAP)
        if q_color is not None:
            if q_color != parsed['color']:
                similar.append({**product, '_reason': f'цвет: просили {q_color}, есть {parsed["color"]}'})
                continue

        # ФИЛЬТР 5: SIM (если указан — точное совпадение)
        if q_sim is not None:
            if q_sim != parsed['sim']:
                similar.append({**product, '_reason': f'SIM: просили {q_sim}, есть {parsed.get("sim", "?")}'})
                continue

        exact.append(product)

    return {'exact': exact, 'similar': similar[:5]}


def _search_samsung(item):
    """Поиск Samsung A/S-серии по нормализованным полям."""
    products = price_parser.get_all_products()

    q_model_raw = item['model'].lower()
    # Убираем префиксы Galaxy/Samsung если есть
    q_model_raw = re.sub(r'^(galaxy|samsung)\s*', '', q_model_raw).strip()
    # Извлекаем базовую модель (A36, S25, S25+) и суффикс (5G, Ultra)
    q_base_match = re.match(r'^([as]\d+\+?)', q_model_raw)
    q_base = q_base_match.group(1) if q_base_match else q_model_raw
    q_sub = None
    if '5g' in q_model_raw:    q_sub = '5g'
    if 'ultra' in q_model_raw: q_sub = 'ultra'
    q_memory = item.get('memory')
    q_color = item.get('color', '').lower() if item.get('color') else None

    exact = []
    similar = []

    for product in products:
        if _detect_product_category(product['name']) != 'samsung':
            continue

        parsed = _parse_samsung_product(product['name'])
        if not parsed:
            continue

        # ФИЛЬТР 1: Базовая модель (a36 == a36)
        if q_base != parsed['model']:
            continue

        # ФИЛЬТР 1.5: Суффикс 5G (если указан в запросе)
        if q_sub is not None and q_sub != parsed.get('sub'):
            continue

        # ФИЛЬТР 2: Память
        if q_memory is not None:
            p_mem = parsed['memory']
            if q_memory != p_mem:
                storage_part = p_mem.split('/')[-1] if '/' in p_mem else p_mem
                if q_memory != storage_part:
                    similar.append({**product, '_reason': f'память: просили {q_memory}, есть {p_mem}'})
                    continue

        # ФИЛЬТР 3: Цвет
        if q_color is not None:
            if q_color != parsed['color']:
                similar.append({**product, '_reason': f'цвет: просили {q_color}, есть {parsed["color"]}'})
                continue

        exact.append(product)

    return {'exact': exact, 'similar': similar[:5]}


def _search_redmi(item):
    """Поиск Redmi/Xiaomi по нормализованным полям."""
    products = price_parser.get_all_products()

    q_model = item['model'].lower()
    q_memory = item.get('memory')
    q_color = item.get('color', '').lower() if item.get('color') else None

    exact = []
    similar = []

    for product in products:
        if _detect_product_category(product['name']) != 'redmi':
            continue

        parsed = _parse_redmi_product(product['name'])
        if not parsed:
            continue

        # ФИЛЬТР 1: Модель (redmi 15 == redmi 15)
        if q_model != parsed['model']:
            continue

        # ФИЛЬТР 2: Память
        if q_memory is not None:
            p_mem = parsed['memory']
            if q_memory != p_mem:
                storage_part = p_mem.split('/')[-1] if '/' in p_mem else p_mem
                if q_memory != storage_part:
                    similar.append({**product, '_reason': f'память: просили {q_memory}, есть {p_mem}'})
                    continue

        # ФИЛЬТР 3: Цвет
        if q_color is not None:
            p_color = parsed['color']
            if q_color != p_color and not p_color.endswith(q_color):
                similar.append({**product, '_reason': f'цвет: просили {q_color}, есть {p_color}'})
                continue

        exact.append(product)

    return {'exact': exact, 'similar': similar[:5]}


def _search_dyson(item):
    """Поиск Dyson — fuzzy matching по модели + цвету."""
    products = price_parser.get_all_products()

    q_full = item['model']
    if item.get('color'):
        q_full += ' ' + item['color']
    q_lower = q_full.lower()

    exact = []
    similar = []

    for product in products:
        if _detect_product_category(product['name']) != 'dyson':
            continue

        p_lower = product['name'].lower()
        score = fuzz.token_set_ratio(q_lower, p_lower)

        if score >= 85:
            exact.append(product)
        elif score >= 60:
            similar.append({**product, '_score': score})

    similar.sort(key=lambda x: x.get('_score', 0), reverse=True)
    return {'exact': exact, 'similar': similar[:5]}


def _search_adapter(item):
    """Поиск адаптеров — keyword matching."""
    products = price_parser.get_all_products()

    exact = []
    for product in products:
        if _detect_product_category(product['name']) != 'adapter':
            continue
        exact.append(product)

    return {'exact': exact, 'similar': []}


def _search_generic(item):
    """Fallback: fuzzy поиск для неизвестных категорий (MacBook и др.)."""
    products = price_parser.get_all_products()

    q = item.get('model', '')
    if item.get('memory'):
        q += ' ' + item['memory']
    if item.get('color'):
        q += ' ' + item['color']
    q_lower = q.lower()

    exact = []
    similar = []

    for product in products:
        score = fuzz.token_set_ratio(q_lower, product['name'].lower())
        if score >= 85:
            exact.append(product)
        elif score >= 60:
            similar.append({**product, '_score': score})

    similar.sort(key=lambda x: x.get('_score', 0), reverse=True)
    return {'exact': exact, 'similar': similar[:5]}


def find_by_normalized(item):
    """
    Главная точка входа нового поиска.
    Принимает нормализованный JSON от AI, возвращает {exact: [...], similar: [...]}.
    """
    model = item.get('model', '')
    category = _detect_category(model)

    logger.info(
        f'  [Поиск] AI вернул: model="{model}" mem={item.get("memory")} '
        f'color={item.get("color")} sim={item.get("sim")} → категория: {category}'
    )

    if category == 'iphone':
        result = _search_iphone(item)
    elif category == 'samsung':
        result = _search_samsung(item)
    elif category == 'redmi':
        result = _search_redmi(item)
    elif category == 'dyson':
        result = _search_dyson(item)
    elif category == 'adapter':
        result = _search_adapter(item)
    else:
        result = _search_generic(item)

    # === ПОДРОБНЫЕ ЛОГИ (убрать перед деплоем) ===
    if result['exact']:
        logger.info(f'  [Поиск] ✅ Найдено {len(result["exact"])} товар(ов):')
        for p in result['exact']:
            logger.info(f'    → {p["name"]} — {p["price"]}')
    elif result['similar']:
        logger.info(f'  [Поиск] ⚠️ Точных нет, похожих: {len(result["similar"])}')
        for p in result['similar']:
            logger.info(f'    ~ {p["name"]} — {p["price"]}')
    else:
        logger.info(f'  [Поиск] ❌ Ничего не найдено')
    # === КОНЕЦ ЛОГОВ ===

    return result

import re
import logging
from rapidfuzz import fuzz
import price_parser
import aliases as aliases_module

logger = logging.getLogger(__name__)

# базовые алиасы (захардкожены)
# заказчик может добавить свои через Телеграм
ALIASES = {
    'айфон': 'iphone',
    'про': 'pro',
    'макс': 'max',
    'мах': 'max',
    'дайсон': 'dyson',
    'редми': 'redmi',
    # цвета - полные и короткие
    'серебро': 'silver',
    'серебряный': 'silver',
    'сильвер': 'silver',
    'оранжевый': 'orange',
    'оранж': 'orange',
    'синий': 'blue',
    'блю': 'blue',
    'голубой': 'blue',
    'черный': 'black',
    'черн': 'black',
    'белый': 'white',
    'золотой': 'gold',
    'золото': 'gold',
    'лавандовый': 'lavender',
    'лаванда': 'lavender',
    'зеленый': 'green',
    'розовый': 'pink',
}

# слова которые нужно убрать из запроса (мусор)
STOP_WORDS = [
    'куплю', 'купить', 'нужен', 'нужна', 'нужно',
    'предложите', 'есть', 'ищу', 'хочу', 'надо',
    'цвет', 'по', 'наличию',
    'apple', 'iphone', 'gb', 'гб',
]

# --- SIM-карты ---
# паттерны для определения типа SIM карты из запроса юзера
# порядок важен! проверяем от более специфичных к менее

# (eSim) — только виртуальные сим карты
ESIM_PATTERNS = [
    r'есим[\s\-]*есим',     # есим-есим, есим есим
    r'esim[\s\-]*esim',     # esim-esim, esim esim
    r'2\s*esim',            # 2esim, 2 esim
    r'2\s*есим',            # 2есим, 2 есим
    r'2\s*е[\s\-]*сим',     # 2е-сим, 2 е сим, 2е сим
    r'2\s*e[\s\-]*sim',     # 2e-sim, 2 e sim
    r'2\s*вирт',            # 2вирт
    r'\bе[\s\-]сим\b',      # е-сим, е сим
    r'\be[\s\-]sim\b',      # e-sim, e sim
    r'\bесим\b',            # есим
    r'\besim\b',            # esim
    r'\bвирт\b',            # вирт
]

# (Sim Sim) — две физические сим карты
SIM_SIM_PATTERNS = [
    r'сим[\s\-]*сим',       # сим-сим, сим сим
    r'sim[\s\-]*sim',       # sim-sim, sim sim
    r'2\s*сим',             # 2сим, 2 сим
    r'2\s*sim',             # 2sim, 2 sim
    r'2\s*физ',             # 2физ, 2 физ
    r'2\s*nano',            # 2nano, 2 nano
    r'2\s*nano[\s\-]*sim',  # 2nano-sim, 2nano sim
    r'дуал[\s\-]*сим',      # дуал-сим, дуал сим
    r'dual[\s\-]*sim',      # dual-sim, dual sim
]

# (Sim eSim) — одна физическая + одна виртуальная
SIM_ESIM_PATTERNS = [
    r'sim[\s\-]*e\s*sim',   # sim-esim, sim esim, sim-e sim, sim e sim
    r'sim[\s\-]*есим',      # sim-есим, sim есим
    r'сим[\s\-]*е\s*сим',   # сим-есим, сим есим
    r'сим[\s\-]*физ',       # сим-физ
    r'физ[\s\-]*сим',       # физ-сим, физ сим
    r'физическ\w*\s*сим',   # физическая сим карта, физическая сим
    r'nano[\s\-]*sim',      # nano-sim, nano sim
    r'1\s*сим',             # 1сим, 1 сим
    r'1\s*sim',             # 1sim, 1 sim
    r'1\s*физ',             # 1физ
    # одиночные — если просто "сим" или "sim" без цифр и пар
    r'\bсим\b',             # сим
    r'\bsim\b',             # sim
    r'\bфиз\b',             # физ
]

# одиночные esim паттерны — НЕ должны ловить sim-esim
ESIM_SINGLE_PATTERNS = [
    r'(?<!sim[\s\-])е[\s\-]*сим',    # е-сим, е сим (но не сим-есим)
    r'(?<!sim[\s\-])e[\s\-]*sim',     # e-sim, e sim (но не sim-esim)
    r'(?<!\w)есим(?!\w)',             # есим (отдельное слово)
    r'(?<!\w)esim(?!\w)',             # esim (отдельное слово)
    r'\bвирт\b',                      # вирт
]


def _detect_sim_type(text):
    """
    определяет какой тип SIM ищет юзер
    проверяем от более специфичных паттернов к менее
    """
    text = text.lower()

    # 0. SIM+eSIM compound (sim-esim, sim+esim, 1sim+e-sim) — САМЫЙ ПЕРВЫЙ!
    # иначе \besim\b словит esim внутри sim-esim
    if re.search(r'\d*sim[\s\-\+]*e[\s\-]*sim|\d*сим[\s\-\+]*е[\s\-]*сим|\d*sim[\s\-\+]*есим', text):
        return 'sim_esim'

    # 1. двойные eSIM (2esim, есим-есим и тд)
    for pattern in ESIM_PATTERNS:
        if re.search(pattern, text):
            return 'esim'

    # 2. двойные физические SIM (2сим, сим-сим и тд)
    for pattern in SIM_SIM_PATTERNS:
        if re.search(pattern, text):
            return 'sim_sim'

    # 3. SIM+eSIM — остальные (физ-сим, nano-sim, просто сим и тд)
    for pattern in SIM_ESIM_PATTERNS:
        if re.search(pattern, text):
            return 'sim_esim'

    # 4. одиночные eSIM (только если ничего выше не сработало)
    for pattern in ESIM_SINGLE_PATTERNS:
        if re.search(pattern, text):
            return 'esim'

    return None


def _get_product_sim_type(name):
    """
    определяет тип SIM в названии товара из прайса
    '17 Pro Max 256 Silver (Sim eSim)' → 'sim_esim'
    '17 Pro Max 256 Orange (eSim)' → 'esim'
    """
    name_lower = name.lower()

    # Sim eSim — физ + виртуальная
    if re.search(r'sim\s+esim|sim\s*\+\s*esim', name_lower):
        return 'sim_esim'

    # Sim Sim — две физические
    if re.search(r'sim\s+sim|sim\s*\+\s*sim', name_lower):
        return 'sim_sim'

    # eSim — только виртуальные
    if 'esim' in name_lower:
        return 'esim'

    # Sim — одна физическая (без eSim)
    if 'sim' in name_lower:
        return 'sim'

    return None


def _filter_by_sim(products, sim_type):
    """фильтрует товары по типу SIM, если тип указан в запросе"""
    if not sim_type:
        return products

    return [p for p in products if _get_product_sim_type(p['name']) == sim_type]


def _remove_sim_words(text):
    """
    убирает слова связанные с SIM из текста запроса
    чтобы они не мешали поиску по ключевым словам
    ВАЖНО: сначала убираем составные (sim-esim), потом одиночные
    """
    # 1. сначала убираем составные паттерны (sim-esim, сим-сим и тд)
    compound_patterns = [
        r'sim[\s\-\+]*e\s*sim',   # sim-esim, sim+esim, sim esim
        r'сим[\s\-\+]*е\s*сим',   # сим-есим
        r'sim[\s\-\+]*есим',      # sim-есим
        r'есим[\s\-\+]*есим',     # есим-есим
        r'esim[\s\-\+]*esim',     # esim-esim
        r'сим[\s\-\+]*сим',       # сим-сим
        r'sim[\s\-\+]*sim',       # sim-sim, sim+sim
    ]
    for pattern in compound_patterns:
        text = re.sub(pattern, '', text)

    # 2. потом убираем одиночные SIM-паттерны
    all_patterns = (
        ESIM_PATTERNS + SIM_SIM_PATTERNS +
        SIM_ESIM_PATTERNS + ESIM_SINGLE_PATTERNS
    )
    for pattern in all_patterns:
        text = re.sub(pattern, '', text)

    # убираем количество со знаком минус (-3, -5) ДО очистки дефисов
    text = re.sub(r'-\s*\d+(?!\d)', '', text)

    # чистим лишние дефисы, плюсы и пробелы
    text = re.sub(r'(?<!\w)[\-\+]|[\-\+](?!\w)', ' ', text)  # одинокие дефисы/плюсы
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def normalize_query(text):
    """
    нормализует запрос юзера:
    - переводит в нижний регистр
    - заменяет русские слова на английские
    - убирает мусорные слова и символы
    """
    text = text.lower().strip()

    # убираем невидимые юникод-символы (braille blanks и тп из ботов)
    text = re.sub(r'[^\x00-\x7FА-Яа-яёЁ0-9]', ' ', text)

    # убираем эмодзи, знаки вопроса, восклицательные
    text = re.sub(r'[❗‼⁉❓?!]+', '', text)

    # убираем GB/ГБ после чисел (256GB → 256)
    text = re.sub(r'(\d+)\s*(?:gb|гб)', r'\1', text, flags=re.IGNORECASE)

    # убираем количество: "2 шт", "3 штуки", "-3", "x5" и тд
    text = re.sub(r'\d+\s*(?:шт\w*|pcs|штук\w*)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'[\-]\s*\d+\b', '', text)  # -3, -5 (количество со знаком минус)

    # убираем мусорные слова (apple, iphone, gb и тд)
    for word in STOP_WORDS:
        text = re.sub(rf'\b{word}\b', '', text, flags=re.IGNORECASE)

    # собираем все алиасы: захардкоженные + из Телеграма
    # телеграмные переопределяют захардкоженные
    all_aliases = {**ALIASES, **aliases_module.get_aliases()}

    # сначала заменяем многословные алиасы (deep blue → blue, cosmic orange → orange)
    multi_word = {k: v for k, v in all_aliases.items() if ' ' in k}
    # сортируем по длине — длинные первыми
    for key in sorted(multi_word.keys(), key=len, reverse=True):
        text = text.replace(key, multi_word[key])

    # потом заменяем однословные алиасы
    words = text.split()
    normalized_words = []
    for word in words:
        word = word.strip('.,;:()[]{}')
        if word in all_aliases:
            normalized_words.append(all_aliases[word])
        elif word:
            normalized_words.append(word)

    return ' '.join(normalized_words)


def _extract_numbers(text):
    """извлекает все числа из текста"""
    return set(re.findall(r'\b\d+\b', text))

def _get_series(text):
    """определяет серию: pro_max, pro, plus, base"""
    if 'pro max' in text: return 'pro_max'
    if 'pro' in text: return 'pro'
    if 'plus' in text: return 'plus'
    return 'base'

def find_products(query, sim_override=None):
    """
    Умный структурный поиск:
    вместо точного совпадения всех слов, оцениваем товары на предмет ПРОТИВОРЕЧИЙ
    с запросом по ключевым характеристикам (модель, память, серия).
    """
    products = price_parser.get_all_products()

    if not products:
        return {'exact': [], 'similar': []}

    # определяем SIM
    sim_type = sim_override or _detect_sim_type(query)

    # нормализуем запрос (чистим мусор, переводим алиасы)
    # ВАЖНО: передаем в _remove_sim_words текст в нижнем регистре
    clean_query = _remove_sim_words(query.lower())
    normalized = normalize_query(clean_query)
    query_words = normalized.split()

    if not query_words:
        return {'exact': [], 'similar': []}

    # слишком общий запрос (например просто "blue")
    too_generic = len(query_words) < 2

    # извлекаем характеристики из запроса
    query_nums = _extract_numbers(normalized)
    query_series = _get_series(normalized)

    logger.info(f'Поиск: "{query}" → "{normalized}" (SIM: {sim_type or "любой"})')

    exact = []
    similar = []

    for product in products:
        # нормализуем имя товара (для алиасов цветов и тд)
        # убираем SIM из имени чтобы не мешало
        name_no_sim = re.sub(r'\([^)]*\)', '', product['name']).lower()
        norm_product = normalize_query(name_no_sim)
        prod_words = norm_product.split()

        prod_nums = _extract_numbers(norm_product)
        prod_series = _get_series(norm_product)

        # 1. Проверка на противоречия (Штрафы)
        is_exact = True

        # А. Противоречие по числам (модель, память)
        # Если юзер указал число (например 512, 17, 13), оно ДОЛЖНО быть в товаре
        for num in query_nums:
            if num not in prod_nums:
                is_exact = False
                break

        # Б. Противоречие по серии (Pro vs Base vs Pro Max)
        # Если юзер указал конкретную серию, товар должен соответствовать
        # Если юзер НЕ указал серию (base), а товар это Pro — это противоречие
        if query_series != prod_series:
            is_exact = False

        # В. Противоречие по словам (цвета, бренды и прочее)
        # Все НЕчисловые слова из запроса должны быть в названии
        for word in query_words:
            if not word.isdigit() and word not in prod_words:
                is_exact = False
                break

        if is_exact and not too_generic:
            exact.append(product)
        else:
            # если точного совпадения нет — считаем fuzzy score
            score = fuzz.token_set_ratio(normalized, norm_product)
            if score >= 55:
                similar.append({**product, 'score': score})

    # фильтруем по SIM
    exact = _filter_by_sim(exact, sim_type)
    similar = _filter_by_sim(similar, sim_type)

    similar.sort(key=lambda x: x['score'], reverse=True)
    similar = similar[:5]

    logger.info(f'  Точных: {len(exact)}, Похожих: {len(similar)}')

    return {'exact': exact, 'similar': similar}

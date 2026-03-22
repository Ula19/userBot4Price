import re
import logging
from rapidfuzz import fuzz
import price_parser

logger = logging.getLogger(__name__)

# словарь для перевода русских слов в английские
# юзеры могут писать "айфон 17 про макс" вместо "iphone 17 pro max"
ALIASES = {
    'айфон': 'iphone',
    'iphone': 'iphone',
    'про': 'pro',
    'макс': 'max',
    'дайсон': 'dyson',
    'редми': 'redmi',
    'сим': 'sim',
    'есим': 'esim',
    'серебро': 'silver',
    'серебряный': 'silver',
    'оранжевый': 'orange',
    'синий': 'blue',
    'черный': 'black',
    'белый': 'white',
    'золотой': 'gold',
    'лавандовый': 'lavender',
}

# слова которые нужно убрать из запроса (мусор)
STOP_WORDS = [
    'куплю', 'купить', 'нужен', 'нужна', 'нужно',
    'предложите', 'есть', 'ищу', 'хочу', 'надо',
]


def normalize_query(text):
    """
    нормализует запрос юзера:
    - переводит в нижний регистр
    - заменяет русские слова на английские
    - убирает мусорные слова и символы
    """
    text = text.lower().strip()

    # убираем эмодзи, знаки вопроса, восклицательные
    text = re.sub(r'[❗‼⁉❓?!]+', '', text)

    # убираем мусорные слова
    for word in STOP_WORDS:
        text = re.sub(rf'\b{word}\b', '', text, flags=re.IGNORECASE)

    # заменяем русские слова на английские
    words = text.split()
    normalized_words = []
    for word in words:
        word = word.strip('.,;:()[]{}')
        if word in ALIASES:
            normalized_words.append(ALIASES[word])
        elif word:
            normalized_words.append(word)

    return ' '.join(normalized_words)


def _detect_sim_type(text):
    """
    определяет какой тип SIM ищет юзер
    возвращает: 'sim_esim', 'esim', 'sim', или None
    """
    text = text.lower()

    # "sim esim" или "sim+esim" — двойная сим
    if re.search(r'sim\s*\+?\s*esim|sim\s+esim', text):
        return 'sim_esim'

    # просто "esim" (без sim перед ним)
    if 'esim' in text:
        return 'esim'

    # просто "sim" (без esim)
    if 'sim' in text:
        return 'sim'

    return None


def _get_product_sim_type(name):
    """
    определяет тип SIM в названии товара из прайса
    '17 Pro Max 256 Silver (Sim eSim)' → 'sim_esim'
    '17 Pro Max 256 Orange (eSim)' → 'esim'
    """
    name_lower = name.lower()

    if re.search(r'sim\s+esim|sim\s*\+?\s*esim', name_lower):
        return 'sim_esim'

    if 'esim' in name_lower:
        return 'esim'

    if 'sim' in name_lower:
        return 'sim'

    return None


def _filter_by_sim(products, sim_type):
    """фильтрует товары по типу SIM, если тип указан в запросе"""
    if not sim_type:
        return products

    return [p for p in products if _get_product_sim_type(p['name']) == sim_type]


def find_products(query):
    """
    ищет товары по запросу юзера

    точное совпадение = ВСЕ слова из запроса есть в названии товара
    похожее = fuzzy match с высоким score
    после поиска фильтруем по типу SIM
    """
    products = price_parser.get_all_products()

    if not products:
        return {'exact': [], 'similar': []}

    # нормализуем запрос
    normalized = normalize_query(query)
    query_words = normalized.split()

    if not query_words:
        return {'exact': [], 'similar': []}

    # определяем тип SIM из запроса
    sim_type = _detect_sim_type(normalized)

    logger.info(f'Поиск: "{query}" → "{normalized}" (SIM: {sim_type or "любой"})')

    exact = []
    similar = []

    for product in products:
        product_name_lower = product['name'].lower()

        # проверяем точное совпадение - все слова запроса есть в названии
        all_words_match = all(
            word in product_name_lower
            for word in query_words
        )

        if all_words_match:
            exact.append(product)
        else:
            # считаем fuzzy score для похожих
            score = fuzz.token_set_ratio(normalized, product_name_lower)
            if score >= 55:
                similar.append({**product, 'score': score})

    # фильтруем по типу SIM если указан в запросе
    exact = _filter_by_sim(exact, sim_type)
    similar = _filter_by_sim(similar, sim_type)

    # сортируем похожие по score (лучшие первыми)
    similar.sort(key=lambda x: x['score'], reverse=True)

    # берем максимум 5 похожих
    similar = similar[:5]

    logger.info(f'  Точных: {len(exact)}, Похожих: {len(similar)}')

    return {'exact': exact, 'similar': similar}

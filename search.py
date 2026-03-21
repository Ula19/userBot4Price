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


def find_products(query):
    """
    ищет товары по запросу юзера
    
    возвращает словарь:
    {
        'exact': [список точных совпадений],
        'similar': [список похожих если точных нет]
    }
    
    точное совпадение = ВСЕ слова из запроса есть в названии товара
    похожее = fuzzy match с высоким score
    """
    products = price_parser.get_all_products()

    if not products:
        return {'exact': [], 'similar': []}

    # нормализуем запрос
    normalized = normalize_query(query)
    query_words = normalized.split()

    if not query_words:
        return {'exact': [], 'similar': []}

    logger.info(f'Поиск: "{query}" → "{normalized}"')

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

    # сортируем похожие по score (лучшие первыми)
    similar.sort(key=lambda x: x['score'], reverse=True)

    # берем максимум 5 похожих чтобы не спамить
    similar = similar[:5]

    logger.info(f'  Точных: {len(exact)}, Похожих: {len(similar)}')

    return {'exact': exact, 'similar': similar}

"""
проверка «есть ли модель в прайсе» ДО вызова ИИ — только для групп

зачем: в группах много запросов на модели, которых у нас нет (s25 ultra, 15 pro...),
а каждый вызов ИИ стоит ~2.5к токенов.

как работает:
  1. из прайса собираем «семейства» моделей по брендам: samsung → {a07, a56}, iphone → {16e, 17}, ...
  2. из сообщения тем же способом достаём семейства моделей (через паттерны brand_detector)
  3. решение:
       'есть'      — хоть одна модель из сообщения есть в прайсе → в ИИ
       'нет'       — все модели распознаны точно и ни одной нет в прайсе → ИИ не зовём
       'не_уверен' — модель не распознали (только бренд, странное написание) → в ИИ, как раньше

семейство = модель без памяти/цвета/версии: «S25 Ultra 12/512» → s25, «17 Pro Max 256» → 17
Redmi и Xiaomi — один бренд: «Xiaomi 15» и «Redmi 15» дают одно семейство
проверяем только iPhone, Samsung, Honor, Xiaomi; остальные бренды — всегда в ИИ
"""
import re
import logging
import price_parser
import brand_detector

logger = logging.getLogger(__name__)

# русские слова → латиница, чтобы «редми ноут 14», «икс 8 д», «фолд 7» свелись к одному виду
_WORD_FIXES = [(re.compile(p), r) for p, r in [
    (r'р[еэи]дми\w*', 'redmi'),
    (r'ноут', 'note'),
    (r'(?<!\w)поко', 'poco'),
    (r'сяом\w*|ксиа?оми|ксяоми|сиоми|ш[аи]оми|хиаоми', 'xiaomi'),
    (r'\bми\b(?=\s?\d)', 'mi'),
    (r'(?<!\w)ми(?=\s?\d)', 'mi'),
    (r'хонор|хнор|хонр|хонер', 'honor'),
    (r'икс[\s-]?', 'x'),
    (r'м[эе]й?джик|маджик|магик', 'magic'),
    (r'самсун\w*|сам\s?сунг|самс(?![а-я])|г[аеэ]л[аеэ]кси', 'samsung'),
    (r'фолд', 'fold'),
    (r'флип', 'flip'),
    (r'(?<!\w)з(?=\s?(?:fold|flip))', 'z'),
    (r'айфон\w*|(?<![а-я])афон\w*|(?<![а-я])ифон\w*', 'iphone'),
    (r'эйр|эир|ейр|аир', 'air'),
    (r'лайт|лаит', 'lite'),
    (r'ультра', 'ultra'),
]]


def _norm(text):
    """Нижний регистр, русские слова → латиница, кириллица-двойники → латиница."""
    text = text.lower().replace('ё', 'е')
    for pattern, repl in _WORD_FIXES:
        text = pattern.sub(repl, text)
    return text.translate(brand_detector._TO_LATIN)


# ─────────────── семейство модели по найденному упоминанию ───────────────
# kind — вид паттерна brand_detector: brand (название бренда), model, number (номер/код)
# span — сам найденный текст, after — текст сразу после него (для «Samsung A56», «Redmi 15»)

# «16e», «16 e», «16 е» — но не «17 e-sim», «16 есим»
_IPHONE_E = r'(?:[^\S\n]?(e)(?![a-z])(?![^\S\n]?-?[sc][iи]m))?'


def _iphone_family(kind, span, after):
    if kind == 'brand':
        m = re.match(rf'[^\S\n]*(?:pro[\s-]?max|pro)?[^\S\n]*(?:(air)|(1[1-9]){_IPHONE_E})(?!\d)', _norm(after))
        if m:
            return m.group(1) or m.group(2) + (m.group(3) or '')
        # "ip 17", "эпл 16" — номер уже внутри найденного
        m = re.search(rf'(?<!\d)(1[1-9]){_IPHONE_E}(?!\d)', _norm(span))
        return m.group(1) + (m.group(2) or '') if m else None
    if re.search(r'(?<![a-z])air', _norm(span)):
        return 'air'
    t = _norm(span + after[:5])   # хвост нужен, чтобы увидеть «e sim» после «17 e»
    m = re.search(rf'(?<!\d)(1[1-9]){_IPHONE_E}(?:(?!\d)|(?=128|256|512))', t)
    if m:
        return m.group(1) + (m.group(2) or '')
    m = re.search(r'(?<![a-z])(xr|xs|se)(?![a-z])', _norm(span))
    return m.group(1) if m else None


def _samsung_code(t):
    if re.match(r'\s*sm-?', t):
        return None                                   # заводской код SM-S938B — не сводим к семейству
    m = re.search(r'(?<![a-z0-9])(?:z\s?)?(fold|flip)\s?(\d)(?!\d)', t)
    if m:
        return m.group(1) + m.group(2)
    m = re.search(r'(?<![a-z0-9])([asc])\s?-?(\d{2})(?!\d)', t)
    if m:
        return ('a' if m.group(1) == 'a' else 's') + m.group(2)
    m = re.search(r'(?<![a-z0-9])a(\d{2})\d(?!\d)', t)       # «A566», «A566B» → a56
    if m:
        return 'a' + m.group(1)
    m = re.search(r'(?<![\w])(2[2-6])\s?ultra', t)
    return 's' + m.group(1) if m else None


def _samsung_family(kind, span, after):
    if kind == 'brand':
        m = re.match(r'[^\S\n]*(?:samsung[^\S\n]*)?((?:z\s?)?(?:fold|flip)\s?\d(?!\d)|a\d{3}(?!\d)|[asc]\s?\d{2}(?!\d))', _norm(after))
        return _samsung_code(m.group(1)) if m else None
    return _samsung_code(_norm(span + after[:4]))


def _honor_code(t):
    m = re.search(r'(?<![a-z0-9])x\s?(\d{1,2})\s?([abcd])(?![a-z])', t)
    if m:
        return f'x{m.group(1)}{m.group(2)}'
    m = re.search(r'magic\s?(v)?\s?(\d)', t)
    if m:
        return f'magic{m.group(1) or ""}{m.group(2)}'
    m = re.search(r'(?<!\d)([2-9]00)(?!\d)', t)
    return m.group(1) if m else None


def _honor_family(kind, span, after):
    if kind == 'brand':
        m = re.match(r'[^\S\n]*(x\s?\d{1,2}\s?[abcd](?![a-z])|magic\s?v?\s?\d|[2-9]00(?!\d))', _norm(after))
        return _honor_code(m.group(1)) if m else None
    return _honor_code(_norm(span))


def _xiaomi_code(t):
    """
    Семейство Xiaomi/Redmi/Poco/Mi. Redmi ≡ Xiaomi ≡ Mi — один бренд:
    "Redmi 15", "Xiaomi 15", "Mi 15" → 'redmi 15'; "Redmi 15C" → 'redmi 15c' (другая модель)
    """
    brand = r'(?:redmi|xiaomi|(?<![a-z])mi)'
    m = re.search(r'poco\s?([xfmc])\s?(\d{1,2})(?!\d)', t)
    if m:
        return f'poco {m.group(1)}{m.group(2)}'
    m = re.search(r'note\s?(\d{1,2})(?!\d)', t)
    if m:
        return f'redmi note {m.group(1)}'
    if re.search(rf'{brand}\s?\d{{1,2}}\sc\s*\d{{1,2}}\s?/', t):
        return None                                   # «редми 15 с 4/128»: C или «с памятью» — не угадываем
    # буква после номера — отдельная модель: A5, 15C, 15T
    m = re.search(
        rf'{brand}\s?(a\s?\d{{1,2}}|\d{{1,2}}(?:\s?t(?![a-z.])|[a-z]|\sc(?=\s*(?:$|[,.!?)\n]))))(?![a-z0-9])', t)
    if m:
        return 'redmi ' + m.group(1).replace(' ', '')
    m = re.search(rf'{brand}\s?(\d{{1,2}})(?![a-z0-9])', t)
    if m:
        return f'redmi {m.group(1)}'
    m = re.search(r'(?<!\d)(1[3-5])\s?t(?![a-z.])', t)
    return f'redmi {m.group(1)}t' if m else None


def _xiaomi_family(kind, span, after):
    if kind == 'brand':
        # "Redmi 15", "Xiaomi Redmi Note 14", "Poco X7", "Xiaomi 15T" — модель сразу после бренда
        return _xiaomi_code(_norm(span + after[:16]))
    return _xiaomi_code(_norm(span))


_FAMILY = {
    'iphone': _iphone_family,
    'samsung': _samsung_family,
    'honor': _honor_family,
    'xiaomi': _xiaomi_family,
}


def model_families(text, key):
    """
    Семейства моделей бренда key в тексте.
    Возвращает (множество семейств, не_уверен): не_уверен — был номер, который не свели к модели,
    или бренд упомянут вообще без модели.
    """
    mentions = [(kind, start, end, _FAMILY[key](kind, low[start:end], low[end:end + 24]))
                for kind, start, end, low in brand_detector.iter_mentions(text, key)]
    families = {family for *_, family in mentions if family}
    known_spans = [(start, end) for _, start, end, family in mentions if family]

    unsure_number, brand_only = False, False
    for kind, start, end, family in mentions:
        # «pro max» внутри «15 pro max» — модель этого места уже распознана
        if family or any(start < e and s < end for s, e in known_spans):
            continue
        if kind == 'brand':
            brand_only = True
        else:
            unsure_number = True
    return families, unsure_number or (brand_only and not families)


# ─────────────── модели прайса ───────────────

_index = {}
_built_for = None
_built_len = -1


def _price_index():
    """
    Семейства моделей в прайсе по брендам. Пересобирается, когда прайс перезагрузился.
    Если у бренда в прайсе есть товар, модель которого не распознали, — для бренда не отсекаем (None).
    """
    global _index, _built_for, _built_len
    products = price_parser.get_all_products()
    if products is _built_for and len(products) == _built_len:
        return _index

    index = {}
    for key in _FAMILY:
        families, unclear = set(), []
        for product in products:
            found, unsure = model_families(product['name'], key)
            if not found and not unsure and key in ('samsung', 'honor'):
                # «Flip7 12/256», «400 8/256» — в прайсе без бренда: пробуем с названием бренда
                found = model_families(f'{key} {product["name"]}', key)[0]
            families |= found
            if unsure and not found:
                unclear.append(product['name'])
        if unclear:
            logger.warning(f'[Проверка прайса] {key}: не понял модель у {unclear[:5]} — для {key} не отсекаю')
            index[key] = None
        else:
            index[key] = families

    _index, _built_for, _built_len = index, products, len(products)
    summary = ' | '.join(f'{k}: {", ".join(sorted(v)) if v is not None else "не отсекаю"}' for k, v in index.items())
    logger.info(f'Модели прайса для групп: {summary}')
    return _index


def check(text, brands):
    """
    Есть ли в прайсе модель из сообщения (для разрешённых брендов группы).
    Возвращает ('есть' | 'нет' | 'не_уверен', множество семейств).
    """
    if not price_parser.get_all_products():
        return 'не_уверен', set()   # прайс не загрузился — не отсекаем
    index = _price_index()
    missing = set()
    for key in sorted(brands):
        if key not in _FAMILY:
            if brand_detector.find_brand(text, {key}):
                return 'не_уверен', set()
            continue
        families, unsure = model_families(text, key)
        if unsure or (families and index.get(key) is None):
            return 'не_уверен', families
        if key == 'honor' and any('magicv' + f[5:] in index[key] for f in families
                                  if f.startswith('magic') and not f.startswith('magicv')):
            return 'не_уверен', families          # «Мэджик 5» при Magic V5 в прайсе — скорее всего V5
        in_price = families & index[key]
        if in_price:
            return 'есть', in_price
        missing |= families
    return ('нет', missing) if missing else ('не_уверен', set())

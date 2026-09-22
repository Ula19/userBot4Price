"""
фильтры брендов для групп — задаются сообщением в канале прайса

заказчик пишет в канал прайса:

    Единственный -1001234567890 фильтр:
    Honor, Samsung

что понимаем:
- в строке есть слово «фильтр» и ID группы (-1001234567890, 1001234567890, -123456789; двоеточие не обязательно)
- ID можно написать строкой выше: "Единственный -1001234567890" ⏎ "Фильтр: Honor"
- бренды — после слова «фильтр»/ID и в следующих строках, до пустой строки
  (пустая строка сразу после заголовка блок не закрывает)
- бренды через запятую, пробел, «и», «+», списком «1. Honor», «• Samsung»; "iPhone 17" → весь iPhone
- несколько групп — несколько блоков или несколько ID в одной строке
- если фильтр одной группы написан в нескольких сообщениях — действует самое новое

в группе с фильтром отвечаем только на смартфоны перечисленных брендов (см. brand_detector.py).
группа без фильтра — молчим (и не тратим токены ИИ).
"""
import re
import logging
from brand_detector import SMARTPHONE_BRANDS, UNKNOWN_PREFIX

logger = logging.getLogger(__name__)

# длиннее — это чей-то прайс или объявление, в ИИ не отправляем
MAX_LEN = 350
MAX_LINES = 6

# слово «фильтр»/«фильтры» целиком (не «фильтром», «префильтр», «фильтр-салонный»)
_FILTER_WORD = re.compile(r'(?<![^\W_])(?<!-)фильтры?(?![^\W_])(?!-)', re.IGNORECASE)
# ID группы как в Telegram: -1001234567890, 1001234567890, -123456789
_STRICT_ID = re.compile(r'(?<![^\W_])(?:[-−–][^\S\n]?\d{9,13}|[-−–]?[^\S\n]?100(?:[^\S\n]?\d){10})(?!\d)(?!-\d)')
# «голый» ID без минуса (1234567890) — засчитываем, только если в блоке есть знакомый бренд
_WEAK_ID = re.compile(r'(?<![\w+])\d{9,10}(?!\d)(?!-\d)')

# разделители брендов: запятая, ; / + & | и
_BRAND_SPLIT = re.compile(r'[,;/+&|]|\s+(?:и|and)\s+')
# маркеры нумерации: "1.", "2)", "1️⃣"
_NUMBERING = re.compile(r'^\s*(?:\d{1,2}\s*[.)]|\d️?⃣)\s*')

# как заказчик пишет бренд → ключ ("Айфон" → 'iphone')
_NAME_TO_KEY = {
    name.replace(' ', ''): key
    for key, brand in SMARTPHONE_BRANDS.items()
    for name in brand['names']
}

# текущие фильтры: ID группы (int, как event.chat_id) → множество ключей брендов
_rules = {}
# ID сообщений канала прайса — чтобы понять, что удалили сообщение именно оттуда
_known_message_ids = set()

# ссылки на клиент и чат (для перезагрузки)
_client = None
_chat_id = None


def group_id_keys(raw_id):
    """
    ID группы из фильтра → варианты event.chat_id:
    '-1001234567890' → {-1001234567890}; '1001234567890' → {-1001234567890};
    '1234567890' (без минуса) → {-1001234567890, -1234567890} — канал/супергруппа или обычная группа
    """
    digits = re.sub(r'[^\d]', '', raw_id)
    if re.match(r'^\s*[-−–]', raw_id):
        return {-int(digits)}
    if digits.startswith('100') and len(digits) == 13:
        return {-int(digits)}
    return {-(10 ** 12 + int(digits)), -int(digits)}


def _looks_like_price(line):
    """Строка вида «Товар — 12990» (формат прайса) — это не фильтр."""
    from price_parser import PRICE_LINE_RE  # внутри: price_parser сам импортирует group_rules
    return bool(PRICE_LINE_RE.match(line))


def _brand_keys(text, warn=True):
    """
    '1. Honor, Самсунг Галакси и iPhone' → {'honor', 'samsung', 'iphone'} (весь iPhone)
    'iPhone 18' / 'айфон 18' / 'iPhone 18 Pro Max' → {'iphone:18'} (только 18-е поколение)
    """
    keys = set()
    for part in _BRAND_SPLIT.split(text):
        name = _NUMBERING.sub('', part.strip())
        name = re.sub(r'^[\W_]+|[\W_]+$', '', name.lower().replace('ё', 'е'))
        if not name:
            continue
        key = _NAME_TO_KEY.get(name.replace(' ', ''))
        if key:
            keys.add(key)
            continue
        # "Самсунг Галакси", "iPhone 18", "Honor (все модели)" — берём знакомые слова
        tokens = re.findall(r'[a-zа-я]+|\d+', name)          # "iphone18" → ['iphone', '18']
        found = {_NAME_TO_KEY[t] for t in tokens if t in _NAME_TO_KEY}
        found |= {_NAME_TO_KEY[a + b] for a, b in zip(tokens, tokens[1:]) if a + b in _NAME_TO_KEY}
        # iPhone с номером поколения — не вся линейка, а только эти поколения
        gens = [t for t in tokens if re.fullmatch(r'1[1-9]', t)]
        if 'iphone' in found and gens:
            found.discard('iphone')
            found |= {f'iphone:{gen}' for gen in gens}
        if found:
            keys |= found
        elif re.fullmatch(r'[a-z]{3,}', name):
            if warn:
                logger.warning(f'[Фильтр групп] незнакомый бренд "{name}" — ищу в сообщениях это слово как есть')
            keys.add(UNKNOWN_PREFIX + name)
        elif warn:
            logger.warning(f'[Фильтр групп] не понял "{name}" в списке брендов — пропускаю')
    return keys


def _parse_header(line, prev_ids):
    """
    Строка-заголовок → (список ID, слабый_ли_ID, текст брендов, ID_взят_из_строки_выше) или None.
    prev_ids — строгие ID из предыдущей непустой строки (формат «карточкой»).
    """
    word = _FILTER_WORD.search(line)
    if not word:
        return None

    ids, weak, from_prev = list(_STRICT_ID.finditer(line)), False, False
    if not ids:
        ids, weak = list(_WEAK_ID.finditer(line)), True
    if not ids and prev_ids and word.start() <= 2:
        return [m.group(0) for m in prev_ids], False, line[word.end():], True
    if not ids:
        return None

    # строка с ценой (после удаления ID) — это товар, а не фильтр
    if _looks_like_price(_STRICT_ID.sub(' ', _WEAK_ID.sub(' ', line))):
        return None
    end = max(word.end(), ids[-1].end())
    return [m.group(0) for m in ids], weak, line[end:], from_prev


def _parse(text, warn=True):
    """
    Разбирает сообщение канала.
    Возвращает ({ID группы как написан: множество брендов}, номера строк, занятых фильтрами).
    """
    rules, rule_lines = {}, set()
    block, prev_ids, prev_index = None, [], None

    def close(current):
        if not current:
            return
        known = {k for k in current['brands'] if not k.startswith(UNKNOWN_PREFIX)}
        if current['weak'] and not known:
            return  # "ИНН 7701234567 — фильтр: только юрлица" — не фильтр
        for raw_id in current['ids']:
            rules.setdefault(re.sub(r'\s', '', raw_id).replace('−', '-').replace('–', '-'), set()).update(current['brands'])
        rule_lines.update(current['lines'])

    for index, raw_line in enumerate((text or '').split('\n')):
        line = raw_line.strip()
        header = _parse_header(line, prev_ids)
        if header:
            close(block)
            ids, weak, rest, from_prev = header
            lines = {index, prev_index} if from_prev else {index}
            block = {'ids': ids, 'weak': weak, 'brands': _brand_keys(rest, warn), 'lines': lines}
            prev_ids = []
            continue
        if block is not None:
            if not line:
                if block['brands']:
                    close(block)
                    block = None
                continue
            if _looks_like_price(line):
                close(block)
                block = None
            else:
                block['brands'] |= _brand_keys(line, warn)
                block['lines'].add(index)
                continue
        if line:
            prev_ids = [] if _FILTER_WORD.search(line) else list(_STRICT_ID.finditer(line))
            prev_index = index

    close(block)
    return rules, rule_lines


def parse_rules_message(text):
    """Фильтры из сообщения: {ID группы как написан: множество ключей брендов}."""
    return _parse(text)[0]


def is_rules_message(text):
    """True, если в сообщении есть фильтр группы."""
    return bool(_parse(text, warn=False)[0])


def strip_rules(text):
    """Убирает из сообщения строки фильтров, чтобы price_parser не принял их за товары."""
    rule_lines = _parse(text, warn=False)[1]
    if not rule_lines:
        return text
    return '\n'.join(line for i, line in enumerate(text.split('\n')) if i not in rule_lines)


async def load_rules(client, chat_id):
    """Загружает фильтры групп из канала прайса (последние 200 сообщений)."""
    global _rules, _known_message_ids, _client, _chat_id
    _client = client
    _chat_id = chat_id
    rules, message_ids = {}, set()

    entity = 'me' if chat_id == 'me' else int(chat_id)
    # iter_messages идёт от новых к старым — первое найденное для группы и есть самое новое
    async for message in client.iter_messages(entity, limit=200):
        message_ids.add(message.id)
        if not message.text or 'фильтр' not in message.text.lower():
            continue
        for raw_id, brands in parse_rules_message(message.text).items():
            keys = group_id_keys(raw_id)
            if any(key in rules for key in keys):
                continue
            for key in keys:
                rules[key] = brands
            logger.info(f'Фильтр групп: {raw_id} → {", ".join(sorted(brands)) or "пусто (молчим)"}')

    _rules, _known_message_ids = rules, message_ids
    if not rules:
        logger.info('Фильтров групп в канале нет — в группах бот молчит')


async def reload_rules():
    """Перезагрузка фильтров при изменении в канале прайса."""
    if _client and _chat_id:
        await load_rules(_client, _chat_id)


def knows_message(message_ids):
    """True, если среди удалённых сообщений есть сообщения канала прайса."""
    return bool(_known_message_ids.intersection(message_ids or []))


def get_brands(chat_id):
    """Разрешённые бренды для группы или None, если фильтра для неё нет."""
    return _rules.get(int(chat_id))


def is_too_long(text):
    """Длинное сообщение (чужой прайс, объявление) — в ИИ не отправляем."""
    t = (text or '').strip()
    lines = [line for line in t.split('\n') if line.strip()]
    return len(t) > MAX_LEN or len(lines) > MAX_LINES

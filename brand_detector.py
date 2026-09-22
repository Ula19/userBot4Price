"""
детектор смартфонов в сообщениях групп

find_brand(text, brands) → ключ бренда из brands, если в тексте упомянут смартфон этого бренда, иначе None

как работает:
  1. _clean — нижний регистр, эмодзи/флаги → пробел и вырезаем «шум», похожий на модели:
     часы/планшеты/ноутбуки/наушники с их номерами, чехлы и кабели «для айфона»,
     павильоны (А56), время (с10 до 20), телефоны, ники, суммы в рублях, размеры одежды
  2. ищем паттерны разрешённых брендов. У паттерна есть вид:
       brand  — название бренда (айфон, samsung, хонор) — засчитываем сразу
       model  — явная серия (magic v3, sm-s938b) — засчитываем сразу
       number — «голый» номер или код (17 pro, а56, x8d) — засчитываем, только если
                прямо перед ним в той же части фразы нет чужого бренда
                («Xiaomi 17 Pro» — не айфон, «Oppo A78» — не Samsung)
  3. паттерны с latin=True ищем в тексте, где кириллица-двойники заменены латиницей (а56 → a56)
"""
import re

_EOW = r'(?![а-яa-z])'   # конец слова (цифра после — можно: "pro256")
_SP = r'[^\S\n]*'        # пробелы, но не перевод строки

# ─────────────────────────── iPhone ───────────────────────────

_GEN = r'1[1-9]'
# номер поколения: перед ним не буква/цифра ("a16", "m11" — не айфон), двоеточие можно ("Продам:17")
_HEAD = rf'(?<!\w)(?<!\d[:./]){_GEN}'
# "16ка", "17й", "17-ку", "15+ 128"
_SUFFIX = r'(?:-?(?:й|ый|ая|ую|ю|ка|ку|ки|ке)(?![а-я])|\+(?=[^\S\n]))?'

# память: не "15 640 руб", не "512 ssd"
_MEM = (r'(?:64|128|256|512|[12]\s?(?:tb|тб))(?!\d)'
        r'(?!\s*(?:руб|р(?![а-яa-z])|₽|\$|кг|kg|ssd|мм|mm))')
_SIM = r'(?:[12]\s?)?(?:e|е)?(?:sim|сим)(?![а-яa-z])|dual\s?sim|физ\.?\s?сим'
# цвета: основа слова, но не "белых", "серые коробки", "Беляево", "титанов"
_COLORS = (
    r'(?:black|white|blue|orange|silver|gold|green|pink|purple|lavender|sage|yellow|teal|'
    r'ultramarine|midnight|starlight|cosmic|desert|natural|titanium|mist|space\s?black|'
    r'deep\s?blue|sky\s?blue|cloud|burgundy|glacier|cherry)(?![a-z])|'
    r'(?:черн|бел|син|голуб|оранж|серебр|сер(?=[ыо])|золот|зелен|розов|фиолет|лаванд|желт|'
    r'бирюз(?!ов\w*х)|ультрамарин|космик|натурал|титан(?!ов\w*х)|графит|пустын|сирен|шалфе|мятн|'
    r'бордо|бургунд|вишнев|глейшер|глетчер|ледян)'
    r'(?!ых|их|ые|ие|ье|ья|яев|ьн)'
)
_IPHONE_WORDS = (
    rf'(?:pro|про)[\s-]?(?:max|макс|мах){_EOW}|'
    rf'(?:pro|про|прошк\w*|прошечк\w*|prm|прм|max|макс|мах|pm|пм|plus|плюс|mini|мини|air|эйр|эир|ейр|аир){_EOW}|'
    r'[eе](?![а-яa-z])'
)
_Q_NOMEM = rf'(?:{_IPHONE_WORDS}|{_SIM}|{_COLORS})'
_Q = rf'(?:{_Q_NOMEM}|{_MEM})'

SMARTPHONE_BRANDS = {
    'iphone': {
        'names': ['iphone', 'iphones', 'iphon', 'айфон', 'айфоны', 'apple', 'эпл', 'эппл'],
        'series': [],
        'patterns': [
            ('brand', r'iphone|iphon|ipone|i[\s-]phone|айфон|айфн|аифон|айфоун|айфан|(?<![а-я])ифон|(?<![а-я])афон(?![ия])', False),
            ('brand', rf'(?<!\w)ip[\s-]?{_GEN}(?!\d)', False),
            ('brand', rf'(?:apple|эпл|эппл){_SP}{_GEN}(?!\d)', False),
            # "17 pro", "17pm256", "16ка про", "17-pro", "16e", "17 черный"
            ('number', rf'{_HEAD}(?![\d:.,]){_SUFFIX}{_SP}(?:[-–(]{_SP})?{_Q_NOMEM}', False),
            # "17 256", "15+ 128"
            ('number', rf'{_HEAD}(?![\d:.,]){_SUFFIX}{_SP}{_MEM}', False),
            # "15/128"
            ('number', rf'{_HEAD}{_SP}/{_SP}(?:128|256|512)(?![\d/-])', False),
            # "15-128 синий" — дефис + память только с хвостом (иначе ловит телефоны)
            ('number', rf'{_HEAD}{_SP}[-–_]{_SP}(?:128|256|512)(?![\d/-]){_SP}(?:gb|гб|{_SIM}|{_COLORS})', False),
            # "17256 оранж" — слитно, только с цветом или sim
            ('number', rf'{_HEAD}(?:128|256|512)(?!\d){_SP}(?:{_SIM}|{_COLORS})', False),
            # "pro max 17", "пм 16" (но не "Pro 12/512" у Android и не "Max 17 лет")
            ('number', rf'(?<!\w)(?:pro[\s-]?max|промакс|про[\s-]?макс|pro|max|прошк\w*|pm|пм|прм){_SP}{_GEN}'
                       r'(?![\d@])(?!\s*/\s*\d|\.\d)(?!\s*(?:лет|год))', False),
            # "промакс оранж" — Pro Max без номера у смартфонов бывает только у iPhone
            ('number', rf'(?<!\w)(?:pro[\s-]?max|промакс|про[\s-]?макс){_EOW}', False),
            # "эйр 512 белый" — Air без номера, только с памятью или цветом iPhone Air
            ('number', rf'(?<!\w)(?:air|эйр|эир|ейр){_SP}(?:256|512|1\s?(?:tb|тб)|sky|скай|cloud|light\s?gold)', False),
            # старые: "xs max 256", "se 2022 64"
            ('number', r'(?<!\w)(?:xr|xs(?:\s?max)?)\s*(?:64|128|256|512)(?!\d)', False),
            ('number', r'(?<!\w)se\s?(?:20(?:20|22)|[23])\s*(?:64|128|256)(?!\d)', False),
            # "семнадцатый про 256"
            ('number', rf'(?:одиннадцат|двенадцат|тринадцат|четырнадцат|пятнадцат|шестнадцат|семнадцат|восемнадцат|семнаш|шестнаш|восемнаш)\w*{_SP}{_Q}', False),
        ],
    },
    'samsung': {
        'names': ['samsung', 'самсунг', 'самсунги', 'galaxy', 'галакси'],
        'series': [r'galaxy|г[аеэ]л[аеэ]кси', r'(?<!\w)z[\s-]?(?:fold|flip)'],
        'patterns': [
            ('brand', r'samsung|самсун\w*|сам\s?сунг|sumsung|samsumg|samsng|сумсунг|сасмунг|самсуг|galaxy|'
                      r'г[аеэ]л[аеэ]кси|(?<!\w)самс(?:ик\w*)?(?![а-я])', False),
            ('brand', r'samsung|galaxy', True),   # смешанная раскладка: "Sаmsung"
            ('model', r'(?<!\w)sm-?[asf]\d{3}', False),
            ('number', r'(?<!\w)z[\s-]?(?:fold|flip)', False),
            ('number', r'(?<!\w)з?\s?(?:фолд|флип)\s?\d', False),
            ('number', r'(?<!\w)fold\s?[3-7](?!\d)', False),
            ('number', r'(?<!\w)flip\s?[3-7]\s?(?:fe|64|128|256|512|1\s?tb)', False),
            # S-серия с суффиксом: "s25 ultra", "S25Ultra512", "с 25 ультра", "s25u", "s25+"
            ('number', r'(?<!\w)(?:s|с|эс)[\s-]?2[0-6]\s?(?:ultra|ультра|ult|ульт|fe|фе|edge|эдж)(?![а-яa-z]|-)', False),
            ('number', r'(?<!\w)[sс]2[0-6](?:[uу]|plus|плюс|\+)(?![а-яa-z])', False),
            ('number', r'(?<![\w:./])2[2-6]\s?(?:ultra|ультра)\s?(?:256|512|1\s?(?:tb|тб)|12/)', False),
            ('number', r'(?<!\w)[aа]\s(?:0[5-7]|1[5-7]|2[5-6]|3[5-6]|5[5-6])\s?(?:5g|\d{1,2}/\d{3})', False),
            # коды A/S: a56, а07, s24 (только реальные номера серий)
            ('number', r'(?<!\w)(?:a(?:0\d|[1-3]\d|5\d|7\d)(?!\s?(?:pro|bionic))|s(?:10|2\d))'
                       r'(?:\+|\s?(?:ultra|plus|fe|5g|edge))?(?!\w)', True),
            # "A56x2", "2xA56", "a56black" — количество или цвет вплотную к коду
            ('number', r'(?<![a-wyz\d])a(?:0\d|[1-3]\d|5\d|7\d)(?=x\d|black|blue|gr[ae]y|olive|pink|mint|navy)|(?<!\w)\dxa(?:0\d|[1-3]\d|5\d|7\d)(?!\w)', True),
            # кириллическая «с25» (латинская C — это Poco/Realme/Nokia)
            ('number', r'(?<!\w)с(?:10|2\d)(?:\+|\s?(?:ultra|ультра|plus|плюс|fe|фе|5g))?(?![а-яa-z0-9])', False),
        ],
    },
    'honor': {
        'names': ['honor', 'хонор', 'хоноры'],
        'series': [r'magic|м[эе]й?джик'],
        'patterns': [
            ('brand', r'honor(?!able)|хонор(?!ар)|(?<!\w)(?:хнор|хонр|хонер|honr|hnor|honer|honour)(?!\w)|'
                      r'[хx][оo][нh][оo][рp](?!ар)|[hн][оo]n[оo][rр](?!able)', False),
            ('model', r'magic\s?(?:v\s?)?\d(?!\s?ball)|magic\s?v\s?flip|м[эе]й?джик\s?(?:v\s?|в)?\d|'
                      r'(?:маджик|магик)\s?(?:v\s?\d|в\d|[6-9](?!\d)\s?(?:pro|про|lite|лайт|\d{1,2}\s?/))', False),
            ('number', r'(?<!\w)[2-9]00[\s-]?(?:lite|лайт|лаит)(?![а-яa-z])', False),
            ('number', r'(?<![\w.,:/-])(?<!\d\s)[246]00\s?(?:pro|про|smart|смарт)'
                       r'(?=\s*(?:\d{1,2}\s?[/+]\s?)?(?:64|128|256|512|1\s?(?:tb|тб))(?!\d))', False),
            ('number', r'икс[\s-]?\d{1,2}\s?(?:[абдabcd]|ди|дэ|си|би|бэ|с(?!\s*[а-я]))(?![а-яa-z])', False),
            # x8d, х9с (после транслита), x8d256
            ('number', r'(?<!\w)x[5-9][abcd](?:(?!\w)|(?=(?:\d{1,2}\s?[/+]\s?)?(?:64|128|256|512|1\s?(?:tb|тб))(?!\d)))', True),
            ('number', r'(?<!\w)x[5-9]\s?d\s*(?=(?:\d{1,2}\s?[/+]\s?)?(?:64|128|256|512|1\s?(?:tb|тб))(?!\d)(?!\s*л))', True),
        ],
    },
    'xiaomi': {
        'names': ['xiaomi', 'сяоми', 'ксиоми', 'ксиаоми', 'ксяоми', 'шаоми', 'хиаоми', 'redmi', 'редми', 'poco', 'поко'],
        'series': [r'note|ноут', r'(?<!\w)(?:mi|ми)(?=\s?\d)'],
        'patterns': [
            ('brand', r'xiaomi|redmi|сяом\w*|ксиа?оми|ксяоми|сиоми|ш[аи]оми|хиаоми|р[еэи]дми\w*|xiomi|xaomi|xioami', False),
            ('brand', r'(?<!\w)(?:poco|поко)(?:\s?[xfmcхфмс]\d|фон|phone|(?![а-яa-z]))', False),
            ('brand', r'xiaomi|redmi|(?<!\w)poco\s?[xfmc]\d', True),
            ('number', r'(?<!\w)(?:mi|ми)\s?1[0-5](?:t|т|\s?(?:pro|про|ultra|ультра|lite|лайт))(?![а-яa-z])', False),
            # "Mi 15 8/256" — без суффикса, но с памятью (иначе ловит "ми 15 минут")
            ('number', r'(?<!\w)(?:mi|ми)\s?1[0-5]\s?\d{1,2}\s?/\s?\d{2,4}(?!\d)', False),
            ('number', r'(?<!\w)(?:note|ноут)\s?1[2-5]\s?(?:s|с|pro|про)?\s?(?:\+|plus|плюс|5g)(?![а-яa-z])', False),
            ('number', r'(?<![\w:./])1[3-5]t\s?(?:pro|ultra)(?![а-яa-z])', False),
        ],
    },
    'pixel': {
        'names': ['pixel', 'google', 'гугл', 'пиксель'],
        'series': [],
        'patterns': [
            ('brand', r'(?:google|гугл)\s?(?:pixel|пиксел\w*)|pixel|'
                      r'(?<![а-я])пиксел[ья]?\s?(?:\d{1,2}(?!\d)|pro\s?\d|про\s?\d|про\s?(?:xl|хл)|fold|фолд)', False),
            ('number', r'(?<!\w)(?:google|гугл)\s?\d{1,2}\s?(?:a|а|pro|про|xl|хл|fold|фолд)(?![а-яa-z])', False),
        ],
    },
    'huawei': {
        'names': ['huawei', 'хуавей', 'хуавэй'],
        'series': [r'(?<!\w)(?:pura|пура|mate|мейт|мэйт|nova|нова)(?!\w)'],
        'patterns': [
            ('brand', r'huawe[iy]|хуав[еэа]й|хуав[еэ]и|хавей', False),
            ('number', r'(?<!\w)(?:pura|пура)\s?[789]0(?:\s?(?:pro|про|ultra|ультра|\+))', False),
            ('number', r'(?<!\w)(?:mate|мейт|мэйт)\s?(?:[5-8]0|x\d|х\d)(?!\d)', False),
            ('number', r'(?<!\w)nova\s?1[1-4](?:s|\s?pro|\s?ultra|\s?\d{1,2}/\d)', False),
        ],
    },
    'realme': {
        'names': ['realme', 'реалми', 'риалми'],
        'series': [r'(?<!\w)gt(?=\s?\d)'],
        'patterns': [
            ('brand', r'realm[ei]|реалм[иеі]|риалм[иеі]|рилми|(?<!\w)real\s?me\s?(?:gt|c\d|\d)', False),
        ],
    },
    'oneplus': {
        'names': ['oneplus', 'one plus', 'ванплас', 'ван плюс'],
        'series': [r'(?<!\w)nord(?!\w)'],
        'patterns': [
            ('brand', r'one\s?plus|(?<![а-я])(?:ван|уан)\s?пл(?:юс|ас)|оне\s?плюс', False),
            ('number', r'(?<!\w)nord\s?(?:ce\s?\d|\d)(?:\s?lite|\s?\d{1,2}/\d)', False),
            ('number', r'(?<![\w+])1\+\s?1[1-5]r?\s?(?:\d{1,2}/\d|pro|про)', False),
        ],
    },
    'tecno': {
        'names': ['tecno', 'текно', 'техно'],
        'series': [r'(?<!\w)(?:spark|спарк|camon|камон|pova|пова|phantom|фантом)(?!\w)'],
        'patterns': [
            ('brand', r'tecno|(?<!\w)(?:текно|тэкно)(?![а-я])|(?<!\w)техно\s?(?:спарк|камон|пова|spark|camon|pova)', False),
            ('number', r'(?<!\w)(?:spark|спарк|camon|камон)\s?\d{2}(?:c|с|\s?pro|\s?про|\s?premier|\s?\d{1,3}(?!\d))', False),
            ('number', r'(?<!\w)(?:pova|пова)\s?\d\s?(?:pro|про|neo|5g|\d{1,2}/\d)', False),
        ],
    },
    'infinix': {
        'names': ['infinix', 'инфиникс'],
        'series': [r'(?<!\w)(?:hot|хот)(?=\s?\d)'],
        'patterns': [
            ('brand', r'infinix|инфин[иеe]?к[сc]', False),
            ('number', r'(?<!\w)(?:hot|хот)\s?[4-6]0(?:i|s|\s?pro|\s?play|\s?\d{1,2}/\d)', False),
        ],
    },
    'nothing': {
        'names': ['nothing', 'nothing phone', 'нотинг'],
        'series': [r'(?<!\w)cmf(?!\w)'],
        'patterns': [
            ('brand', r'nothing\s?(?:phone|\(?\d)|нотинг(?!\s?хилл)|нафинг|нотхинг|н[ао]синг|(?<!\w)cmf\s?phone', False),
            ('number', r'(?<!\w)phone\s?\(?[1-4]a\)?', False),
        ],
    },
    'vivo': {
        'names': ['vivo', 'виво'],
        'series': [r'iqoo'],
        'patterns': [
            ('brand', r'(?<!\w)(?:vivo|виво)(?:[xyvtsх]\d|(?![а-яa-z]))|iqoo', False),
        ],
    },
    'oppo': {
        'names': ['oppo', 'оппо'],
        'series': [r'(?<!\w)(?:reno|рено|find)(?!\w)'],
        'patterns': [
            ('brand', r'(?<!\w)(?:oppo|оппо)(?:\s?reno|(?![а-яa-z]))|(?<!\w)опо\s?рено', False),
            ('number', r'(?<!\w)reno\s?1[0-5]\s?(?:pro|f|\+|5g|\d{1,2}/\d)', False),
            ('number', r'(?<!\w)рено\s?1[0-5]\s?(?:про|pro|f|ф|\+|5g|\d{1,2}/\d)', False),
            ('number', r'(?<!\w)find\s?[xх]\d\s?(?:pro|ultra|\d{1,2}/\d)', False),
        ],
    },
    'motorola': {
        'names': ['motorola', 'моторола', 'moto'],
        'series': [r'(?<!\w)(?:moto|мото|razr|edge)(?!\w)'],
        'patterns': [
            ('brand', r'motorol+a|моторол+[аыуе](?!р)', False),
            ('number', r'(?<!\w)moto\s?(?:[ge]\s?\d{1,2}(?!\d)|g\s?5g|edge|razr)', False),
            ('number', r'(?<!\w)razr(?!\w)|рейзер\s?\d', False),
            ('number', r'(?<!\w)мото\s?(?:g\s?\d{1,2}(?!\d)|джи\s?\d|(?:edge|эдж)\s?\d|razr|рейзер|разр)', False),
            ('number', r'(?<!\w)edge\s?[4-6]0\s?(?:pro|fusion|ultra|neo)', False),
        ],
    },
}

# ─────────────────────────── шум ───────────────────────────

# бренды, которые могут стоять перед/после не-смартфона ("Samsung Galaxy Watch", "чехол на айфон")
_ANY_BRAND = (
    r'(?:apple|эпл|iphone|айфон\w*|samsung|самсунг\w*|galaxy|галакси|xiaomi|сяоми|redmi|редми|poco|поко|'
    r'honor|хонор\w*|huawei|хуавей|google|гугл|pixel|oneplus|realme|oppo|vivo|nothing|motorola|моторол\w*|'
    r'jbl|sony|сони|dyson|дайсон|lenovo|asus)'
)
# номер/модель после не-смартфона: "s10", "46mm", "11.5", "8/256", "pro", "ultra"
_MODEL_TOKEN = (
    r'(?:[a-zа-я]{0,3}\d{1,4}(?:[.,]\d)?[a-zа-я+]{0,3}|\d{1,2}/\d{2,4}|pro|про|max|макс|ultra|ультра|'
    r'mini|мини|air|эйр|аир|lite|лайт|plus|плюс|fe|se|active|classic|gt|series|hero|smart|'
    r'wi-?fi|lte|cellular|gps|mm|мм|gb|гб|tb|тб)'
)
# не-смартфоны и аксессуары
_DEVICE = (
    r'(?:mac\s?book|макбук\w*|imac|аймак|mac\s?mini|мак\s?мини|iwatch|'
    r'(?:i|mate|magic|galaxy\s)?pad\w*|айпад\w*|пад(?=[^\S\n]*\d)|'
    r'(?:mate|magic|galaxy\s)?book\d*|'
    r'watch\w*|вотч\w*|часы|часики|aw(?=\s?(?:s?\d|ultra|se))|'
    r'band\w*|бэнд|браслет\w*|fit(?=\s?\d)|ring(?=\s?\d)|'
    r'(?:free|ear|galaxy\s|redmi\s)?buds\w*|бадс\w*|наушник\w*|air\s?pods|аирпо[дт]\w*|эйрпо[дт]\w*|эирпо[дт]\w*|enco|'
    r'tab(?=[^\S\n]*[a-z]?\d)|планшет\w*|ноутбук\w*|laptop|'
    r'tv(?!\w)|телевизор\w*|монитор\w*|пылесос\w*|самокат\w*|роутер\w*|колонк\w*|ssd(?!\w)|'
    r'холодильник\w*|стиральн\w*|раци[яи]|go\s?pro|гоу?про|dji|mavic|'
    r'чех[оа]?л\w*|кейс\w*|стекл\w*|пл[её]нк\w*|кабел\w*|провод\w*|зарядк\w*|адаптер\w*|'
    r'блок\w*[^\S\n]+питания|бампер\w*|держател\w*|шнур\w*)'
)
_GAP = r'(?:[^\S\n]|-)+'
_DEVICE_NOISE = re.compile(
    rf'(?<!\w)(?:{_ANY_BRAND}{_GAP}){{0,2}}(?:smart{_GAP})?{_DEVICE}'
    rf'(?:{_GAP}[а-яa-z]+(?={_GAP}{_ANY_BRAND}))?'          # "стиральная машина Samsung"
    rf'(?:{_GAP}(?:на|для|к|под|{_ANY_BRAND}|{_MODEL_TOKEN})(?![а-яa-z]))*',
    re.IGNORECASE
)

_NOISE = [re.compile(p, re.IGNORECASE) for p in [
    # телефоны
    r'(?:\+7|(?<!\d)8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)',
    # ники, почты, ссылки
    r'@\w+|\S+@\S+|https?://\S+|t\.me/\S+|www\.\S+',
    # суммы в рублях/долларах
    r'(?<![\d.,])\d{1,3}(?:[ .,]\d{3})+[^\S\n]?(?:₽|руб\w*|р\.|р(?![а-яa-z])|\$|usd|тыс\w*)|'
    r'(?<![\d.,])\d+[^\S\n]?(?:₽|руб\w*|р\.|\$|usd)',
    # проценты, размеры одежды
    r'\d{1,3}[^\S\n]?(?:%|процент\w*)',
    r'(?<!\w)(?:размер\w*|р-р|us|eu|uk)[^\S\n]*\d{1,2}(?:[.,]5)?(?!\d)',
    # места на рынке и адреса: "павильон А56", "место С21", "дом 16", "кв 256"
    r'(?<!\w)(?:пав(?:ильон\w*)?|мест[оеа]|ряд|точк[аеи]|т\.|корпус\w*|секци\w*|лини[яи]|бокс|вход|этаж|'
    r'офис|кв\.?|квартир\w*|дом|д\.|подъезд)\.?[^\S\n]*№?[^\S\n]*[а-яa-z]?-?[^\S\n]?\d{1,3}[а-яa-z]?(?!\d)',
    r'(?<!\w)[а-яa-z]-?\d{2,3}[^\S\n]*пав(?:ильон)?(?!\w)',
    # время и даты с «с»: "с10 до 20", "с11:00", "с 23 по 25" (но не "с24 по 60к")
    r'(?<!\w)с[^\S\n]?\d{1,2}(?:[:.]\d\d)?[^\S\n]*(?:-|–|до|по)[^\S\n]*\d{1,2}(?:[:.]\d\d)?'
    r'(?![\d,.]|[^\S\n]?(?:к|т|000|k))',
    r'(?<!\w)с\d{1,2}[:.]\d\d',
    r'(?<!\d)\d{1,2}:\d\d(?!\d)',
]]

_EMOJI = re.compile('[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF☀-➿️‍]')

# кириллица, похожая на латиницу — для кодов моделей
_TO_LATIN = str.maketrans({
    'а': 'a', 'б': 'b', 'в': 'v', 'д': 'd', 'е': 'e', 'к': 'k', 'м': 'm',
    'о': 'o', 'р': 'p', 'с': 'c', 'т': 't', 'у': 'y', 'х': 'x',
})

# ─────────────────────────── чужой бренд перед номером ───────────────────────────

# граница «части фразы»: запятая, перевод строки, "и", "или", "на", "+"
_CLAUSE_SEP = re.compile(r'[,;|\n]|(?<!\w)(?:и|или|or|and|на|vs)(?!\w)|\s[+&]\s')
# прочие марки и слова, после которых число — не смартфон ("BMW X5", "Asus TUF A15", "чип A18")
_EXTRA_FOREIGN = (
    r'bmw|бмв|mercedes|мерс\w*|volvo|вольво|audi|ауди|kia|киа|mazda|мазда|tesla|тесла|lada|лада|renault|'
    r'nokia|нокиа|zte|blade|lenovo|леново|asus|асус|tuf|acer|dell|jbl|sony|сони|dji|mix|микс|phantom|фантом|'
    r'ryzen|intel|nvidia|rtx|gtx|чип|bionic|snapdragon|dimensity|helio|exynos|dyson|дайсон|'
    r'playstation|(?<!\w)ps\d|(?<!\w)пс\d|xbox'
)
# латинский код модели другого бренда перед номером айфона: "s24 ultra 12 256", "v40 12 256", "m4 16 512"
_CODE_TOKEN = re.compile(r'(?<!\w)(?!ip\d)[a-z]{1,2}\d{1,3}[a-z]?(?!\w)')


def _compile(pattern):
    return re.compile(pattern, re.IGNORECASE)


_COMPILED = {
    key: [(kind, _compile(p), latin) for kind, p, latin in brand['patterns']]
    for key, brand in SMARTPHONE_BRANDS.items()
}


def _foreign_regex(own_key):
    """Regex слов, которые означают «это чужой бренд» для own_key."""
    parts = [_EXTRA_FOREIGN]
    for key, brand in SMARTPHONE_BRANDS.items():
        if key == own_key:
            continue
        parts.extend(p for kind, p, latin in brand['patterns'] if kind in ('brand', 'model') and not latin)
        parts.extend(brand['series'])
    return _compile('|'.join(f'(?:{p})' for p in parts))


_FOREIGN = {key: _foreign_regex(key) for key in SMARTPHONE_BRANDS}

# артикул Apple: MC6T4, MYND3LL/A (не ники, не Mazda6/Model3, не Mate70/Magic6)
_ARTICLE = re.compile(r'(?<![@\w.])m[a-z0-9]{3,4}\d(?:[a-z]{2}/a)?(?![\w@])', re.IGNORECASE)
_ARTICLE_BLACKLIST = re.compile(r'^M(?:ATE|AGIC|INI|I\d|\d{1,2}(?:PRO|MAX|ULTRA|S|T|X)?$)')
_IPHONE_BRAND = _compile(SMARTPHONE_BRANDS['iphone']['patterns'][0][1])

UNKNOWN_PREFIX = 'слово:'


def _blank(text, match):
    """Заменяет найденный шум на запятую + пробелы той же длины (позиции символов не сдвигаются)."""
    start, end = match.span()
    return text[:start] + ',' + ' ' * (end - start - 1) + text[end:]


def _clean(text):
    """
    Готовит текст к поиску: нижний регистр, эмодзи → пробел, шум → запятая.
    Возвращает (очищенный текст, был_ли_не_смартфон_или_аксессуар).
    """
    low = _EMOJI.sub(' ', text.lower().replace('ё', 'е'))
    for pattern in _NOISE:
        for match in reversed(list(pattern.finditer(low))):
            low = _blank(low, match)

    had_device = False
    for match in reversed(list(_DEVICE_NOISE.finditer(low))):
        low = _blank(low, match)
        had_device = True
    return low, had_device


def _foreign_before(low, latin, start, key):
    """True, если прямо перед номером (до 3 слов, в той же части фразы) стоит чужой бренд."""
    cut = 0
    for sep in _CLAUSE_SEP.finditer(low, 0, start):
        cut = sep.end()
    window = ' '.join(low[cut:start].split()[-3:])
    if window and _FOREIGN[key].search(window):
        return True
    if key == 'iphone':
        latin_window = ' '.join(latin[cut:start].split()[-3:])
        return bool(_CODE_TOKEN.search(latin_window))
    return False


def _has_apple_article(low, latin, had_device):
    """Артикул Apple в тексте. Если рядом часы/ноутбук/наушники и нет слова «айфон» — не считаем."""
    if had_device and not _IPHONE_BRAND.search(low):
        return False
    for match in _ARTICLE.finditer(latin):
        core = re.match(r'M[A-Z0-9]{3,4}\d', match.group(0).upper()).group(0)
        rest = core[1:]
        if not (any(c.isdigit() for c in rest) and any(c.isalpha() for c in rest)):
            continue
        if re.match(r'[A-Z]{4}', rest) or _ARTICLE_BLACKLIST.match(core):
            continue
        return True
    return False


def _mentions(key, low, latin):
    """Упоминания смартфона бренда key: (вид, начало, конец). «Голые» номера с чужим брендом перед ними — пропускаем."""
    for kind, pattern, use_latin in _COMPILED[key]:
        for match in pattern.finditer(latin if use_latin else low):
            if kind != 'number' or not _foreign_before(low, latin, match.start(), key):
                yield kind, match.start(), match.end()


def _brand_found(key, low, latin):
    """Есть ли в тексте смартфон бренда key."""
    return next(_mentions(key, low, latin), None) is not None


def iter_mentions(text, key):
    """
    Все упоминания смартфона бренда key в тексте (для проверки модели по прайсу):
    (вид паттерна, начало, конец, очищенный текст) — позиции указывают в очищенный текст.
    """
    if key not in _COMPILED:
        return
    low, _ = _clean(text)
    latin = low.translate(_TO_LATIN)
    for kind, start, end in _mentions(key, low, latin):
        yield kind, start, end, low


# числительные поколения iPhone: "восемнадцатый про" → 18
_NUMERAL_GEN = {
    'одиннадцат': '11', 'двенадцат': '12', 'тринадцат': '13', 'четырнадцат': '14', 'пятнадцат': '15',
    'шестнадцат': '16', 'семнадцат': '17', 'восемнадцат': '18', 'шестнаш': '16', 'семнаш': '17', 'восемнаш': '18',
}
# фильтр по одному поколению iPhone: 'iphone:18' — только 18-е, а не вся линейка
_IPHONE_MODEL_KEY = re.compile(r'^iphone:(1[1-9])$')


def _iphone_generations(low, latin):
    """Какие поколения iPhone упомянуты в тексте: {'17', '18'}. Без номера («промакс», артикул) — не считаем."""
    gens = set()
    for kind, start, end in _mentions('iphone', low, latin):
        span = low[start:end]
        m = re.search(r'(?<!\d)(1[1-9])(?:(?!\d)|(?=128|256|512))', span)
        if not m and kind == 'brand':
            # "айфон 18", "iphone 18 pro", "iPhone18ProMax" — номер сразу после названия
            m = re.match(r'[^\S\n]*(?:pro[\s-]?max|pro|про)?[^\S\n]*(1[1-9])(?!\d)', low[end:end + 16])
        if m:
            gens.add(m.group(1))
            continue
        gens.update(gen for word, gen in _NUMERAL_GEN.items() if word in span)
    return gens


def find_brand(text, brands):
    """
    Ищет в тексте смартфон одного из разрешённых брендов.
    Возвращает ключ бренда ('honor', 'samsung', 'iphone:18', ...) или None.
    """
    if not text or not brands:
        return None

    low, had_device = _clean(text)
    latin = low.translate(_TO_LATIN)

    for key in sorted(brands):
        model_key = _IPHONE_MODEL_KEY.match(key)
        if model_key:
            # в фильтре только одно поколение: "iPhone 18" — отвечаем на 18-е, 17-е пропускаем
            if model_key.group(1) in _iphone_generations(low, latin):
                return key
        elif key.startswith(UNKNOWN_PREFIX):
            word = key[len(UNKNOWN_PREFIX):]
            if re.search(rf'(?<!\w){re.escape(word)}(?!\w)', low):
                return key
        elif key in _COMPILED:
            if key == 'iphone' and _has_apple_article(low, latin, had_device):
                return key
            if _brand_found(key, low, latin):
                return key
    return None

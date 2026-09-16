# -*- coding: utf-8 -*-
u"""Имя листа по именам видов — общий разбор для кнопок «Оформления».

Здесь две независимые логики:

1. **По планам** (`propose`) — из кнопки «Заполнить имя листа по планам».
2. **По видам** (`propose_views`, `collect_systems`, `compress_system_ranges`)
   — из кнопки «Заполнить имя листа по видам»: префикс из настроек плюс
   системы, найденные в именах видов, свёрнутые в диапазоны («В1-В3, П1»).

Обе вынесены сюда, когда понадобились «Разместить на листы»: новый лист сразу
получает имя по видам, которые на него кладутся.

Правило разбора имени плана ``X_<локация>_<раздел>``:

* части делятся по ``_``, первая (префикс) отбрасывается;
* ``Этаж NN`` → ``План N этажа`` (несколько этажей → ``План N, M этажей``);
* нечисловые локации — по словарю ``sheet_plan_location_map`` из настроек,
  причём ключ словаря ВАЖНЕЕ числового этажа (``99 = План кровли``
  перекрывает «Этаж 99»);
* раздел — по словарю ``sheet_plan_section_map``, иначе как есть;
* итог: ``"{локация}. {раздел}."``.

Использование::

    import pp_sheet_naming

    name, warning = pp_sheet_naming.propose(view_names)
    # name    — предложенное имя листа (пусто, если собрать нечего)
    # warning — замечания через «; » (нет видов, несколько видов, ...)
"""

import re

from pp_settings import load_settings, DEFAULT_SETTINGS

try:
    unicode
except NameError:  # pragma: no cover — на IronPython 2.7 не выполняется
    unicode = str


FLOOR_PATTERN = re.compile(u"Этаж\\s*(\\d+)", re.IGNORECASE)


# ── Словари из настроек ───────────────────────────────────────────────────────

def _parse_map(items):
    u"""Строки вида «ключ = значение» → список пар (ключ, значение)."""
    pairs = []

    for item in items or []:
        text = unicode(item)
        if u"=" not in text:
            continue

        key, _sep, value = text.partition(u"=")
        key = key.strip()
        value = value.strip()

        if key:
            pairs.append((key, value))

    return pairs


def _settings():
    try:
        return load_settings()
    except Exception:
        return {}


def get_location_map(settings=None):
    settings = settings if settings is not None else _settings()
    items = settings.get(
        "sheet_plan_location_map",
        DEFAULT_SETTINGS.get("sheet_plan_location_map", [])
    )
    return _parse_map(items)


def get_section_map(settings=None):
    settings = settings if settings is not None else _settings()
    items = settings.get(
        "sheet_plan_section_map",
        DEFAULT_SETTINGS.get("sheet_plan_section_map", [])
    )
    return _parse_map(items)


def _lookup_map(pairs, raw):
    raw_norm = unicode(raw).strip().lower()

    for key, value in pairs:
        if unicode(key).strip().lower() == raw_norm:
            return value

    return None


# ── Разбор имени вида ─────────────────────────────────────────────────────────

def parse_view_name(view_name, location_map=None, section_map=None):
    u"""Разбирает имя вида «X_<локация>_<раздел>».

    Возвращает (floor_number, location_override, section_text):

    * location_override — значение из словаря локаций, если сработал ключ
      (ключ словаря приоритетнее числового этажа), иначе None;
    * floor_number — int этажа, если словарь не сработал, иначе None;
    * section_text — раздел по словарю, иначе как в имени.
    """
    if location_map is None or section_map is None:
        settings = _settings()
        if location_map is None:
            location_map = get_location_map(settings)
        if section_map is None:
            section_map = get_section_map(settings)

    parts = [p.strip() for p in unicode(view_name).split(u"_") if p.strip()]

    if not parts:
        return None, None, None

    section_raw = parts[-1]
    section_text = _lookup_map(section_map, section_raw) or section_raw

    location_parts = parts[:-1] if len(parts) > 1 else parts

    floor_raw = None
    floor_number = None
    for part in location_parts:
        match = FLOOR_PATTERN.search(part)
        if match:
            floor_raw = match.group(1)
            floor_number = int(floor_raw)
            break

    # Кандидаты для словаря локаций: части имени, а также цифры этажа как в
    # имени («00», «99») и нормализованно («0»).
    candidates = list(location_parts)
    if floor_raw is not None:
        candidates.append(floor_raw)
        candidates.append(unicode(floor_number))

    for candidate in candidates:
        mapped = _lookup_map(location_map, candidate)
        if mapped:
            return None, mapped, section_text

    return floor_number, None, section_text


def format_location(floor_numbers):
    unique_floors = sorted(set(floor_numbers))

    if not unique_floors:
        return None

    if len(unique_floors) == 1:
        return u"План {0} этажа".format(unique_floors[0])

    joined = u", ".join(unicode(number) for number in unique_floors)
    return u"План {0} этажей".format(joined)


def build_sheet_name(location, section):
    location_text = (location or u"").strip()
    section_text = (section or u"").strip()

    if location_text and section_text:
        return u"{0}. {1}.".format(location_text, section_text)

    if location_text:
        return u"{0}.".format(location_text)

    if section_text:
        return u"{0}.".format(section_text)

    return u""


def propose(view_names):
    u"""Имя листа по именам видов на нём. Возврат: (имя, замечания)."""
    view_names = [unicode(name) for name in (view_names or []) if name]

    settings = _settings()
    location_map = get_location_map(settings)
    section_map = get_section_map(settings)

    floor_numbers = []
    location_overrides = []
    section_texts = []

    for view_name in view_names:
        floor_number, location_override, section_text = parse_view_name(
            view_name, location_map, section_map)

        if location_override:
            location_overrides.append(location_override)
        elif floor_number is not None:
            floor_numbers.append(floor_number)
        if section_text:
            section_texts.append(section_text)

    warnings = []

    if not view_names:
        warnings.append(u"нет видов на листе")
    elif len(view_names) > 1:
        warnings.append(u"на листе несколько видов ({0})".format(len(view_names)))

    if len(set(section_texts)) > 1:
        warnings.append(u"разные разделы в видах")

    if location_overrides:
        location = location_overrides[0]
        if len(set(location_overrides)) > 1:
            warnings.append(u"разные локации в видах")
    else:
        location = format_location(floor_numbers)

    section = section_texts[0] if section_texts else None

    if view_names and location is None:
        warnings.append(u"локация не распознана")

    return build_sheet_name(location, section), u"; ".join(warnings)


# ══════════════════════════════════════════════════════════════════════════════
#  По видам: префикс + системы из имён видов
# ══════════════════════════════════════════════════════════════════════════════

# Обозначение системы: 1–3 буквы + номер + необязательный подномер через точку
# («П1», «ВД2», «В 3.1»). Буквы и цифры по краям не допускаются, чтобы не
# ловить куски слов и отметок.
SYSTEM_PATTERN = re.compile(
    u"(?<![A-Za-zА-Яа-я0-9])([A-Za-zА-Яа-я]{1,3})\\s*(\\d+)(?:\\.(\\d+))?"
    u"(?![A-Za-zА-Яа-я0-9.])"
)

# Первая буква обозначения, которую считаем системой, и порядок по умолчанию
# («Имя листа по видам» исторически ставит В первыми).
PREFIX_SORT_ORDER = {
    u"В": 0,
    u"Д": 1,
    u"П": 2,
    u"Х": 3,
    u"К": 4
}


def get_prefix_options(settings=None):
    u"""Начала имени листа из настроек («Схема систем», ...), без дублей."""
    settings = settings if settings is not None else _settings()

    result = []

    for source in (settings.get("sheet_name_prefixes"),
                   DEFAULT_SETTINGS.get("sheet_name_prefixes", [])):
        for prefix in source or []:
            text = unicode(prefix).strip()
            if text and text not in result:
                result.append(text)

        if result:
            break

    return result


def _normalize_system_prefix(raw_prefix):
    text = unicode(raw_prefix).strip().upper()
    text = text.replace(u" ", u"")
    text = text.replace(u"Ё", u"Е")
    return text


def _is_supported_system_prefix(prefix):
    return bool(prefix) and prefix[0] in PREFIX_SORT_ORDER


def extract_systems(view_name):
    u"""Системы из имени вида: [(буквы, номер, подномер|None), ...] без дублей."""
    systems = []
    seen = set()

    if not view_name:
        return systems

    for match in SYSTEM_PATTERN.finditer(unicode(view_name)):
        prefix = _normalize_system_prefix(match.group(1))
        if not _is_supported_system_prefix(prefix):
            continue

        number = int(match.group(2))
        sub_text = match.group(3)
        sub_number = int(sub_text) if sub_text else None
        key = (prefix, number, sub_number)

        if key in seen:
            continue

        seen.add(key)
        systems.append(key)

    return systems


def system_sort_key(system_item):
    u"""Порядок «Имя листа по видам»: В, Д, П, Х, К."""
    prefix, number, sub_number = system_item
    prefix_rank = PREFIX_SORT_ORDER.get(prefix, 999)
    has_sub = 1 if sub_number is not None else 0
    return (prefix_rank, prefix, number, has_sub, sub_number or 0)


def supply_first_key(system_item):
    u"""Порядок «Разместить на листы»: сначала приток, потом вытяжка.

    Общеобменные системы идут раньше противодымных. Внутри группы П раньше В:
    П1, П2, В1, В2, затем ПД1, ВД1. Прочее (Д, Х, К) — после В.
    Противодымной считаем систему из двух-трёх букв с «Д» (ПД, ВД, ДП, ДВ).
    """
    prefix, number, sub_number = system_item

    smoke = 1 if (len(prefix) > 1 and u"Д" in prefix) else 0

    if u"П" in prefix:
        direction = 0
    elif u"В" in prefix:
        direction = 1
    else:
        direction = 2 + PREFIX_SORT_ORDER.get(prefix[:1], 9)

    has_sub = 1 if sub_number is not None else 0
    return (smoke, direction, prefix, number, has_sub, sub_number or 0)


def _item_sort_key(item):
    number, sub_number = item
    has_sub = 1 if sub_number is not None else 0
    return (number, has_sub, sub_number or 0)


def format_system_number(item):
    number, sub_number = item
    if sub_number is None:
        return u"{0}".format(number)

    return u"{0}.{1}".format(number, sub_number)


def system_to_text(system_item):
    prefix, number, sub_number = system_item
    return u"{0}{1}".format(prefix, format_system_number((number, sub_number)))


def _is_next_in_sequence(previous_item, current_item):
    previous_number, previous_sub = previous_item
    current_number, current_sub = current_item

    if previous_sub is None and current_sub is None:
        return current_number == previous_number + 1

    if previous_sub is not None and current_sub is not None:
        return (current_number == previous_number and
                current_sub == previous_sub + 1)

    return False


def _format_item_range(prefix, range_start, range_end):
    if range_start == range_end:
        return u"{0}{1}".format(prefix, format_system_number(range_start))

    return u"{0}{1}-{0}{2}".format(
        prefix,
        format_system_number(range_start),
        format_system_number(range_end)
    )


def _compress_prefix_items(prefix, items):
    parts = []
    index = 0
    count = len(items)

    while index < count:
        range_start = items[index]
        range_end = range_start
        next_index = index + 1

        while next_index < count and \
                _is_next_in_sequence(range_end, items[next_index]):
            range_end = items[next_index]
            next_index += 1

        parts.append(_format_item_range(prefix, range_start, range_end))
        index = next_index

    return parts


def compress_system_ranges(systems, sort_key=None):
    u"""[(П,1,None), (П,2,None), (В,1,None)] → «П1-П2, В1» (порядок — sort_key)."""
    sort_key = sort_key or system_sort_key
    unique_systems = sorted(set(systems or []), key=sort_key)

    if not unique_systems:
        return u""

    grouped_items = {}
    grouped_order = []

    for prefix, number, sub_number in unique_systems:
        if prefix not in grouped_items:
            grouped_items[prefix] = []
            grouped_order.append(prefix)

        grouped_items[prefix].append((number, sub_number))

    parts = []

    for prefix in grouped_order:
        items = sorted(set(grouped_items[prefix]), key=_item_sort_key)
        parts.extend(_compress_prefix_items(prefix, items))

    return u", ".join(parts)


def build_views_name(prefix, systems, sort_key=None):
    prefix_text = (prefix or u"").strip()
    compressed = compress_system_ranges(systems, sort_key)

    if prefix_text and compressed:
        return u"{0} {1}".format(prefix_text, compressed)

    if prefix_text:
        return prefix_text

    return compressed


def collect_systems(view_names, sort_key=None):
    u"""Возврат: (имена видов, где нашлись системы; все системы по порядку)."""
    sort_key = sort_key or system_sort_key
    systems = []
    matched = []

    for view_name in view_names or []:
        found = extract_systems(view_name)
        if not found:
            continue

        systems.extend(found)

        if view_name not in matched:
            matched.append(view_name)

    return matched, sorted(set(systems), key=sort_key)


_DIGITS = re.compile(u"(\\d+)")


def natural_key(text):
    u"""Ключ сортировки, где 2 идёт перед 10."""
    key = []

    for index, part in enumerate(_DIGITS.split(unicode(text or u""))):
        if index % 2:
            key.append((1, int(part), u""))
        else:
            key.append((0, 0, part.lower()))

    return key


def view_order_key(view_name):
    u"""Порядок видов на листе: по первой системе в имени (приток → вытяжка),
    виды без систем — в конце по имени."""
    systems = extract_systems(view_name)

    if systems:
        best = min(supply_first_key(item) for item in systems)
        return (0, best, natural_key(view_name))

    return (1, (), natural_key(view_name))


def propose_views(prefix, view_names):
    u"""Имя листа для группы видов. Возврат: (имя, замечание)."""
    view_names = [unicode(name) for name in (view_names or []) if name]
    _matched, systems = collect_systems(view_names, supply_first_key)

    name = build_views_name(prefix, systems, supply_first_key)
    warning = u""

    if not systems:
        warning = u"в именах видов нет обозначений систем"
        if not (prefix or u"").strip() and view_names:
            name = view_names[0]

    return name, warning

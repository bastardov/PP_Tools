# -*- coding: utf-8 -*-
u"""Имя листа по имени плана — общий разбор для кнопок «Оформления».

Родилось в кнопке «Заполнить имя листа по планам» и вынесено сюда, когда та же
логика понадобилась «Разместить на листы»: новый лист сразу получает имя по
плану, который на него кладётся.

Правило разбора имени вида ``X_<локация>_<раздел>``:

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

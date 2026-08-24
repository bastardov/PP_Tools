# -*- coding: utf-8 -*-

# Общая логика для инструментов «Инструменты меток» и «Маркировать по правилу».
#
# Правило (dict):
#   cat        — имя BuiltInCategory элемента, напр. "OST_DuctTerminal"
#   family     — имя семейства элемента (обязательно)
#   type       — имя типа элемента или None (None = правило на всё семейство)
#   tag_family — имя семейства марки
#   tag_type   — имя типа марки
#   leader     — bool, ставить с выноской

from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, ElementId,
    FilteredElementCollector, FamilySymbol, XYZ
)


# (enum_name, категория элемента, категория марки, отображаемое имя)
ELEMENT_CATEGORIES = [
    (u"OST_DuctTerminal",
     BuiltInCategory.OST_DuctTerminal,
     BuiltInCategory.OST_DuctTerminalTags,
     u"Воздухораспределители"),
    (u"OST_DuctAccessory",
     BuiltInCategory.OST_DuctAccessory,
     BuiltInCategory.OST_DuctAccessoryTags,
     u"Арматура воздуховодов"),
    (u"OST_MechanicalEquipment",
     BuiltInCategory.OST_MechanicalEquipment,
     BuiltInCategory.OST_MechanicalEquipmentTags,
     u"Оборудование"),
    (u"OST_PipeAccessory",
     BuiltInCategory.OST_PipeAccessory,
     BuiltInCategory.OST_PipeAccessoryTags,
     u"Арматура трубопроводов"),
    (u"OST_DuctCurves",
     BuiltInCategory.OST_DuctCurves,
     BuiltInCategory.OST_DuctTags,
     u"Воздуховоды"),
    (u"OST_PipeCurves",
     BuiltInCategory.OST_PipeCurves,
     BuiltInCategory.OST_PipeTags,
     u"Трубы"),
    (u"OST_PipeInsulations",
     BuiltInCategory.OST_PipeInsulations,
     BuiltInCategory.OST_PipeInsulationsTags,
     u"Материалы изоляции труб"),
    (u"OST_DuctInsulations",
     BuiltInCategory.OST_DuctInsulations,
     BuiltInCategory.OST_DuctInsulationsTags,
     u"Материалы изоляции воздуховодов"),
    (u"OST_GenericModel",
     BuiltInCategory.OST_GenericModel,
     BuiltInCategory.OST_GenericModelTags,
     u"Обобщённые модели"),
]


# ── Служебные карты ─────────────────────────────────────────────────────────

def _enum_int(bic):
    return int(bic)


ENUM_NAMES = [row[0] for row in ELEMENT_CATEGORIES]

# Категории, показываемые в конфигураторе «Инструменты меток» (в порядке отображения).
CONFIG_ENUM_NAMES = [
    u"OST_DuctTerminal",        # Воздухораспределители
    u"OST_DuctAccessory",       # Арматура воздуховодов
    u"OST_PipeAccessory",       # Арматура трубопроводов
    u"OST_MechanicalEquipment", # Оборудование
    # Воздуховоды сюда не входят: у них своя вкладка, где марка зависит ещё и
    # от длины надписи (см. SIZE_ENUM_NAMES).
    u"OST_PipeCurves",          # Трубы
    u"OST_PipeInsulations",     # Материалы изоляции труб
    u"OST_DuctInsulations",     # Материалы изоляции воздуховодов
    u"OST_GenericModel",        # Обобщённые модели
]

DISPLAY_BY_ENUM = dict((row[0], row[3]) for row in ELEMENT_CATEGORIES)
ENUM_BY_DISPLAY = dict((row[3], row[0]) for row in ELEMENT_CATEGORIES)

_ELEM_BIC_BY_ENUM = dict((row[0], row[1]) for row in ELEMENT_CATEGORIES)
_TAG_BIC_BY_ENUM = dict((row[0], row[2]) for row in ELEMENT_CATEGORIES)

# int id категории элемента -> enum_name
_ENUM_BY_CATID = dict((_enum_int(row[1]), row[0]) for row in ELEMENT_CATEGORIES)


def enum_name_by_category_id(cat_id_int):
    return _ENUM_BY_CATID.get(cat_id_int)


def display_name(enum_name):
    return DISPLAY_BY_ENUM.get(enum_name, enum_name)


# ── Данные элемента ─────────────────────────────────────────────────────────

# Запасной ярлык для системных типов (воздуховоды, трубы, изоляция), у которых
# имя семейства может быть пустым. Одинаков в конфигураторе и при применении,
# чтобы правило совпадало.
NO_FAMILY = u"(без семейства)"


def _clean_family(name):
    try:
        if name is None:
            return NO_FAMILY
        s = name.strip()
        return s if s else NO_FAMILY
    except:
        return NO_FAMILY


def get_element_category_enum(el):
    try:
        if el and el.Category:
            return enum_name_by_category_id(el.Category.Id.IntegerValue)
    except:
        pass
    return None


def get_family_name(el):
    try:
        if hasattr(el, "Symbol") and el.Symbol and el.Symbol.Family:
            return _clean_family(el.Symbol.Family.Name)
    except:
        pass
    try:
        t = el.Document.GetElement(el.GetTypeId())
        if t and hasattr(t, "FamilyName"):
            return _clean_family(t.FamilyName)
    except:
        pass
    return NO_FAMILY


def get_type_name(el):
    try:
        t = el.Document.GetElement(el.GetTypeId())
        if t:
            p = t.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
            if p:
                v = p.AsString()
                if v:
                    return v
            return t.Name
    except:
        pass
    return None


def is_allowed_element(el):
    return get_element_category_enum(el) is not None


# ── Поиск правила (два уровня) ──────────────────────────────────────────────

def find_rule(rules, cat_enum, family, type_name):
    """Сначала точное совпадение family+type, затем правило на семейство
    (type пустой). Возвращает правило-dict или None."""
    if not cat_enum or not family:
        return None

    family_match = None

    for r in rules:
        if r.get("cat") != cat_enum:
            continue
        if r.get("family") != family:
            continue

        r_type = r.get("type")

        if r_type and type_name and r_type == type_name:
            return r  # точное совпадение — приоритет

        if not r_type and family_match is None:
            family_match = r  # запасной вариант на всё семейство

    return family_match


def find_rule_for_element(rules, el):
    return find_rule(
        rules,
        get_element_category_enum(el),
        get_family_name(el),
        get_type_name(el)
    )


# ── Резолв типа марки в проекте ─────────────────────────────────────────────

def _symbol_type_name(sym):
    try:
        p = sym.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p:
            v = p.AsString()
            if v:
                return v
    except:
        pass
    try:
        return sym.Name
    except:
        return None


def symbol_names(sym):
    u"""(имя семейства, имя типа) типоразмера. (None, None) — не прочиталось."""
    if sym is None:
        return (None, None)

    family = None

    try:
        if hasattr(sym, "Family") and sym.Family:
            family = sym.Family.Name
    except:
        family = None

    if not family:
        try:
            family = sym.FamilyName
        except:
            family = None

    return (family or None, _symbol_type_name(sym))


def collect_tag_symbols(doc, cat_enum):
    """Все FamilySymbol марок для категории элемента. Список кортежей
    (family_name, type_name, symbol)."""
    result = []
    tag_bic = _TAG_BIC_BY_ENUM.get(cat_enum)
    if tag_bic is None:
        return result

    collector = (FilteredElementCollector(doc)
                 .OfClass(FamilySymbol)
                 .OfCategory(tag_bic))

    for sym in collector:
        try:
            fam = sym.Family.Name if sym.Family else None
            tname = _symbol_type_name(sym)
            result.append((fam, tname, sym))
        except:
            pass

    return result


def collect_element_types(doc, cat_enum):
    """Все типы элементов (FamilySymbol) категории, загруженные в проект.
    Список кортежей (family_name, type_name, symbol)."""
    result = []
    bic = _ELEM_BIC_BY_ENUM.get(cat_enum)
    if bic is None:
        return result

    collector = (FilteredElementCollector(doc)
                 .OfCategory(bic)
                 .WhereElementIsElementType())

    for sym in collector:
        try:
            fam = None
            if hasattr(sym, "Family") and sym.Family:
                fam = sym.Family.Name
            if not fam and hasattr(sym, "FamilyName"):
                fam = sym.FamilyName
            fam = _clean_family(fam)
            tname = _symbol_type_name(sym)
            result.append((fam, tname, sym))
        except:
            pass

    return result


# ── Работа с таблицей правил (для конфигуратора) ────────────────────────────

def get_assignment(rules, cat_enum, family, type_name):
    """Возвращает правило, назначенное ИМЕННО этому узлу (точное совпадение
    cat/family/type, где type_name=None означает уровень семейства). None — нет."""
    for r in rules:
        if r.get("cat") != cat_enum:
            continue
        if r.get("family") != family:
            continue
        r_type = r.get("type")
        if (r_type or None) == (type_name or None):
            return r
    return None


def set_assignment(rules, cat_enum, family, type_name, tag_family, tag_type):
    """Устанавливает/заменяет правило для узла. Мутирует список rules."""
    remove_assignment(rules, cat_enum, family, type_name)
    rules.append({
        "cat": cat_enum,
        "family": family,
        "type": type_name if type_name else None,
        "tag_family": tag_family,
        "tag_type": tag_type,
        "leader": False,
    })


def remove_assignment(rules, cat_enum, family, type_name):
    """Удаляет правило узла, если оно есть. Мутирует список rules."""
    keep = []
    for r in rules:
        same = (r.get("cat") == cat_enum
                and r.get("family") == family
                and (r.get("type") or None) == (type_name or None))
        if not same:
            keep.append(r)
    del rules[:]
    rules.extend(keep)


def resolve_tag_symbol(doc, cat_enum, tag_family, tag_type):
    """Находит FamilySymbol марки по имени семейства и типа. None если нет."""
    if not tag_family or not tag_type:
        return None

    for fam, tname, sym in collect_tag_symbols(doc, cat_enum):
        if fam == tag_family and tname == tag_type:
            return sym

    return None


# ── Размерные правила: марка по длине надписи ───────────────────────────────
#
# У воздуховодов ширина марки зависит не от типа элемента, а от того, сколько
# знаков вышло в надписи: «100х100» — 7, «1000х1000» — 9. Поэтому правило
# второго рода ключуется диапазоном длины. Одной длины мало: у круглого и у
# прямоугольного воздуховода надпись может совпасть по длине, а марки нужны
# разные — значит, у строки есть ещё и область действия (семейство и тип
# воздуховода).
#
# Строка правила (dict):
#   cat        — имя BuiltInCategory элемента (одно из SIZE_ENUM_NAMES)
#   family     — имя семейства воздуховода или None («все воздуховоды»)
#   type       — имя типа воздуховода или None («всё семейство»)
#   min_len    — длина надписи от, знаков (включительно)
#   max_len    — длина надписи до, знаков (включительно) или None — «и больше»
#   tag_family — имя семейства марки
#   tag_type   — имя типа марки
#
# Пресет (dict): {"name": u"Планы 1:100", "rules": [строка, ...]}
# Пресеты лежат в настройках под ключом tag_size_presets, имя активного —
# под tag_size_active.

# Категории, где марка подбирается по длине надписи. Всё остальное — в режиме
# «По семейству и типу».
SIZE_ENUM_NAMES = [
    u"OST_DuctCurves",        # Воздуховоды
]

DEFAULT_PRESET_NAME = u"Основной"

# «Без предела» внутри сравнений: столько знаков надпись не наберёт.
_NO_LIMIT = 10 ** 6


def _to_int(value, default=None):
    try:
        if value is None:
            return default
        if isinstance(value, basestring) and not value.strip():
            return default
        return int(value)
    except:
        return default


def normalize_size_rule(raw):
    u"""Строка правила из настроек или файла -> проверенная копия. None —
    строка непригодна (нет категории или марки)."""
    if not isinstance(raw, dict):
        return None

    cat = raw.get("cat")

    if cat not in SIZE_ENUM_NAMES:
        return None

    tag_family = raw.get("tag_family")
    tag_type = raw.get("tag_type")

    if not tag_family or not tag_type:
        return None

    min_len = _to_int(raw.get("min_len"), 0)

    if min_len is None or min_len < 0:
        min_len = 0

    max_len = _to_int(raw.get("max_len"), None)

    if max_len is not None and max_len < min_len:
        max_len = min_len

    family = raw.get("family") or None
    type_name = raw.get("type") or None

    if not family:
        type_name = None  # тип без семейства не имеет смысла

    return {
        "cat": cat,
        "family": family,
        "type": type_name,
        "min_len": min_len,
        "max_len": max_len,
        "tag_family": tag_family,
        "tag_type": tag_type,
    }


def size_rule_scope(rule):
    u"""Область действия строки: (семейство, тип). (None, None) — все."""
    family = rule.get("family") or None
    type_name = rule.get("type") or None

    if not family:
        return (None, None)

    return (family, type_name)


def same_scope(one, other):
    return size_rule_scope(one) == size_rule_scope(other)


def scope_rank(rule):
    u"""Насколько строка «точная»: 2 — тип, 1 — семейство, 0 — все."""
    family, type_name = size_rule_scope(rule)

    if type_name:
        return 2

    if family:
        return 1

    return 0


def sort_size_rules(size_rules):
    u"""Строки по области действия и началу диапазона. Мутирует список."""
    order = dict((name, i) for i, name in enumerate(SIZE_ENUM_NAMES))

    def key(r):
        family, type_name = size_rule_scope(r)

        return (order.get(r.get("cat"), 99),
                family or u"",
                type_name or u"",
                r.get("min_len") or 0)

    size_rules.sort(key=key)

    return size_rules


def normalize_presets(raw):
    u"""Список пресетов из настроек -> проверенный список. Пустые настройки
    превращаются в один пустой пресет: окну всегда есть с чем работать."""
    presets = []

    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue

            name = (item.get("name") or u"").strip()

            if not name:
                continue

            rules = []

            for r in (item.get("rules") or []):
                rule = normalize_size_rule(r)

                if rule:
                    rules.append(rule)

            presets.append({"name": name, "rules": sort_size_rules(rules)})

    if not presets:
        presets = [{"name": DEFAULT_PRESET_NAME, "rules": []}]

    return presets


def find_preset(presets, name):
    for p in presets:
        if p.get("name") == name:
            return p

    return None


def active_preset(presets, name):
    u"""Пресет по имени; если такого нет — первый в списке."""
    preset = find_preset(presets, name)

    if preset is not None:
        return preset

    return presets[0] if presets else None


def active_size_rules(presets, name):
    u"""Строки активного пресета (копия списка)."""
    preset = active_preset(normalize_presets(presets), name)

    return list(preset.get("rules") or []) if preset else []


def measure_tag_text(text):
    u"""Длина самой длинной строки надписи марки, в знаках. 0 — надписи нет."""
    if not text:
        return 0

    try:
        value = unicode(text)
    except:
        return 0

    value = value.replace(u"\r\n", u"\n").replace(u"\r", u"\n")

    best = 0

    for line in value.split(u"\n"):
        length = len(line.strip())

        if length > best:
            best = length

    return best


def has_size_rules(size_rules, cat_enum):
    for r in size_rules:
        if r.get("cat") == cat_enum:
            return True

    return False


def _covers_length(rule, length):
    min_len = rule.get("min_len") or 0
    max_len = rule.get("max_len")

    if length < min_len:
        return False

    if max_len is not None and length > max_len:
        return False

    return True


def rules_in_scope(size_rules, cat_enum, family=None, type_name=None):
    u"""Строки, действующие на этот воздуховод, от точных к общим.

    Правило типа важнее правила семейства, правило семейства важнее общего —
    та же лестница, что и в режиме «По семейству и типу»."""
    exact = []
    by_family = []
    common = []

    for r in size_rules:
        if r.get("cat") != cat_enum:
            continue

        r_family, r_type = size_rule_scope(r)

        if not r_family:
            common.append(r)
            continue

        if family is None or r_family != family:
            continue

        if r_type:
            if type_name is not None and r_type == type_name:
                exact.append(r)
            continue

        by_family.append(r)

    return exact + by_family + common


def find_size_rule(size_rules, cat_enum, length, family=None, type_name=None):
    u"""Строка для этого воздуховода и этой длины надписи. None — нет.

    Сначала правило типа, затем семейства, затем общее; внутри уровня —
    первая строка, в диапазон которой попала длина."""
    if not cat_enum or not length:
        return None

    for r in rules_in_scope(size_rules, cat_enum, family, type_name):
        if _covers_length(r, length):
            return r

    return None


def size_coverage_gaps(size_rules, cat_enum, family=None, type_name=None,
                       start=1):
    u"""Непокрытые длины надписи для этого воздуховода: [(от, до|None), ...].
    Учитываются и точные строки, и общие. Пустой список — покрыто всё."""
    spans = []

    for r in rules_in_scope(size_rules, cat_enum, family, type_name):
        spans.append((r.get("min_len") or 0, r.get("max_len")))

    spans.sort(key=lambda s: s[0])

    gaps = []
    cursor = start

    for low, high in spans:
        if low > cursor:
            gaps.append((cursor, low - 1))
            cursor = low

        if high is None:
            return gaps  # дальше покрыто до бесконечности

        if high + 1 > cursor:
            cursor = high + 1

    gaps.append((cursor, None))

    return gaps


def find_size_overlap(size_rules, cat_enum, min_len, max_len, ignore=None,
                      family=None, type_name=None):
    u"""Строка с той же областью действия, диапазон которой пересекается с
    заданным. None — пересечений нет. ignore — строка, которую редактируют.

    Строки с разной областью не конфликтуют: «круглые, 7 знаков» и
    «прямоугольные, 7 знаков» спокойно живут рядом, потому что применяются к
    разным воздуховодам."""
    hi = max_len if max_len is not None else _NO_LIMIT
    scope = (family or None, type_name or None)

    if not scope[0]:
        scope = (None, None)

    for r in size_rules:
        if r is ignore or r.get("cat") != cat_enum:
            continue

        if size_rule_scope(r) != scope:
            continue

        r_lo = r.get("min_len") or 0
        r_hi = r.get("max_len")
        r_hi = r_hi if r_hi is not None else _NO_LIMIT

        if min_len <= r_hi and r_lo <= hi:
            return r

    return None


def strip_size_categories(rules):
    u"""Убрать из старой таблицы (tag_rules) правила категорий, которые теперь
    настраиваются размерными правилами. Мутирует список, возвращает число
    убранных строк."""
    keep = []
    dropped = 0

    for r in rules:
        if r.get("cat") in SIZE_ENUM_NAMES:
            dropped += 1
            continue

        keep.append(r)

    if dropped:
        del rules[:]
        rules.extend(keep)

    return dropped


# ── Геометрия ───────────────────────────────────────────────────────────────

def get_location_point(el):
    """Точка расположения элемента; при отсутствии — центр габаритов."""
    try:
        loc = el.Location
        if loc and hasattr(loc, "Point") and loc.Point is not None:
            return loc.Point
    except:
        pass
    try:
        bb = el.get_BoundingBox(None)
        if bb:
            return XYZ((bb.Min.X + bb.Max.X) / 2.0,
                      (bb.Min.Y + bb.Max.Y) / 2.0,
                      (bb.Min.Z + bb.Max.Z) / 2.0)
    except:
        pass
    return XYZ(0, 0, 0)

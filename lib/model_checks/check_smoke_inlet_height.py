# -*- coding: utf-8 -*-
"""Проверка «13. Высота дымоприёмного устройства» (ключ smoke_inlet_height).

Перенесено из pp_model_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    ElementId,
    FilteredElementCollector,
    Level,
)

from model_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
    FEET_TO_MM,
)

from model_checks.common import (
    _is_yes,
    _location_point,
    _parse_category_map,
    _parse_float,
    _parse_masks,
    _safe_name,
    _smoke_label,
    _smoke_search_text,
)


# Значения по умолчанию опций, которые описаны в этом файле.
DEFAULTS = {
    # Проверка «Высота дымоприёмного устройства».
    # В модели это обычно решётка (воздухораспределитель), надетая
    # на клапан дымоудаления, поэтому категория по умолчанию — воздухораспределители.
    "inlet_categories": u"OST_DuctTerminal",
    # Маски через ; — какие элементы считаем дымоприёмными.
    "inlet_masks": u"ДУ; ДВ",
    # Норма: низ устройства не ниже верха дверного проёма (~2100 мм).
    "inlet_min_height_mm": u"2100",
    # Если отметка уровня — конструктив, а не чистый пол: поправка в мм.
    "inlet_floor_offset_mm": u"0",
    # База отсчёта низа: «габарит» или «точка вставки».
    "inlet_base": u"габарит",
    "inlet_name_fallback": u"да",
}


# Категории для проверки «Высота дымоприёмного устройства».
# Обычно это решётка на клапане, поэтому воздухораспределители идут
# первыми и единственные отмечены по умолчанию.
INLET_CATEGORY_OPTIONS = [
    (u"OST_DuctTerminal", u"Воздухораспределители", BuiltInCategory.OST_DuctTerminal),
    (u"OST_MechanicalEquipment", u"Оборудование", BuiltInCategory.OST_MechanicalEquipment),
    (u"OST_DuctAccessory", u"Арматура воздуховодов", BuiltInCategory.OST_DuctAccessory),
]


INLET_CATEGORY_MAP = {
    key: built_in for key, _label, built_in in INLET_CATEGORY_OPTIONS
}


INLET_DEFAULT_KEYS = [u"OST_DuctTerminal"]


def _collect_levels(doc):
    """Список (отметка в футах, имя) всех уровней модели, по возрастанию."""
    levels = []
    try:
        collector = FilteredElementCollector(doc).OfClass(Level) \
            .WhereElementIsNotElementType()
        for level in collector.ToElements():
            try:
                levels.append((level.Elevation, _safe_name(level)))
            except:
                pass
    except:
        pass
    levels.sort(key=lambda item: item[0])
    return levels


def _element_level(doc, element):
    """(отметка в футах, имя) уровня элемента или (None, u"")."""
    level_id = None
    try:
        level_id = element.LevelId
    except:
        level_id = None

    if level_id is None or level_id == ElementId.InvalidElementId:
        # У разных семейств уровень лежит в разных BIP, набор разнится по
        # версиям Revit — поэтому перебираем через getattr.
        for name in (u"FAMILY_LEVEL_PARAM",
                     u"INSTANCE_SCHEDULE_ONLY_LEVEL_PARAM",
                     u"SCHEDULE_LEVEL_PARAM",
                     u"RBS_START_LEVEL_PARAM"):
            built_in = getattr(BuiltInParameter, name, None)
            if built_in is None:
                continue
            try:
                param = element.get_Parameter(built_in)
                if param is None or not param.HasValue:
                    continue
                candidate = param.AsElementId()
                if (candidate is not None
                        and candidate != ElementId.InvalidElementId):
                    level_id = candidate
                    break
            except:
                pass

    if level_id is None or level_id == ElementId.InvalidElementId:
        return None, u""

    try:
        level = doc.GetElement(level_id)
    except:
        return None, u""
    if level is None:
        return None, u""
    try:
        return level.Elevation, _safe_name(level)
    except:
        return None, u""


def _nearest_level_below(levels, z_ft, tolerance_ft):
    """Ближайший уровень на отметке элемента или ниже неё."""
    best = None
    for elevation, name in levels:
        if elevation > z_ft + tolerance_ft:
            continue
        if best is None or elevation > best[0]:
            best = (elevation, name)
    if best is None:
        return None, u""
    return best


def _element_bottom_z(element, use_point):
    """Отметка низа элемента в футах: габарит или точка вставки."""
    if use_point:
        point = _location_point(element)
        if point is not None:
            return point.Z

    try:
        box = element.get_BoundingBox(None)
    except:
        box = None
    if box is not None:
        try:
            return box.Min.Z
        except:
            pass

    point = _location_point(element)
    if point is not None:
        return point.Z
    return None


def _is_point_base(raw_text):
    """«точка вставки» / «вставка» / «point» = считать по точке вставки."""
    text = unicode(raw_text or u"").strip().lower()
    if not text:
        return False
    return (text.startswith(u"точ") or text.startswith(u"вст")
            or text.startswith(u"point"))


def _format_mm(value_mm):
    try:
        return u"{0:.0f}".format(value_mm)
    except:
        return u"?"


def _matches_word_start(text, masks):
    """Маска должна начинать слово: «ДУ1» подходит, «воздуховод» — нет.

    Обычное сравнение по вхождению здесь опасно: маска «ДУ» находится
    внутри слова «возДУхораспределитель», а имя типа мы как раз
    просматриваем, когда у элемента нет системы."""
    low = (text or u"").lower()
    for mask in masks:
        if not mask:
            continue
        start = 0
        while True:
            index = low.find(mask, start)
            if index < 0:
                break
            previous = low[index - 1] if index > 0 else u""
            if not previous or not (previous.isalpha() or previous.isdigit()):
                return True
            start = index + 1
    return False


def _run_smoke_inlet_height_check(doc, config, cache):
    masks = _parse_masks(config.get("inlet_masks")) or [u"ду"]
    use_name_fallback = _is_yes(config.get("inlet_name_fallback"))
    use_point = _is_point_base(config.get("inlet_base"))

    min_mm = _parse_float(config.get("inlet_min_height_mm"), 2100.0)
    if min_mm <= 0:
        min_mm = 2100.0
    floor_offset_mm = _parse_float(config.get("inlet_floor_offset_mm"), 0.0)

    categories = _parse_category_map(
        config.get("inlet_categories"), INLET_CATEGORY_MAP, INLET_DEFAULT_KEYS)
    elements = cache.get_by_categories(categories)

    levels = _collect_levels(doc)
    # Допуск, чтобы элемент ровно на отметке уровня не «проваливался» ниже.
    tolerance_ft = 10.0 / FEET_TO_MM

    checked = 0
    skipped_no_level = 0
    found = []
    for element in elements:
        search_text, system_name = _smoke_search_text(
            doc, element, use_name_fallback)
        if not search_text or not _matches_word_start(search_text, masks):
            continue

        z_ft = _element_bottom_z(element, use_point)
        if z_ft is None:
            continue

        elevation, level_name = _element_level(doc, element)
        # Уровень выше самого элемента = привязка не к своему этажу,
        # иначе высота ушла бы в минус. Берём ближайший уровень снизу.
        if elevation is None or elevation > z_ft + tolerance_ft:
            elevation, level_name = _nearest_level_below(
                levels, z_ft, tolerance_ft)

        if elevation is None:
            skipped_no_level += 1
            continue

        checked += 1
        height_mm = (z_ft - elevation) * FEET_TO_MM - floor_offset_mm
        if height_mm >= min_mm:
            continue

        try:
            element_id = element.Id.IntegerValue
        except:
            continue

        display = system_name or search_text
        found.append((height_mm, element, display, level_name, element_id))

    found.sort(key=lambda item: item[0])

    issues = []
    for height_mm, element, display, level_name, element_id in found:
        issues.append(CheckIssue(
            u"{0} мм от пола (норма {1} мм) — {2}, уровень «{3}»".format(
                _format_mm(height_mm),
                _format_mm(min_mm),
                _smoke_label(element, display),
                level_name or u"?"),
            [element_id]))

    info = u"Дымоприёмных устройств найдено: {0}".format(checked)
    if not checked:
        info += (u". Ни один элемент не подошёл — проверьте маски имён "
                 u"и категории в настройках проверки")
    if skipped_no_level:
        info += u". Без уровня, пропущено: {0}".format(skipped_no_level)
    if use_point:
        info += u". База отсчёта — точка вставки"
    if floor_offset_mm:
        info += u". Поправка пола: {0} мм".format(_format_mm(floor_offset_mm))

    return CheckResult(u"smoke_inlet_height",
                       u"13. Высота дымоприёмного устройства",
                       checked, issues, info)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "inlet_categories",
            u"Категории для проверки",
            ["smoke_inlet_height"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _b in INLET_CATEGORY_OPTIONS]
        ),
        CheckOptionDefinition(
            "inlet_masks",
            u"Дымоприёмные устройства — маски имён через ; (напр. ДУ; ДВ)",
            ["smoke_inlet_height"]
        ),
        CheckOptionDefinition(
            "inlet_min_height_mm",
            u"Минимальная высота низа от пола, мм",
            ["smoke_inlet_height"]
        ),
        CheckOptionDefinition(
            "inlet_floor_offset_mm",
            u"Поправка «уровень → чистый пол», мм",
            ["smoke_inlet_height"]
        ),
        CheckOptionDefinition(
            "inlet_base",
            u"База отсчёта низа: габарит / точка вставки",
            ["smoke_inlet_height"]
        ),
        CheckOptionDefinition(
            "inlet_name_fallback",
            u"Если системы нет — искать маски в имени типа (да/нет)",
            ["smoke_inlet_height"]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "smoke_inlet_height",
        u"13. Высота дымоприёмного устройства",
        u"Проверяет, что низ дымоприёмного устройства не опускается ниже "
        u"заданной отметки от пола (по умолчанию 2100 мм — верх дверного "
        u"проёма). В модели дымоприёмное устройство — обычно решётка на "
        u"клапане дымоудаления, поэтому по умолчанию проверяются "
        u"Воздухораспределители, а отбор внутри категории идёт по маскам "
        u"имени системы (если системы нет — по имени типа или семейства; "
        u"маска должна начинать слово, поэтому «ДУ» ловит «ДУ1», но не "
        u"«возДУхораспределитель»). "
        u"Высота считается от отметки уровня до низа элемента: низ берётся "
        u"по габариту (рамка решётки) либо по точке вставки. Если уровни в "
        u"модели заданы по конструктиву, разницу до чистого пола укажите "
        u"полем «поправка». Когда уровень элемента оказывается выше самого "
        u"элемента (привязка не к своему этажу), берётся ближайший уровень "
        u"снизу.",
        runner=_run_smoke_inlet_height_check,
        option_keys=[
            "inlet_categories",
            "inlet_masks",
            "inlet_min_height_mm",
            "inlet_floor_offset_mm",
            "inlet_base",
            "inlet_name_fallback",
        ],
        kind=u"report",
    )

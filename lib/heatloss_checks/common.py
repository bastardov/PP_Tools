# -*- coding: utf-8 -*-
"""Общая инфраструктура проверок теплопотерь.

Разбор опций, мягкое сравнение значений, категории, подписи элементов
для отчёта и сбор проверяемых элементов. Сейчас всем этим пользуется одна
проверка, но разделы изначально были общими и понадобятся любой следующей —
поэтому они здесь, а не в check_numname.py (решение 15.09.2026, см. CHECKS.md).
"""

import re
import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    StorageType,
)

import pp_heatloss_spaces as spaces


# --------------------------------------------------------------------------- #
#                                конфигурация                                 #
# --------------------------------------------------------------------------- #

# Категории проверки: (ключ, подпись). Стены и витражи — одна категория
# Revit (OST_Walls), но проверяются по-разному, поэтому ключи разные.
CATEGORY_OPTIONS = [
    (u"walls", u"Стены (обычные)"),
    (u"curtain", u"Витражи"),
    (u"windows", u"Окна"),
    (u"doors", u"Двери"),
    (u"floors", u"Перекрытия"),
]


CATEGORY_KEYS = [key for key, _label in CATEGORY_OPTIONS]


CATEGORY_LABELS = dict(CATEGORY_OPTIONS)


# Единственное число — для сообщений об элементе.
CATEGORY_SINGULAR = {
    u"walls": u"Стена",
    u"curtain": u"Витраж",
    u"windows": u"Окно",
    u"doors": u"Дверь",
    u"floors": u"Перекрытие",
}


# --------------------------------------------------------------------------- #
#                             разбор значений опций                           #
# --------------------------------------------------------------------------- #

def _is_yes(raw_text):
    value = unicode(raw_text or u"").strip().lower()
    return value in (u"да", u"yes", u"1", u"true", u"истина", u"+")


def _parse_category_keys(raw_text):
    """Ключи категорий из строки настройки; пусто = набор по умолчанию."""
    result = []
    seen = set()
    cleaned = unicode(raw_text or u"").replace(u";", u",").replace(u"/", u",")
    for part in cleaned.split(u","):
        token = part.strip()
        if not token or token in seen:
            continue
        if token not in CATEGORY_LABELS:
            continue
        seen.add(token)
        result.append(token)

    if not result:
        result = [u"walls", u"curtain", u"windows", u"doors"]
    return result


# Похожие латинские буквы -> кириллица (номера помещений часто набирают
# в неверной раскладке: "С101" латинской C и кириллической С — разные строки).
_CONFUSABLES = {
    u"A": u"А", u"B": u"В", u"C": u"С", u"E": u"Е", u"H": u"Н",
    u"K": u"К", u"M": u"М", u"O": u"О", u"P": u"Р", u"T": u"Т",
    u"X": u"Х", u"Y": u"У",
    u"a": u"а", u"c": u"с", u"e": u"е", u"o": u"о", u"p": u"р",
    u"x": u"х", u"y": u"у",
}


_SPACE_RE = re.compile(u"\\s+", re.UNICODE)


def _fold_confusables(text):
    return u"".join([_CONFUSABLES.get(char, char) for char in text])


def normalize_value(text, soft=True):
    """Приведение строки к виду для сравнения."""
    value = unicode(text or u"")
    value = value.replace(u" ", u" ")   # неразрывный пробел
    value = _SPACE_RE.sub(u" ", value).strip()
    if soft:
        value = _fold_confusables(value)
        value = value.lower()
        value = value.replace(u"ё", u"е")
    return value


# --------------------------------------------------------------------------- #
#                          подписи элементов для отчёта                       #
# --------------------------------------------------------------------------- #

def _builtins(*names):
    """BuiltInParameter по именам; отсутствующие в этой версии Revit пропускаем."""
    result = []
    for name in names:
        try:
            value = getattr(BuiltInParameter, name)
        except:
            value = None
        if value is not None:
            result.append(value)
    return result


_TYPE_NAME_PARAMS = _builtins(u"SYMBOL_NAME_PARAM", u"ALL_MODEL_TYPE_NAME")


_LEVEL_PARAMS = _builtins(
    u"WALL_BASE_CONSTRAINT",
    u"FAMILY_LEVEL_PARAM",
    u"LEVEL_PARAM",
    u"SCHEDULE_LEVEL_PARAM",
)


def _get_type_name(doc, element):
    try:
        type_element = doc.GetElement(element.GetTypeId())
    except:
        type_element = None

    if type_element is None:
        return u""

    for builtin in _TYPE_NAME_PARAMS:
        try:
            param = type_element.get_Parameter(builtin)
            if param and param.HasValue:
                value = param.AsString()
                if value:
                    return value
        except:
            pass

    try:
        return type_element.Name or u""
    except:
        return u""


def _category_key_for(element):
    category_id = spaces.get_category_id(element)
    if category_id == spaces.WALL_CAT:
        return u"curtain" if spaces.is_curtain_wall(element) else u"walls"
    if category_id == spaces.WINDOW_CAT:
        return u"windows"
    if category_id == spaces.DOOR_CAT:
        return u"doors"
    if category_id == spaces.FLOOR_CAT:
        return u"floors"
    return None


def _element_label(doc, element):
    key = _category_key_for(element)
    prefix = CATEGORY_SINGULAR.get(key, u"Элемент")
    type_name = _get_type_name(doc, element)

    try:
        element_id = element.Id.IntegerValue
    except:
        element_id = 0

    if type_name:
        return u"{0} «{1}» id {2}".format(prefix, type_name, element_id)
    return u"{0} id {1}".format(prefix, element_id)


def _level_name(doc, element):
    """Имя уровня — по нему удобно ориентироваться в длинном отчёте."""
    for builtin in _LEVEL_PARAMS:
        try:
            param = element.get_Parameter(builtin)
            if param and param.HasValue and param.StorageType == StorageType.ElementId:
                level = doc.GetElement(param.AsElementId())
                if level is not None:
                    name = level.Name
                    if name:
                        return name
        except:
            pass
    return u""


def _with_level(doc, element, text):
    level = _level_name(doc, element)
    if level:
        return u"{0} [{1}]".format(text, level)
    return text


# --------------------------------------------------------------------------- #
#                         сбор элементов для проверки                         #
# --------------------------------------------------------------------------- #

def _read_text_param(element, param_name):
    """Значение текстового параметра или None, если параметра нет.

    Возвращает (есть_параметр, текст).
    """
    try:
        param = element.LookupParameter(param_name)
    except:
        param = None

    if param is None:
        return False, u""

    try:
        if param.StorageType == StorageType.String:
            return True, unicode(param.AsString() or u"")
        value = param.AsValueString()
        return True, unicode(value or u"")
    except:
        return True, u""


def _collect_targets(doc, context, category_keys, param_name):
    """Элементы для проверки: нужные категории + наличие параметра.

    Возвращает список кортежей (элемент, ключ_категории).
    """
    wanted = set(category_keys)
    candidates = []

    if context.whole_model:
        categories = []
        if u"walls" in wanted or u"curtain" in wanted:
            categories.append(BuiltInCategory.OST_Walls)
        if u"windows" in wanted:
            categories.append(BuiltInCategory.OST_Windows)
        if u"doors" in wanted:
            categories.append(BuiltInCategory.OST_Doors)
        if u"floors" in wanted:
            categories.append(BuiltInCategory.OST_Floors)
        if categories:
            candidates = context.get_by_categories(categories)
    else:
        for element_id in context.scope_element_ids:
            try:
                element = doc.GetElement(element_id)
            except:
                element = None
            if element is not None:
                candidates.append(element)

    targets = []
    for element in candidates:
        key = _category_key_for(element)
        if key is None or key not in wanted:
            continue
        has_param, _text = _read_text_param(element, param_name)
        if not has_param:
            continue
        targets.append((element, key))

    return targets

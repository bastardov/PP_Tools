# -*- coding: utf-8 -*-
"""Хелперы «размер в ADSK_Наименование» — общие для проверок
«Размер в ADSK_Наименование на ВЕНТИЛЯЦИИ» и «… на ОТОПЛЕНИЕ»
(check_size_in_name.py, check_pipe_size_in_name.py).
"""

import clr
import re

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInParameter,
)

from spec_checks.common import (
    _get_parameter_text,
    _get_parameter_text_from_param,
)


def _get_size_text(element, names):
    value = _get_parameter_text(element, names)
    if value:
        return value

    # Для воздуховодов/труб «Размер» — это встроенный RBS_CALCULATED_SIZE.
    try:
        param = element.get_Parameter(BuiltInParameter.RBS_CALCULATED_SIZE)
        value = _get_parameter_text_from_param(param)
        if value:
            return value
    except:
        pass

    return u""


def _normalize_size(text):
    text = unicode(text or u"").lower()

    # Разделители размеров к единому виду: 400х200 / 400×200 / 400*200 -> 400x200
    for separator in [u"×", u"х", u"*"]:
        text = text.replace(separator, u"x")

    # Обозначения диаметра убираем, чтобы ⌀400 и 400 сравнивались одинаково.
    for diameter in [u"⌀", u"ø", u"Ø", u"∅"]:
        text = text.replace(diameter, u"")

    # Единицы убираем.
    text = text.replace(u"мм", u"").replace(u"mm", u"")

    # Десятичный разделитель к точке и хвостовые нули у дробей убираем,
    # чтобы 32,00 / 32.0 сравнивались как 32, а 3,50 как 3.5.
    text = text.replace(u",", u".")
    text = re.sub(
        r"\d+\.\d+",
        lambda match: match.group(0).rstrip(u"0").rstrip(u"."),
        text
    )

    # Пробелы убираем.
    text = u"".join(text.split())

    return text


def _size_end_in_name(end, name_norm):
    # Одно сечение: прямоугольное AxB совпадает и как BxA (порядок сторон
    # не важен), круглое — как есть.
    if not end:
        return True

    if end in name_norm:
        return True

    parts = end.split(u"x")
    if len(parts) == 2 and parts[0] and parts[1]:
        swapped = u"{0}x{1}".format(parts[1], parts[0])
        if swapped in name_norm:
            return True

    return False


def _size_matches_name(size_norm, name_norm):
    if not size_norm:
        return True

    # Весь размер целиком найден в имени.
    if size_norm in name_norm:
        return True

    # Размер может состоять из нескольких сечений (переходы у фитингов),
    # разделенных "-". Порядок сечений в имени может отличаться, а у
    # прямоугольного сечения стороны могут быть переставлены. Считаем
    # совпадением, если каждое сечение размера присутствует в имени.
    ends = [end for end in size_norm.split(u"-") if end]
    if not ends:
        return False

    for end in ends:
        if not _size_end_in_name(end, name_norm):
            return False

    return True

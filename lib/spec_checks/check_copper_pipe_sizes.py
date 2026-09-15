# -*- coding: utf-8 -*-
"""Проверка «5. Нестандартные размеры медной трубы» (ключ copper_pipe_sizes).

Перенесено из pp_spec_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

import clr
import re

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    StorageType,
)

from spec_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
)

from spec_checks.common import (
    MM_PER_FOOT,
    _get_category_name,
    _get_element_label,
    _get_family_name,
    _get_length_param_mm,
    _get_parameter_text,
    _get_parameter_text_from_element_or_type,
    _get_type_element,
    _get_type_name,
    _normalize_space,
    _parse_float,
)


# Значения по умолчанию опций этой проверки.
DEFAULTS = {
    "copper_keyword_source": u"Имя семейства",
    "copper_keywords": u"мед, copper",
    "copper_size_param": u"Диаметр",
    "copper_standard_sizes_mm": u"6, 8, 10, 12, 15, 18, 22, 28, 35, 42, 54, 64, 76, 89, 108",
}

# Прежнее имя: код проверки читает значения по умолчанию как
# DEFAULT_SPEC_CONFIG["ключ"] — оставлено без изменений. Здесь это
# словарь ТОЛЬКО этого файла, общий словарь собирает registry.py.
DEFAULT_SPEC_CONFIG = DEFAULTS


def _parse_number_list(raw_text):
    result = []
    parts = re.split(r"[;,/\n]+", unicode(raw_text or u""))

    for part in parts:
        value = _parse_float(part)
        if value is None:
            continue
        result.append(value)

    return result


def _parse_text_list(raw_text):
    result = []
    parts = re.split(r"[;,/\n]+", unicode(raw_text or u""))

    for part in parts:
        token = _normalize_space(part).lower()
        if token:
            result.append(token)

    return result


def _get_length_param_mm_from_element_or_type(doc, element, param_names):
    value = _get_length_param_mm(element, param_names)
    if value is not None:
        return value

    type_element = _get_type_element(doc, element)
    if type_element is None:
        return None

    return _get_length_param_mm(type_element, param_names)


def _get_pipe_diameter_mm(element):
    built_in_params = [
        BuiltInParameter.RBS_PIPE_DIAMETER_PARAM,
        BuiltInParameter.RBS_CURVE_DIAMETER_PARAM,
    ]

    for built_in_param in built_in_params:
        try:
            param = element.get_Parameter(built_in_param)
            if param is not None and param.StorageType == StorageType.Double:
                return float(param.AsDouble()) * MM_PER_FOOT
        except:
            pass

    return _get_length_param_mm(element, [u"Диаметр"])


def _is_auto_source(source_name):
    source_key = _normalize_space(source_name).lower()
    return source_key in [u"", u"авто", u"auto", u"все", u"любой"]


def _get_element_name(element):
    try:
        return unicode(element.Name or u"").strip()
    except:
        return u""


def _get_named_source_text(doc, element, source_name):
    source_key = _normalize_space(source_name).lower()

    if _is_auto_source(source_key):
        return _gather_text_blob(doc, element)

    if source_key in [u"имя семейства", u"семейство", u"family", u"family name"]:
        return _get_family_name(element)

    if source_key in [
        u"имя типа",
        u"имя типоразмера",
        u"тип",
        u"типоразмер",
        u"type",
        u"type name"
    ]:
        return _get_type_name(doc, element)

    if source_key in [u"имя элемента", u"имя", u"name", u"element name"]:
        return _get_element_name(element)

    if source_key in [u"категория", u"category"]:
        return _get_category_name(element)

    return _get_parameter_text_from_element_or_type(doc, element, [source_name])


def _get_configured_pipe_size_mm(doc, element, size_source):
    source_key = _normalize_space(size_source).lower()

    if _is_auto_source(source_key) or source_key in [u"диаметр", u"diameter"]:
        return _get_pipe_diameter_mm(element)

    return _get_length_param_mm_from_element_or_type(doc, element, [size_source])


def _gather_text_blob(doc, element):
    values = []

    try:
        values.append(unicode(element.Name or u""))
    except:
        pass

    values.append(_get_family_name(element))
    values.append(_get_type_name(doc, element))

    for param_name in [u"Материал", u"Описание", u"Комментарии к типоразмеру", u"Сегмент трубы"]:
        values.append(_get_parameter_text(element, param_name))

    type_element = _get_type_element(doc, element)
    if type_element is not None:
        for param_name in [u"Материал", u"Описание", u"Комментарии к типоразмеру", u"Сегмент трубы"]:
            values.append(_get_parameter_text(type_element, param_name))

    blob = u" ".join([value for value in values if value])
    return blob.lower()


def _is_copper_pipe(doc, element, keywords, keyword_source):
    if not keywords:
        return False
    blob = _get_named_source_text(doc, element, keyword_source)
    if not blob:
        return False

    blob = blob.lower()

    for keyword in keywords:
        if keyword and keyword in blob:
            return True

    return False


def _run_copper_pipe_sizes_check(doc, config, cache):
    elements = cache.get_by_categories([BuiltInCategory.OST_PipeCurves])
    keyword_source = config.get(
        "copper_keyword_source",
        DEFAULT_SPEC_CONFIG["copper_keyword_source"]
    )
    size_param = config.get(
        "copper_size_param",
        DEFAULT_SPEC_CONFIG["copper_size_param"]
    )
    standard_sizes = _parse_number_list(
        config.get("copper_standard_sizes_mm", DEFAULT_SPEC_CONFIG["copper_standard_sizes_mm"])
    )
    keywords = _parse_text_list(
        config.get("copper_keywords", DEFAULT_SPEC_CONFIG["copper_keywords"])
    )
    issues = []
    checked_count = 0

    for element in elements:
        if not _is_copper_pipe(doc, element, keywords, keyword_source):
            continue

        checked_count += 1
        size_mm = _get_configured_pipe_size_mm(doc, element, size_param)

        if size_mm is None:
            issues.append(CheckIssue(
                u"{0} | не удалось определить размер по полю '{1}'".format(
                    _get_element_label(doc, element),
                    size_param
                ),
                [element.Id.IntegerValue]
            ))
            continue

        matches_standard = False

        for standard_size in standard_sizes:
            if abs(size_mm - standard_size) <= 0.5:
                matches_standard = True
                break

        if not matches_standard:
            issues.append(CheckIssue(
                u"{0} | нестандартный размер {1:.1f} мм по полю '{2}'".format(
                    _get_element_label(doc, element),
                    size_mm,
                    size_param
                ),
                [element.Id.IntegerValue]
            ))

    return CheckResult(
        "copper_pipe_sizes",
        u"5. Нестандартные размеры медной трубы",
        checked_count,
        issues
    )


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "copper_keyword_source",
            u"Где искать ключевые слова (Имя семейства / Имя типа / параметр / Авто)",
            ["copper_pipe_sizes"]
        ),
        CheckOptionDefinition(
            "copper_keywords",
            u"Ключевые слова медной трубы",
            ["copper_pipe_sizes"]
        ),
        CheckOptionDefinition(
            "copper_size_param",
            u"Параметр размера для проверки (например Диаметр или ADSK_Размер)",
            ["copper_pipe_sizes"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "copper_standard_sizes_mm",
            u"Стандартные размеры, мм",
            ["copper_pipe_sizes"]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "copper_pipe_sizes",
        u"5. Нестандартные размеры медной трубы",
        u"Ищет медные трубы по выбранному полю и проверяет выбранный размер по списку стандартов.",
        _run_copper_pipe_sizes_check,
        option_keys=[
            "copper_keyword_source",
            "copper_keywords",
            "copper_size_param",
            "copper_standard_sizes_mm"
        ]
    )

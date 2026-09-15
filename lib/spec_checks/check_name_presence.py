# -*- coding: utf-8 -*-
"""Проверка «1. Наименования у труб и воздуховодов» (ключ name_presence).

Перенесено из pp_spec_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

from spec_checks.core import (
    CheckDefinition,
    CheckOptionDefinition,
)

from spec_checks.common import (
    PIPE_AND_DUCT_CATEGORIES,
    _make_simple_result,
)


# Значения по умолчанию опций этой проверки.
DEFAULTS = {
    "name_presence_param": u"ADSK_Наименование",
}

# Прежнее имя: код проверки читает значения по умолчанию как
# DEFAULT_SPEC_CONFIG["ключ"] — оставлено без изменений. Здесь это
# словарь ТОЛЬКО этого файла, общий словарь собирает registry.py.
DEFAULT_SPEC_CONFIG = DEFAULTS


def _run_name_presence_check(doc, config, cache):
    elements = cache.get_by_categories(PIPE_AND_DUCT_CATEGORIES)
    param_name = config.get("name_presence_param", DEFAULT_SPEC_CONFIG["name_presence_param"])

    return _make_simple_result(
        "name_presence",
        u"1. Наименования у труб и воздуховодов",
        elements,
        [param_name],
        u"{0} | параметр наименования пустой"
    )


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "name_presence_param",
            u"Параметр для проверки",
            ["name_presence"],
            option_type=u"param"
        ),
    ]


def get_definition():
    return CheckDefinition(
        "name_presence",
        u"1. Наименования у труб и воздуховодов",
        u"Ищет трубы и воздуховоды с пустым параметром ADSK_Наименование.",
        _run_name_presence_check,
        option_keys=["name_presence_param"]
    )

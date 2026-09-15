# -*- coding: utf-8 -*-
"""Проверка «7. Заполнение PP_Порядок сортировки» (ключ sort_order).

Перенесено из pp_spec_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

from spec_checks.core import (
    CheckDefinition,
    CheckOptionDefinition,
    CheckResult,
)

from spec_checks.common import (
    SORT_ORDER_CATEGORY_MAP,
    SORT_ORDER_CATEGORY_OPTIONS,
    _make_simple_result,
    _parse_category_keys,
)


# Значения по умолчанию опций этой проверки.
DEFAULTS = {
    "sort_order_param": u"PP_Порядок сортировки",
    "sort_order_categories": u", ".join([
        key for key, _label, _built_in in SORT_ORDER_CATEGORY_OPTIONS
    ]),
}

# Прежнее имя: код проверки читает значения по умолчанию как
# DEFAULT_SPEC_CONFIG["ключ"] — оставлено без изменений. Здесь это
# словарь ТОЛЬКО этого файла, общий словарь собирает registry.py.
DEFAULT_SPEC_CONFIG = DEFAULTS


def _run_sort_order_check(doc, config, cache):
    param_name = config.get("sort_order_param", DEFAULT_SPEC_CONFIG["sort_order_param"])
    category_keys = _parse_category_keys(
        config.get("sort_order_categories", DEFAULT_SPEC_CONFIG["sort_order_categories"])
    )

    categories = []
    for category_key in category_keys:
        built_in = SORT_ORDER_CATEGORY_MAP.get(category_key)
        if built_in is not None:
            categories.append(built_in)

    if not categories:
        return CheckResult(
            "sort_order",
            u"7. Заполнение PP_Порядок сортировки",
            0,
            []
        )

    elements = cache.get_by_categories(categories)

    return _make_simple_result(
        "sort_order",
        u"7. Заполнение PP_Порядок сортировки",
        elements,
        [param_name],
        u"{0} | PP_Порядок сортировки пустой"
    )


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "sort_order_param",
            u"Параметр для проверки",
            ["sort_order"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "sort_order_categories",
            u"Категории для проверки",
            ["sort_order"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _built_in in SORT_ORDER_CATEGORY_OPTIONS]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "sort_order",
        u"7. Заполнение PP_Порядок сортировки",
        u"Ищет элементы выбранных категорий с пустым параметром PP_Порядок сортировки.",
        _run_sort_order_check,
        option_keys=["sort_order_param", "sort_order_categories"]
    )

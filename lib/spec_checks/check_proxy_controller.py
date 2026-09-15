# -*- coding: utf-8 -*-
"""Проверка «11. Прокси-позиции по контроллеру» (ключ proxy_controller).

Перенесено из pp_spec_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

import re

from spec_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
)

from spec_checks.common import (
    _get_family_name,
    _get_number_param,
    _get_parameter_text,
    _is_zero_number,
    _normalize_space,
)


# Значения по умолчанию опций этой проверки.
DEFAULTS = {
    "proxy_ctrl_family": u"PP_Семейство контроллер 1",
    "proxy_ctrl_proxy_family": u"PP_Прокси позиция 1",
    "proxy_ctrl_code_param": u"PP_Код правила",
    "proxy_ctrl_value_param": u"ADSK_Количество",
    "proxy_ctrl_mapping": (
        u"PP_Итоговое кол-во металла для воздуховодов = МЕТАЛЛ_КРЕПЛЕНИЙ_ВОЗДУХОВОДОВ\n"
        u"PP_Итоговое кол-во металла для труб = МЕТАЛЛ_КРЕПЛЕНИЙ_ТРУБ\n"
        u"PP_Утеплитель для окожушивания = Утеплитель для окожушивания\n"
        u"PP_Металл для окожушивания = Металл для окожушивания\n"
        u"PP_Клей для огнезащиты = Клей для огнезащиты"
    ),
}

# Прежнее имя: код проверки читает значения по умолчанию как
# DEFAULT_SPEC_CONFIG["ключ"] — оставлено без изменений. Здесь это
# словарь ТОЛЬКО этого файла, общий словарь собирает registry.py.
DEFAULT_SPEC_CONFIG = DEFAULTS


def _parse_mapping_lines(raw_text):
    # Каждая строка: "Параметр контроллера = КОД".
    result = []
    for line in re.split(r"[\r\n]+", unicode(raw_text or u"")):
        if u"=" not in line:
            continue
        left, right = line.split(u"=", 1)
        left = _normalize_space(left)
        right = _normalize_space(right)
        if left and right:
            result.append((left, right))
    return result


def _values_match(ctrl_num, ctrl_text, proxy_num, proxy_text):
    if ctrl_num is not None and proxy_num is not None:
        if abs(ctrl_num - proxy_num) <= 0.01:
            return True

    if ctrl_text and _normalize_space(ctrl_text).lower() == _normalize_space(proxy_text).lower():
        return True

    return False


def _run_proxy_controller_check(doc, config, cache):
    title = u"11. Прокси-позиции по контроллеру"

    controller_family = _normalize_space(
        config.get("proxy_ctrl_family", DEFAULT_SPEC_CONFIG["proxy_ctrl_family"])
    )
    proxy_family = _normalize_space(
        config.get("proxy_ctrl_proxy_family", DEFAULT_SPEC_CONFIG["proxy_ctrl_proxy_family"])
    )
    code_param = config.get("proxy_ctrl_code_param", DEFAULT_SPEC_CONFIG["proxy_ctrl_code_param"])
    value_param = config.get("proxy_ctrl_value_param", DEFAULT_SPEC_CONFIG["proxy_ctrl_value_param"])
    mapping = _parse_mapping_lines(
        config.get("proxy_ctrl_mapping", DEFAULT_SPEC_CONFIG["proxy_ctrl_mapping"])
    )

    all_elements = cache.get_all()

    controllers = []
    proxies = []

    for element in all_elements:
        family_name = _normalize_space(_get_family_name(element))
        if controller_family and family_name == controller_family:
            controllers.append(element)
        elif proxy_family and family_name == proxy_family:
            proxies.append(element)

    issues = []

    if not controllers:
        issues.append(CheckIssue(
            u"Контроллер '{0}' не найден в проекте.".format(controller_family),
            []
        ))
        return CheckResult("proxy_controller", title, 0, issues)

    controller = controllers[0]

    if len(controllers) > 1:
        issues.append(CheckIssue(
            u"Найдено несколько контроллеров '{0}' ({1}). Проверяю первый.".format(
                controller_family,
                len(controllers)
            ),
            [element.Id.IntegerValue for element in controllers]
        ))

    # Группируем прокси по коду правила.
    proxies_by_code = {}
    for proxy in proxies:
        code = _normalize_space(_get_parameter_text(proxy, [code_param]))
        if not code:
            continue
        proxies_by_code.setdefault(code.lower(), []).append(proxy)

    checked_count = 0

    for controller_param, code in mapping:
        ctrl_num = _get_number_param(controller, [controller_param])
        ctrl_text = _get_parameter_text(controller, [controller_param])

        # Позиция контроллера заполнена? (для чисел — не ноль)
        if ctrl_num is not None:
            if _is_zero_number(ctrl_num):
                continue
        elif not ctrl_text:
            continue

        checked_count += 1

        display_value = ctrl_text
        if not display_value and ctrl_num is not None:
            display_value = unicode(ctrl_num)

        code_proxies = proxies_by_code.get(code.lower(), [])

        if not code_proxies:
            issues.append(CheckIssue(
                u"Нет прокси-позиции с кодом '{0}' | контроллер '{1}' = {2}".format(
                    code,
                    controller_param,
                    display_value
                ),
                [controller.Id.IntegerValue]
            ))
            continue

        matched = False
        proxy_values = []

        for proxy in code_proxies:
            proxy_num = _get_number_param(proxy, [value_param])
            proxy_text = _get_parameter_text(proxy, [value_param])
            proxy_values.append(proxy_text if proxy_text else u"")
            if _values_match(ctrl_num, ctrl_text, proxy_num, proxy_text):
                matched = True
                break

        if not matched:
            issues.append(CheckIssue(
                u"Код '{0}': значение не совпадает | контроллер '{1}' = {2} | прокси {3} = {4}".format(
                    code,
                    controller_param,
                    display_value,
                    value_param,
                    u", ".join(proxy_values)
                ),
                [proxy.Id.IntegerValue for proxy in code_proxies]
            ))

    return CheckResult("proxy_controller", title, checked_count, issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "proxy_ctrl_family",
            u"Семейство контроллера",
            ["proxy_controller"]
        ),
        CheckOptionDefinition(
            "proxy_ctrl_proxy_family",
            u"Семейство прокси-позиции",
            ["proxy_controller"]
        ),
        CheckOptionDefinition(
            "proxy_ctrl_code_param",
            u"Параметр кода правила в прокси",
            ["proxy_controller"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "proxy_ctrl_value_param",
            u"Параметр значения для сверки (в прокси)",
            ["proxy_controller"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "proxy_ctrl_mapping",
            u"Связки: параметр контроллера = КОД (по одной на строку)",
            ["proxy_controller"],
            option_type=u"multiline"
        ),
    ]


def get_definition():
    return CheckDefinition(
        "proxy_controller",
        u"11. Прокси-позиции по контроллеру",
        u"Для заполненных позиций контроллера проверяет наличие прокси с "
        u"нужным кодом и совпадение значения (по таблице связок).",
        _run_proxy_controller_check,
        option_keys=[
            "proxy_ctrl_family",
            "proxy_ctrl_proxy_family",
            "proxy_ctrl_code_param",
            "proxy_ctrl_value_param",
            "proxy_ctrl_mapping"
        ]
    )

# -*- coding: utf-8 -*-
"""Проверка «1. Имя и номер помещения ≠ пространство рядом» (ключ numname_mismatch).

Перенесено из pp_heatloss_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

import pp_heatloss_spaces as spaces

from heatloss_checks.core import (
    CheckCancelled,
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
)

from heatloss_checks.common import (
    CATEGORY_OPTIONS,
    _collect_targets,
    _element_label,
    _is_yes,
    _parse_category_keys,
    _read_text_param,
    _with_level,
    normalize_value,
)


# Параметр, который «Перенос данных из пространств» пишет в стены/окна/двери.
PARAM_NUM_NAME = u"PP_Номер имя помещения"


# Значения по умолчанию опций этой проверки.
DEFAULTS = {
    # Проверка «Имя и номер помещения».
    # Имя проверяемого параметра (общий с «Переносом данных»).
    "numname_param": PARAM_NUM_NAME,
    # Ключи категорий через запятую (см. CATEGORY_OPTIONS).
    "numname_categories": u"walls, curtain, windows, doors",
    # Мягкое сравнение: регистр, лишние пробелы, похожие латинские буквы.
    "numname_soft_compare": u"да",
    # Показывать элементы с пустым параметром (перенос не делали).
    "numname_report_empty": u"да",
    # Показывать элементы, для которых пространство не найдено.
    "numname_report_missing": u"да",
    # Показывать элементы, где пространство найдено ненадёжным способом.
    "numname_report_unreliable": u"да",
    # Показывать окна/двери, у которых значение разошлось с хост-стеной.
    "numname_report_host": u"да",
}


def _run_numname_check(doc, config, context):
    param_name = unicode(config.get("numname_param") or PARAM_NUM_NAME).strip()
    if not param_name:
        param_name = PARAM_NUM_NAME

    category_keys = _parse_category_keys(config.get("numname_categories"))
    soft = _is_yes(config.get("numname_soft_compare"))
    report_empty = _is_yes(config.get("numname_report_empty"))
    report_missing = _is_yes(config.get("numname_report_missing"))
    report_unreliable = _is_yes(config.get("numname_report_unreliable"))
    report_host = _is_yes(config.get("numname_report_host"))

    targets = _collect_targets(doc, context, category_keys, param_name)
    total = len(targets)

    mismatch = []
    empty = []
    missing = []
    unreliable = []
    host_mismatch = []

    finder = context.finder
    checked = 0

    for index, pair in enumerate(targets):
        element, category_key = pair
        context.report(index, total)

        try:
            hit, host = finder.find(element)
        except CheckCancelled:
            raise
        except Exception as ex:
            missing.append(CheckIssue(
                _with_level(doc, element, u"{0}: сбой поиска пространства ({1})".format(
                    _element_label(doc, element), unicode(ex))),
                [element.Id.IntegerValue]
            ))
            continue

        checked += 1
        _has_param, actual = _read_text_param(element, param_name)
        label = _element_label(doc, element)

        # Окно/дверь против хост-стены — сравнение без геометрии.
        if report_host and category_key in (u"windows", u"doors") and host is not None:
            host_has, host_value = _read_text_param(host, param_name)
            if host_has and normalize_value(host_value, soft) != normalize_value(actual, soft):
                host_mismatch.append(CheckIssue(
                    _with_level(doc, element, (
                        u"{0}: у элемента «{1}», у хост-стены id {2} — «{3}»"
                    ).format(label, actual, host.Id.IntegerValue, host_value)),
                    [element.Id.IntegerValue, host.Id.IntegerValue]
                ))

        if not hit.found:
            if report_missing:
                note = hit.note or u"пространство рядом не найдено"
                missing.append(CheckIssue(
                    _with_level(doc, element, u"{0}: {1}; записано «{2}»".format(
                        label, note, actual)),
                    [element.Id.IntegerValue]
                ))
            continue

        expected = spaces.build_num_name(hit.space)
        space_id = hit.space.Id.IntegerValue

        if not normalize_value(actual, soft):
            if report_empty:
                empty.append(CheckIssue(
                    _with_level(doc, element, (
                        u"{0}: параметр пуст, рядом пространство «{1}»"
                    ).format(label, expected)),
                    [element.Id.IntegerValue, space_id]
                ))
        elif normalize_value(actual, soft) != normalize_value(expected, soft):
            mismatch.append(CheckIssue(
                _with_level(doc, element, (
                    u"{0}: записано «{1}» → рядом «{2}» ({3})"
                ).format(label, actual, expected, hit.method_label())),
                [element.Id.IntegerValue, space_id]
            ))

        if report_unreliable and not hit.reliable:
            unreliable.append(CheckIssue(
                _with_level(doc, element, (
                    u"{0}: пространство «{1}» найдено 2D-ближайшим — "
                    u"проверьте вручную; записано «{2}»"
                ).format(label, expected, actual)),
                [element.Id.IntegerValue, space_id]
            ))

    context.report(total, total)

    scope_text = u"вся модель" if context.whole_model else u"текущее выделение"
    base_info = u"Проверено элементов: {0} ({1}). Параметр: {2}.".format(
        checked, scope_text, param_name)

    results = [
        CheckResult(
            "numname_mismatch",
            u"1. Расхождение с пространством",
            checked,
            mismatch,
            info=base_info
        )
    ]

    if report_empty:
        results.append(CheckResult(
            "numname_empty",
            u"1а. Параметр не заполнен",
            checked,
            empty,
            info=u"Перенос данных для этих элементов не делали."
        ))

    if report_missing:
        results.append(CheckResult(
            "numname_missing",
            u"1б. Пространство рядом не найдено",
            checked,
            missing,
            info=u"Проверьте расчёт объёмов, фазу и наличие пространства."
        ))

    if report_unreliable:
        results.append(CheckResult(
            "numname_unreliable",
            u"1в. Пространство найдено ненадёжно",
            checked,
            unreliable,
            info=u"Эталон получен 2D-ближайшим пространством — может быть неверным."
        ))

    if report_host:
        results.append(CheckResult(
            "numname_host",
            u"1г. Окно/дверь ≠ хост-стена",
            checked,
            host_mismatch,
            info=u"Значение проёма разошлось со стеной, в которую он вставлен."
        ))

    return results


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "numname_categories",
            u"Какие элементы проверять",
            ["numname_mismatch"],
            option_type=u"category_multiselect",
            choices=list(CATEGORY_OPTIONS)
        ),
        CheckOptionDefinition(
            "numname_param",
            u"Проверяемый параметр",
            ["numname_mismatch"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "numname_soft_compare",
            u"Мягкое сравнение: регистр, лишние пробелы, латиница (да/нет)",
            ["numname_mismatch"]
        ),
        CheckOptionDefinition(
            "numname_report_empty",
            u"Показывать элементы с пустым параметром (да/нет)",
            ["numname_mismatch"]
        ),
        CheckOptionDefinition(
            "numname_report_missing",
            u"Показывать элементы без найденного пространства (да/нет)",
            ["numname_mismatch"]
        ),
        CheckOptionDefinition(
            "numname_report_unreliable",
            u"Показывать ненадёжно найденные пространства (да/нет)",
            ["numname_mismatch"]
        ),
        CheckOptionDefinition(
            "numname_report_host",
            u"Показывать окна/двери, разошедшиеся с хост-стеной (да/нет)",
            ["numname_mismatch"]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "numname_mismatch",
        u"1. Имя и номер помещения ≠ пространство рядом",
        u"Сверяет параметр «PP_Номер имя помещения» на стенах, витражах, "
        u"окнах, дверях и перекрытиях со значением пространства, которое "
        u"находится рядом с элементом. Пространство ищется тем же "
        u"алгоритмом, что и в «Переносе данных из пространств» (зонды по "
        u"обе стороны элемента), поэтому эталон совпадает с тем, что "
        u"перенос записал бы сейчас. Для окон и дверей эталон берётся с "
        u"хост-стены — так же, как это делает перенос.\n\n"
        u"Зачем: архитектор переименовал или перенумеровал помещение, вы "
        u"обновили пространства родным инструментом Revit — а на стенах и "
        u"проёмах осталось старое имя. Проверка показывает, где именно.\n\n"
        u"Находки разносятся по отдельным строкам отчёта: расхождение, "
        u"пустой параметр, пространство не найдено, пространство найдено "
        u"ненадёжно (2D-ближайшее — эталон под вопросом), окно/дверь "
        u"разошлись с хост-стеной. Лишние строки отключаются опциями.\n\n"
        u"Проверяются только элементы, у которых этот параметр вообще "
        u"есть: остальные к теплопотерям отношения не имеют.",
        runner=lambda doc, config, context: _run_numname_check(
            doc, config, context),
        option_keys=[
            "numname_categories",
            "numname_param",
            "numname_soft_compare",
            "numname_report_empty",
            "numname_report_missing",
            "numname_report_unreliable",
            "numname_report_host",
        ]
    )

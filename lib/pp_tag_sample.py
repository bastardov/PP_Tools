# -*- coding: utf-8 -*-
u"""Взятие правила маркировки с плана — общий код для двух мест.

Обратная сторона «Шаблона марок»: вместо того чтобы искать семейство в дереве
и подбирать ему марку руками, пользователь показывает уже поставленную марку,
а правило собирается само.

Модулем пользуются кнопка «Взять марку с плана» и кнопки «С плана» внутри окна
«Шаблон марок».

    tags, scope = pp_tag_sample.collect(uidoc)

    if tags:
        result = pp_tag_sample.apply(doc, rules, tags, family_level)
        lines = pp_tag_sample.report_lines(result, scope, len(tags))

`apply` мутирует переданный список правил (тот же формат, что `tag_rules`).
Воздуховоды не трогаются: у них марка зависит ещё и от длины надписи, это
отдельная вкладка окна.
"""

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import IndependentTag
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

import pp_tagrules as tr

from pp_tags import get_tagged_element_from_tag


DEFAULT_PROMPT = u"Выберите марки-образцы и нажмите «Готово»"


class TagFilter(ISelectionFilter):
    u"""Мышью можно ткнуть только в марку."""

    def AllowElement(self, elem):
        try:
            return isinstance(elem, IndependentTag)
        except:
            return False

    def AllowReference(self, reference, point):
        return False


def collect(uidoc, use_selection=True, prompt=DEFAULT_PROMPT):
    u"""Марки-образцы: выделение либо выбор мышью.

    Возврат: (список марок, откуда). (None, None) — пользователь нажал Esc."""
    doc = uidoc.Document

    if use_selection:
        selected = []

        for eid in uidoc.Selection.GetElementIds():
            el = doc.GetElement(eid)

            if isinstance(el, IndependentTag):
                selected.append(el)

        if selected:
            return (selected, u"выделение")

    try:
        refs = uidoc.Selection.PickObjects(ObjectType.Element, TagFilter(),
                                           prompt)
    except OperationCanceledException:
        return (None, None)

    picked = []

    for ref in refs:
        el = doc.GetElement(ref.ElementId)

        if isinstance(el, IndependentTag):
            picked.append(el)

    return (picked, u"выбор мышью")


def _node_label(cat_enum, family, type_name):
    return u"{} / {}{}".format(
        tr.display_name(cat_enum), family,
        u"" if type_name is None else u" / {}".format(type_name))


def apply(doc, rules, tags, family_level):
    u"""Разобрать образцы и записать правила в rules. Возврат: отчёт-словарь."""
    result = {
        "written": {},          # ключ узла -> (подпись, семейство, тип марки)
        "added": 0,
        "updated": 0,
        "same": 0,
        "conflicts": {},
        "skipped_ducts": 0,
        "skipped_category": {},
        "skipped_no_host": 0,
        "skipped_no_symbol": 0,
        "last_key": None,       # куда смотреть в дереве после записи
    }

    for tag in tags:
        el = get_tagged_element_from_tag(doc, tag)

        if el is None:
            result["skipped_no_host"] += 1
            continue

        cat_enum = tr.get_element_category_enum(el)

        if cat_enum is None:
            name = u"?"

            try:
                if el.Category:
                    name = el.Category.Name
            except:
                pass

            result["skipped_category"][name] = \
                result["skipped_category"].get(name, 0) + 1
            continue

        if cat_enum in tr.SIZE_ENUM_NAMES:
            result["skipped_ducts"] += 1
            continue

        tag_family, tag_type = tr.symbol_names(doc.GetElement(tag.GetTypeId()))

        if not tag_family or not tag_type:
            result["skipped_no_symbol"] += 1
            continue

        family = tr.get_family_name(el)
        type_name = None if family_level else tr.get_type_name(el)

        key = (cat_enum, family, type_name)
        label = _node_label(cat_enum, family, type_name)

        # Два образца на один узел с разными марками — это ошибка выбора,
        # а не повод молча записать последнюю.
        if key in result["written"]:
            was = result["written"][key]

            if (was[1], was[2]) != (tag_family, tag_type):
                result["conflicts"][label] = u"[{} : {}] и [{} : {}]".format(
                    was[1], was[2], tag_family, tag_type)

            continue

        existing = tr.get_assignment(rules, cat_enum, family, type_name)

        if existing and (existing.get("tag_family"),
                         existing.get("tag_type")) == (tag_family, tag_type):
            result["same"] += 1
        elif existing:
            result["updated"] += 1
        else:
            result["added"] += 1

        tr.set_assignment(rules, cat_enum, family, type_name,
                          tag_family, tag_type)

        result["written"][key] = (label, tag_family, tag_type)
        result["last_key"] = key

    return result


def changed_count(result):
    return result["added"] + result["updated"] + result["same"]


def summary(result):
    u"""Одна строка для статуса окна."""
    if not result["written"]:
        if result["skipped_ducts"]:
            return (u"Марки воздуховодов сюда не пишутся — для них вкладка "
                    u"«Воздуховоды».")

        if result["skipped_no_host"]:
            return u"У выбранных марок не нашлось помеченного элемента."

        if result["skipped_category"]:
            return u"Категория выбранной марки в шаблон не входит."

        return u"Ничего не записано."

    parts = []

    if result["added"]:
        parts.append(u"добавлено {}".format(result["added"]))

    if result["updated"]:
        parts.append(u"переписано {}".format(result["updated"]))

    if result["same"]:
        parts.append(u"уже было {}".format(result["same"]))

    tail = u""

    if result["skipped_ducts"]:
        tail = u" Воздуховоды пропущены — для них вкладка «Воздуховоды»."

    last = result["written"].get(result["last_key"])
    where = u""

    if last:
        where = u" Последнее: {} → [{} : {}].".format(last[0], last[1], last[2])

    return u"С плана: {}.{}{}".format(u", ".join(parts) or u"без изменений",
                                      where, tail)


def report_lines(result, scope, total):
    u"""Полный отчёт для окна pp_wpf.show_report."""
    lines = [u"Область: {}".format(scope),
             u"Марок-образцов: {}".format(total),
             u"Правил добавлено: {}".format(result["added"]),
             u"Правил переписано: {}".format(result["updated"]),
             u"Уже было таким: {}".format(result["same"])]

    if result["written"]:
        lines.append(u"")
        lines.append(u"Записано:")

        for key in sorted(result["written"].keys()):
            label, tag_family, tag_type = result["written"][key]
            lines.append(u"  • {}   →   [{} : {}]".format(
                label, tag_family, tag_type))

    if result["conflicts"]:
        lines.append(u"")
        lines.append(u"Разные марки на одном узле — записана первая:")

        for label in sorted(result["conflicts"].keys()):
            lines.append(u"  • {}: {}".format(label,
                                              result["conflicts"][label]))

    if result["skipped_ducts"]:
        lines.append(u"")
        lines.append(
            u"Пропущено марок воздуховодов: {}. Их марка зависит ещё и от "
            u"длины надписи — вкладка «Воздуховоды» в кнопке «Шаблон "
            u"марок».".format(result["skipped_ducts"]))

    if result["skipped_category"]:
        lines.append(u"")
        lines.append(u"Пропущено — категория вне шаблона:")

        for name in sorted(result["skipped_category"].keys()):
            lines.append(u"  • {}  (×{})".format(
                name, result["skipped_category"][name]))

    if result["skipped_no_host"]:
        lines.append(u"")
        lines.append(u"Марок без помеченного элемента: {}".format(
            result["skipped_no_host"]))

    if result["skipped_no_symbol"]:
        lines.append(u"")
        lines.append(u"Марок с непрочитанным типом: {}".format(
            result["skipped_no_symbol"]))

    return lines

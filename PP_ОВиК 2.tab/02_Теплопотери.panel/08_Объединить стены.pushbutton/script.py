# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass
# Объединить стены — версия 1.
#
# Архитектор часто рисует одну стену несколькими кусками: простенок, перемычка
# над проёмом, сегменты в ряд. В расчёте теплопотерь каждый кусок даёт свою
# позицию. Инструмент собирает куски, лежащие в одной плоскости и касающиеся
# друг друга, в один контур и создаёт по нему одну стену.
#
# Вся геометрия — в lib/pp_wall_merge.py, окно — в pp_wall_merge_window.py.
# Здесь только выделение, транзакция и отчёт.

import os
import sys

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

import pp_wpf
import pp_settings
import pp_wall_merge

_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

from pp_wall_merge_window import ask_options


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


TOOL_TITLE = u"Объединение стен"

WALL_CAT = int(BuiltInCategory.OST_Walls)


class Stop(Exception):
    pass


def fail(message, title):
    u"""Показать окно ошибки и прервать сценарий."""
    pp_wpf.show_report(message, title=title, subtitle=TOOL_TITLE, is_error=True)
    raise Stop()


class WallFilter(ISelectionFilter):
    def AllowElement(self, el):
        try:
            if el.Category is None:
                return False

            return el.Category.Id.IntegerValue == WALL_CAT
        except:
            return False

    def AllowReference(self, ref, pt):
        return False


def collect_walls():
    u"""Стены из текущего выделения, а если его нет — выбор мышью."""
    walls = []

    for element_id in uidoc.Selection.GetElementIds():
        element = doc.GetElement(element_id)

        if isinstance(element, Wall):
            walls.append(element)

    if walls:
        return walls

    refs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        WallFilter(),
        u"Выберите куски одной стены → Готово"
    )

    for ref in refs:
        element = doc.GetElement(ref.ElementId)

        if isinstance(element, Wall):
            walls.append(element)

    return walls


def build_type_options(sets):
    u"""Типы стен из наборов: «Автоматически» плюс каждый тип отдельной строкой."""
    options = [{
        u"key": u"auto",
        u"title": u"Автоматически — тип самого большого куска в наборе"
    }]

    seen = {}

    for mset in sets:
        for piece in mset.pieces:
            try:
                wall_type = piece.wall.WallType
            except Exception:
                continue

            key = unicode(wall_type.Id.IntegerValue)

            if key not in seen:
                seen[key] = wall_type.Name

    for key in sorted(seen.keys(), key=lambda k: seen[k]):
        options.append({u"key": key, u"title": seen[key]})

    return options


def build_rows(sets, rejects):
    rows = []

    for index, mset in enumerate(sets):
        rows.append({
            u"text": u"Набор {} · {}".format(index + 1, mset.describe()),
            u"bad": False
        })

    for reject in rejects:
        rows.append({
            u"text": u"Не получится · {}".format(reject.describe()),
            u"bad": True
        })

    return rows


try:
    walls = collect_walls()

    if not walls:
        fail(
            u"Стены не выбраны.\n\n"
            u"Выделите куски одной стены — простенок и перемычку над проёмом "
            u"или сегменты в ряд — и запустите инструмент заново.",
            u"Нечего объединять"
        )

    sets, rejects = pp_wall_merge.plan(doc, walls)

    if not sets:
        lines = [u"Ни один набор объединить не получилось."]

        if rejects:
            lines.append(u"")
            lines.append(u"Причины:")

            for reject in rejects:
                lines.append(u"  • {}".format(reject.describe()))

        lines.append(u"")
        lines.append(
            u"Объединяются только обычные стены без проёмов, лежащие в одной "
            u"плоскости и касающиеся друг друга."
        )

        fail(u"\n".join(lines), u"Нечего объединять")

    merge_walls = sum(len(mset.pieces) for mset in sets)

    options = ask_options(
        build_rows(sets, rejects),
        build_type_options(sets),
        len(sets),
        merge_walls,
        len(rejects)
    )

    if options is None:
        raise Stop()

    chosen_key = options.get(u"type_key", u"auto")
    chosen_type = None

    if chosen_key != u"auto":
        try:
            chosen_type = doc.GetElement(ElementId(int(chosen_key)))
        except Exception:
            chosen_type = None

    # ── Объединение ──────────────────────────────────────────
    done = []
    errors = []

    transaction = Transaction(doc, u"PP: объединить стены")
    transaction.Start()

    try:
        for index, mset in enumerate(sets):
            wall_type = chosen_type or mset.master.wall.WallType

            sub = SubTransaction(doc)
            sub.Start()

            try:
                pp_wall_merge.merge(doc, mset, wall_type)
                sub.Commit()
                done.append(mset)
            except Exception as ex:
                sub.RollBack()
                errors.append(u"Набор {} · {} — {}".format(
                    index + 1, mset.describe(), unicode(ex)
                ))

        transaction.Commit()
    except Exception:
        try:
            transaction.RollBack()
        except:
            pass
        raise

    # ── Отчёт ────────────────────────────────────────────────
    merged_pieces = sum(len(mset.pieces) for mset in done)

    if not done:
        fail(
            u"Ни одну стену объединить не удалось.\n\n" + u"\n".join(errors),
            u"Объединение не выполнено"
        )

    success = [u"Объединено: {} {} → {} {}.".format(
        merged_pieces, pp_wall_merge.plural_walls(merged_pieces),
        len(done), pp_wall_merge.plural_walls(len(done))
    )]

    for index, mset in enumerate(done):
        success.append(u"  • Набор {} · {}".format(index + 1, mset.describe()))

    success.append(u"")
    success.append(u"Пользовательские параметры перенесены с самого большого куска.")
    success.append(u"Если результат не устроил — отмена по Ctrl+Z.")

    warning = list(success)

    if rejects:
        warning.append(u"")
        warning.append(u"Не получилось объединить:")

        for reject in rejects:
            warning.append(u"  • {}".format(reject.describe()))

    if errors:
        warning.append(u"")
        warning.append(u"Revit отказался создавать стену:")

        for line in errors:
            warning.append(u"  • {}".format(line))

    pp_settings.show_report(
        None,
        TOOL_TITLE,
        u"\n".join(success),
        u"\n".join(warning),
        bool(rejects or errors)
    )

except Stop:
    pass

except OperationCanceledException:
    pass

except Exception as ex:
    pp_wpf.show_report(
        unicode(ex),
        title=u"Ошибка",
        subtitle=TOOL_TITLE,
        is_error=True
    )

# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr
import random

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    BuiltInCategory,
    FillPatternElement,
    OverrideGraphicSettings,
    Color,
    ElementId,
    Transaction
)

import System


import pp_wpf
import pp_paint_dialog

TITLE = u"Окрашивание для проверки теплопотерь"


class Stop(Exception):
    u"""Осмысленная остановка: сообщение уходит в окно отчёта, транзакция откатывается."""
    pass


def fail(message):
    raise Stop(message)


def rollback():
    u"""Откат незакрытой транзакции. Вызывается из каждой ветки except."""
    try:
        if "t" in globals():
            _t = globals()["t"]
            if hasattr(_t, "HasStarted") and _t.HasStarted() and not _t.HasEnded():
                _t.RollBack()
    except Exception:
        pass


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView


PARAM_NAME = "PP_Номер имя помещения"


CATEGORIES = {
    u"Стены": BuiltInCategory.OST_Walls,
    u"Двери": BuiltInCategory.OST_Doors,
    u"Окна": BuiltInCategory.OST_Windows,
    u"Перекрытия": BuiltInCategory.OST_Floors
}


# Порядок флажков в окне: словарь CATEGORIES его не гарантирует
CATEGORY_ORDER = [u"Стены", u"Двери", u"Окна", u"Перекрытия"]


def ask_categories():
    u"""Общий диалог окраски. Возврат прежний: словарь категорий и флаг сброса."""
    result = pp_paint_dialog.ask({
        u"title": u"Окраска по помещениям",
        u"subtitle": u"Элементы одного помещения получают общий случайный цвет "
                     u"по параметру «{}». Кому параметр не проставлен — останется без цвета.".format(PARAM_NAME),
        u"categories": [(name, CATEGORIES[name])
                        for name in CATEGORY_ORDER if name in CATEGORIES],
        u"run_label": u"Окрасить",
        u"reset_label": u"Сбросить окрашивание вместо окраски",
        u"reset_hint": u"Снимет переопределения с выбранных категорий на активном виде.",
    })

    if result is None:
        return None

    return {
        "categories": dict(result[u"categories"]),
        "reset_colors": result[u"reset"],
    }


def get_solid_fill_id():
    try:
        fps = FilteredElementCollector(doc).OfClass(FillPatternElement).ToElements()

        for fp in fps:
            try:
                if fp.GetFillPattern().IsSolidFill:
                    return fp.Id
            except:
                pass
    except:
        pass

    return ElementId.InvalidElementId


def unique_colors(n):
    colors = []
    rng = random.Random()

    for i in range(n):
        h = (i * 137.508) % 360
        s = 0.65 + rng.uniform(-0.10, 0.10)
        v = 0.80 + rng.uniform(-0.10, 0.10)

        hi = int(h / 60) % 6
        f = (h / 60) - int(h / 60)

        p = v * (1 - s)
        q = v * (1 - f * s)
        t = v * (1 - (1 - f) * s)

        table = [
            (v, t, p),
            (q, v, p),
            (p, v, t),
            (p, q, v),
            (t, p, v),
            (v, p, q)
        ]

        r, g, b = table[hi]

        colors.append(
            Color(
                int(r * 255),
                int(g * 255),
                int(b * 255)
            )
        )

    return colors


def get_param_string(el, param_name):
    try:
        p = el.LookupParameter(param_name)

        if p and p.HasValue:
            value = p.AsString()

            if value:
                value = value.strip()

                if value:
                    return value
    except:
        pass

    return None


def make_ogs(color, solid_id):
    ogs = OverrideGraphicSettings()

    # Поверхности — 3D, фасады, видимые грани
    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetSurfaceForegroundPatternId(solid_id)
            ogs.SetSurfaceForegroundPatternVisible(True)

        ogs.SetSurfaceForegroundPatternColor(color)
    except:
        pass

    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetSurfaceBackgroundPatternId(solid_id)
            ogs.SetSurfaceBackgroundPatternVisible(True)

        ogs.SetSurfaceBackgroundPatternColor(color)
    except:
        pass

    # Сечения — нужно для стен на планах
    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetCutForegroundPatternId(solid_id)
            ogs.SetCutForegroundPatternVisible(True)

        ogs.SetCutForegroundPatternColor(color)
    except:
        pass

    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetCutBackgroundPatternId(solid_id)
            ogs.SetCutBackgroundPatternVisible(True)

        ogs.SetCutBackgroundPatternColor(color)
    except:
        pass

    return ogs


try:
    result = ask_categories()

    if result is None:
        raise System.OperationCanceledException()

    selected = result["categories"]
    reset_colors = result["reset_colors"]

    if not selected:
        fail(
            u"Ни одна категория не выбрана."
        )

    if reset_colors:

        elems_to_reset = []

        for cat_name, bic in selected.items():
            elems = FilteredElementCollector(doc, view.Id) \
                .OfCategory(bic) \
                .WhereElementIsNotElementType() \
                .ToElements()

            elems_to_reset.extend(elems)

        t = Transaction(doc, u"PP: Сброс окрашивания теплопотерь")
        t.Start()

        clean_ogs = OverrideGraphicSettings()
        reset_count = 0

        for e in elems_to_reset:
            try:
                view.SetElementOverrides(e.Id, clean_ogs)
                reset_count += 1
            except:
                pass

        t.Commit()

        pp_wpf.show_report(
            u"Вид: {}\nКатегории: {}\nСброшено элементов: {}".format(
            view.Name,
            u", ".join(selected.keys()),
            reset_count
            ),
            title=u"Готово",
            subtitle=TITLE
        )

        raise SystemExit

    groups = {}
    no_param = []

    for cat_name, bic in selected.items():
        elems = FilteredElementCollector(doc, view.Id) \
            .OfCategory(bic) \
            .WhereElementIsNotElementType() \
            .ToElements()

        for e in elems:
            val = get_param_string(e, PARAM_NAME)

            if val:
                if val not in groups:
                    groups[val] = []

                groups[val].append(e)

            else:
                no_param.append(e)

    if not groups and not no_param:
        fail(
            u"На активном виде не найдено элементов выбранных категорий."
        )

    solid_id = get_solid_fill_id()

    palette = unique_colors(len(groups))
    group_colors = {}

    index = 0
    for group_name in groups.keys():
        group_colors[group_name] = palette[index]
        index += 1

    applied = 0

    t = Transaction(doc, u"PP: Окрашивание для проверки теплопотерь")
    t.Start()

    for group_name, elems in groups.items():
        col = group_colors[group_name]
        ogs = make_ogs(col, solid_id)

        for e in elems:
            try:
                view.SetElementOverrides(e.Id, ogs)
                applied += 1
            except:
                pass

    clean_ogs = OverrideGraphicSettings()
    cleaned = 0

    for e in no_param:
        try:
            view.SetElementOverrides(e.Id, clean_ogs)
            cleaned += 1
        except:
            pass

    t.Commit()

    lines = [
        u"Вид: {}".format(view.Name),
        u"Категории: {}".format(u", ".join(selected.keys())),
        u"Групп найдено: {}".format(len(groups)),
        u"Элементов окрашено: {}".format(applied),
        u"Без параметра / сброшено: {}".format(cleaned)
    ]

    pp_wpf.show_report(
        u"\n".join(lines),
        title=u"Готово",
        subtitle=TITLE
    )

except System.OperationCanceledException:
    rollback()

except SystemExit:
    pass

except Stop as ex:
    rollback()

    pp_wpf.show_report(
        unicode(ex),
        title=u"Не выполнено",
        subtitle=TITLE,
        is_error=True
    )

except Exception as ex:
    rollback()

    pp_wpf.show_report(
        unicode(ex),
        title=u"Ошибка",
        subtitle=TITLE,
        is_error=True
    )

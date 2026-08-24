# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import *

import System

import pp_wpf
import pp_paint_dialog

TITLE = u"Окраска по стороне света"


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


PARAM_ORIENT = "PP_Ориентация по стороне света"

CATEGORIES = {
    u"Стены": BuiltInCategory.OST_Walls,
    u"Окна": BuiltInCategory.OST_Windows,
    u"Двери": BuiltInCategory.OST_Doors,
    u"Перекрытия": BuiltInCategory.OST_Floors
}

ORIENT_COLORS = {
    u"С":  Color(70, 130, 255),
    u"СВ": Color(70, 200, 255),
    u"В":  Color(80, 220, 140),
    u"ЮВ": Color(180, 230, 80),
    u"Ю":  Color(255, 210, 60),
    u"ЮЗ": Color(255, 150, 60),
    u"З":  Color(255, 90, 90),
    u"СЗ": Color(170, 100, 255)
}


# Порядок флажков в окне: словарь CATEGORIES его не гарантирует
CATEGORY_ORDER = [u"Стены", u"Окна", u"Двери", u"Перекрытия"]


def ask_categories():
    u"""Общий диалог окраски. Возврат прежний: словарь категорий и флаг сброса."""
    result = pp_paint_dialog.ask({
        u"title": u"Окраска по стороне света",
        u"subtitle": u"Цвет назначается по значению параметра «{}» — "
                     u"сразу видно, где ориентация не определена.".format(PARAM_ORIENT),
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


def get_param_string(el, param_name):
    try:
        p = el.LookupParameter(param_name)

        if p and p.HasValue:
            value = p.AsString()

            if value:
                value = value.strip().upper()

                if value:
                    return value
    except:
        pass

    return None


def make_ogs(color, solid_id):
    ogs = OverrideGraphicSettings()

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

    elems = []

    for cat_name, bic in selected.items():
        found = FilteredElementCollector(doc, view.Id) \
            .OfCategory(bic) \
            .WhereElementIsNotElementType() \
            .ToElements()

        elems.extend(found)

    if not elems:
        fail(
            u"На активном виде не найдено элементов выбранных категорий."
        )

    if reset_colors:
        t = Transaction(doc, u"PP: Сброс окраски по стороне света")
        t.Start()

        clean_ogs = OverrideGraphicSettings()
        reset_count = 0

        for e in elems:
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

    solid_id = get_solid_fill_id()

    orientation_groups = {}
    no_orientation = []
    unknown_orientation = []

    for e in elems:
        orient = get_param_string(e, PARAM_ORIENT)

        if not orient:
            no_orientation.append(e)
            continue

        if orient not in ORIENT_COLORS:
            unknown_orientation.append(e)
            continue

        if orient not in orientation_groups:
            orientation_groups[orient] = []

        orientation_groups[orient].append(e)

    applied = 0
    cleaned = 0

    t = Transaction(doc, u"PP: Окраска по стороне света")
    t.Start()

    for orient, group_elems in orientation_groups.items():
        ogs = make_ogs(ORIENT_COLORS[orient], solid_id)

        for e in group_elems:
            try:
                view.SetElementOverrides(e.Id, ogs)
                applied += 1
            except:
                pass

    clean_ogs = OverrideGraphicSettings()

    for e in no_orientation:
        try:
            view.SetElementOverrides(e.Id, clean_ogs)
            cleaned += 1
        except:
            pass

    for e in unknown_orientation:
        try:
            view.SetElementOverrides(e.Id, clean_ogs)
            cleaned += 1
        except:
            pass

    t.Commit()

    lines = [
        u"Вид: {}".format(view.Name),
        u"Категории: {}".format(u", ".join(selected.keys())),
        u"Окрашено элементов: {}".format(applied),
        u"Без стороны света / сброшено: {}".format(cleaned),
        u"",
        u"Группы:"
    ]

    for orient in [u"С", u"СВ", u"В", u"ЮВ", u"Ю", u"ЮЗ", u"З", u"СЗ"]:
        count = len(orientation_groups.get(orient, []))
        lines.append(u"{}: {}".format(orient, count))

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

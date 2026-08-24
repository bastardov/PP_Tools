# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr
import colorsys

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import *

import System

import pp_wpf
import pp_paint_dialog


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


PARAM_NAME = u"Rст"
TITLE = u"Окраска по типу стен (Rст)"

# Базовая палитра — контрастные цвета для первых групп
BASE_COLORS = [
    Color(70, 130, 255),
    Color(255, 90, 90),
    Color(80, 220, 140),
    Color(255, 210, 60),
    Color(170, 100, 255),
    Color(70, 200, 255),
    Color(255, 150, 60),
    Color(180, 230, 80),
    Color(255, 110, 180),
    Color(120, 200, 200),
    Color(200, 160, 90),
    Color(140, 140, 255)
]


def ask_options():
    u"""Категорий здесь нет — красятся только стены."""
    result = pp_paint_dialog.ask({
        u"title": TITLE,
        u"subtitle": u"Стены получают цвет по значению параметра «{}»: "
                     u"одинаковое значение — одинаковый цвет.".format(PARAM_NAME),
        u"run_label": u"Окрасить",
        u"reset_label": u"Сбросить окрашивание вместо окраски",
        u"reset_hint": u"Снимет переопределения со стен на активном виде.",
    })

    if result is None:
        return None

    return {
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


def get_rst_value(el):
    """Возвращает значение Rст экземпляра как строку-ключ группы, либо None."""
    try:
        p = el.LookupParameter(PARAM_NAME)

        if p and p.HasValue:
            if p.StorageType == StorageType.Double:
                # Округляем, чтобы значения вроде 3.4500001 и 3.45 попали в одну группу
                return u"{:.4f}".format(p.AsDouble()).rstrip(u"0").rstrip(u".")

            if p.StorageType == StorageType.String:
                value = p.AsString()

                if value:
                    value = value.strip()

                    if value:
                        return value

                return None

            if p.StorageType == StorageType.Integer:
                return unicode(p.AsInteger())

            value = p.AsValueString()

            if value:
                value = value.strip()

                if value:
                    return value
    except:
        pass

    return None


def generate_color(index):
    """Цвет для группы: сначала базовая палитра, дальше — генерация по золотому сечению."""
    if index < len(BASE_COLORS):
        return BASE_COLORS[index]

    hue = (index * 0.61803398875) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 0.95)

    return Color(int(r * 255), int(g * 255), int(b * 255))


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


def sort_key(value):
    """Числовые значения сортируем как числа, остальные — как строки."""
    try:
        return (0, float(value.replace(u",", u".")))
    except:
        return (1, value)


try:
    result = ask_options()

    if result is None:
        raise System.OperationCanceledException()

    reset_colors = result["reset_colors"]

    elems = FilteredElementCollector(doc, view.Id) \
        .OfCategory(BuiltInCategory.OST_Walls) \
        .WhereElementIsNotElementType() \
        .ToElements()

    if not elems:
        fail(
            u"На активном виде не найдено стен."
        )

    if reset_colors:
        t = Transaction(doc, u"PP: Сброс окраски по типу стен")
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
            u"Вид: {}\nСброшено элементов: {}".format(
            view.Name,
            reset_count
            ),
            title=u"Готово",
            subtitle=TITLE
        )

        raise SystemExit

    solid_id = get_solid_fill_id()

    value_groups = {}
    no_value = []

    for e in elems:
        value = get_rst_value(e)

        if value is None:
            no_value.append(e)
            continue

        if value not in value_groups:
            value_groups[value] = []

        value_groups[value].append(e)

    if not value_groups:
        fail(
            u"Ни у одной стены на виде не заполнен параметр «{}».".format(PARAM_NAME)
        )

    sorted_values = sorted(value_groups.keys(), key=sort_key)

    applied = 0
    cleaned = 0

    t = Transaction(doc, u"PP: Окраска по типу стен (Rст)")
    t.Start()

    group_colors = {}

    for i, value in enumerate(sorted_values):
        color = generate_color(i)
        group_colors[value] = color
        ogs = make_ogs(color, solid_id)

        for e in value_groups[value]:
            try:
                view.SetElementOverrides(e.Id, ogs)
                applied += 1
            except:
                pass

    clean_ogs = OverrideGraphicSettings()

    for e in no_value:
        try:
            view.SetElementOverrides(e.Id, clean_ogs)
            cleaned += 1
        except:
            pass

    t.Commit()

    lines = [
        u"Вид: {}".format(view.Name),
        u"Окрашено стен: {}".format(applied),
        u"Без значения {} / сброшено: {}".format(PARAM_NAME, cleaned),
        u"",
        u"Группы ({}):".format(len(sorted_values))
    ]

    for value in sorted_values:
        c = group_colors[value]
        lines.append(u"{} = {} шт.  (RGB {}, {}, {})".format(
            value,
            len(value_groups[value]),
            c.Red, c.Green, c.Blue
        ))

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

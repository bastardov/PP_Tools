# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    BuiltInCategory,
    FillPatternElement,
    OverrideGraphicSettings,
    Color,
    ElementId,
    Transaction,
    Options,
    ViewDetailLevel,
    GeometryInstance,
    Solid,
    SolidUtils,
    Transform,
    BooleanOperationsUtils,
    BooleanOperationsType,
)

import System

from pyrevit import forms

import pp_wpf
import pp_paint_dialog

TITLE = u"Окрасить задвоение"


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
view = doc.ActiveView


# Категории, в которых ищем задвоение. Сравнение идёт ТОЛЬКО внутри
# одной категории (окно с окном, стена со стеной и т.д.).
DUP_CATEGORIES = [
    (u"Окна",        BuiltInCategory.OST_Windows),
    (u"Двери",       BuiltInCategory.OST_Doors),
    (u"Перекрытия",  BuiltInCategory.OST_Floors),
    (u"Стены",       BuiltInCategory.OST_Walls),
]

RED_COLOR    = Color(220, 60, 60)
VIOLET_COLOR = Color(150, 50, 210)
GOLD_COLOR   = Color(218, 165, 32)

GEOMETRY_TOLERANCE = 0.0001
DEFAULT_OVERLAP = 90.0   # процент наложения по объёму


# ---------------------------------------------------------------------------
# Диалог
# ---------------------------------------------------------------------------

def ask_options():
    u"""Общий диалог окраски плюс порог наложения."""
    result = pp_paint_dialog.ask({
        u"title": u"Окрасить задвоение",
        u"subtitle": u"Ищет элементы, наложенные друг на друга. "
                     u"Найденная пара подсвечивается красным и фиолетовым.",
        u"categories": list(DUP_CATEGORIES),
        u"number": {
            u"label": u"ПОРОГ НАЛОЖЕНИЯ",
            u"hint": u"Доля общего объёма, начиная с которой пара считается задвоением.",
            u"unit": u"%",
            u"value": DEFAULT_OVERLAP,
            u"min": 1.0,
            u"max": 100.0,
        },
        u"run_label": u"Найти задвоения",
        u"reset_label": u"Сбросить окрашивание вместо поиска",
        u"reset_hint": u"Снимет переопределения с выбранных категорий на активном виде.",
    })

    if result is None:
        return None

    return {
        "categories": list(result[u"categories"]),
        "overlap_ratio": result[u"number"] / 100.0,
        "reset_colors": result[u"reset"],
    }


# ---------------------------------------------------------------------------
# Графические настройки
# ---------------------------------------------------------------------------

def get_solid_fill_id():
    try:
        for pattern in FilteredElementCollector(doc).OfClass(FillPatternElement).ToElements():
            try:
                if pattern.GetFillPattern().IsSolidFill:
                    return pattern.Id
            except:
                pass
    except:
        pass
    return ElementId.InvalidElementId


def make_ogs(line_color, solid_id, surface_color=None):
    """line_color — цвет рёбер; surface_color — цвет заливки граней."""
    if surface_color is None:
        surface_color = line_color

    ogs = OverrideGraphicSettings()

    try:
        ogs.SetProjectionLineColor(line_color)
    except:
        pass
    try:
        ogs.SetCutLineColor(line_color)
    except:
        pass
    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetSurfaceForegroundPatternId(solid_id)
            ogs.SetSurfaceForegroundPatternVisible(True)
        ogs.SetSurfaceForegroundPatternColor(surface_color)
    except:
        pass
    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetSurfaceBackgroundPatternId(solid_id)
            ogs.SetSurfaceBackgroundPatternVisible(True)
        ogs.SetSurfaceBackgroundPatternColor(surface_color)
    except:
        pass
    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetCutForegroundPatternId(solid_id)
            ogs.SetCutForegroundPatternVisible(True)
        ogs.SetCutForegroundPatternColor(surface_color)
    except:
        pass
    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetCutBackgroundPatternId(solid_id)
            ogs.SetCutBackgroundPatternVisible(True)
        ogs.SetCutBackgroundPatternColor(surface_color)
    except:
        pass

    return ogs


# ---------------------------------------------------------------------------
# Сбор элементов
# ---------------------------------------------------------------------------

def collect_elements_on_view(category_id):
    return list(
        FilteredElementCollector(doc, view.Id)
        .OfCategory(category_id)
        .WhereElementIsNotElementType()
        .ToElements()
    )


# ---------------------------------------------------------------------------
# Геометрия
# ---------------------------------------------------------------------------

def bboxes_touch(bbox_a, bbox_b):
    if bbox_a is None or bbox_b is None:
        return False
    if bbox_a.Max.X < bbox_b.Min.X - GEOMETRY_TOLERANCE: return False
    if bbox_b.Max.X < bbox_a.Min.X - GEOMETRY_TOLERANCE: return False
    if bbox_a.Max.Y < bbox_b.Min.Y - GEOMETRY_TOLERANCE: return False
    if bbox_b.Max.Y < bbox_a.Min.Y - GEOMETRY_TOLERANCE: return False
    if bbox_a.Max.Z < bbox_b.Min.Z - GEOMETRY_TOLERANCE: return False
    if bbox_b.Max.Z < bbox_a.Min.Z - GEOMETRY_TOLERANCE: return False
    return True


def get_element_bbox(element):
    try:
        bbox = element.get_BoundingBox(None)
        if bbox is not None:
            return bbox
    except:
        pass
    try:
        return element.get_BoundingBox(view)
    except:
        return None


def append_solids(geometry_element, solids, transform_value):
    if geometry_element is None:
        return
    for geom_obj in geometry_element:
        try:
            if isinstance(geom_obj, Solid):
                if geom_obj.Faces.Size > 0 and geom_obj.Edges.Size > 0:
                    if transform_value and not transform_value.IsIdentity:
                        solids.append(SolidUtils.CreateTransformed(geom_obj, transform_value))
                    else:
                        solids.append(geom_obj)
            elif isinstance(geom_obj, GeometryInstance):
                try:
                    inst_transform = geom_obj.Transform
                except:
                    inst_transform = Transform.Identity
                combined = transform_value.Multiply(inst_transform)
                append_solids(geom_obj.GetSymbolGeometry(), solids, combined)
        except:
            pass


def get_element_solids(element):
    solids = []
    try:
        opts = Options()
        opts.DetailLevel = ViewDetailLevel.Fine
        opts.IncludeNonVisibleObjects = True
        opts.ComputeReferences = False
        append_solids(element.get_Geometry(opts), solids, Transform.Identity)
    except:
        pass
    return solids


def solids_total_volume(solids):
    total = 0.0
    for s in solids:
        try:
            v = s.Volume
            if v > 0:
                total += v
        except:
            pass
    return total


def solids_intersection_volume(solids_a, solids_b):
    """Суммарный объём пересечения двух наборов солидов."""
    total = 0.0
    for sa in solids_a:
        for sb in solids_b:
            try:
                inter = BooleanOperationsUtils.ExecuteBooleanOperation(
                    sa, sb, BooleanOperationsType.Intersect
                )
                if inter is not None:
                    v = inter.Volume
                    if v > 0:
                        total += v
            except:
                pass
    return total


# ---------------------------------------------------------------------------
# Поиск задвоений — пары (id_a, id_b) внутри одной категории
# ---------------------------------------------------------------------------

def find_duplicate_pairs(elements, overlap_ratio, pb, checked, total):
    """Возвращает список пар id элементов, у которых объём пересечения
    >= overlap_ratio * (меньший из двух объёмов)."""
    pairs = []
    bbox_cache   = {}
    solids_cache = {}
    volume_cache = {}
    n = len(elements)

    for el in elements:
        bbox_cache[el.Id.IntegerValue] = get_element_bbox(el)

    for i in range(n):
        if pb.cancelled:
            break

        a    = elements[i]
        id_a = a.Id.IntegerValue
        bb_a = bbox_cache.get(id_a)

        for j in range(i + 1, n):
            checked[0] += 1
            if total > 0:
                pb.update_progress(checked[0], total)

            if pb.cancelled:
                break

            b    = elements[j]
            id_b = b.Id.IntegerValue
            bb_b = bbox_cache.get(id_b)

            # 1) Быстрый bbox-фильтр
            if not bboxes_touch(bb_a, bb_b):
                continue

            # 2) Солиды и объёмы (с кэшем)
            if id_a not in solids_cache:
                solids_cache[id_a] = get_element_solids(a)
                volume_cache[id_a] = solids_total_volume(solids_cache[id_a])
            if id_b not in solids_cache:
                solids_cache[id_b] = get_element_solids(b)
                volume_cache[id_b] = solids_total_volume(solids_cache[id_b])

            vol_a = volume_cache[id_a]
            vol_b = volume_cache[id_b]
            ref = min(vol_a, vol_b)
            if ref <= GEOMETRY_TOLERANCE:
                continue

            inter = solids_intersection_volume(solids_cache[id_a], solids_cache[id_b])
            if inter / ref >= overlap_ratio:
                pairs.append((id_a, id_b))

    return pairs


# ---------------------------------------------------------------------------
# Сброс переопределений
# ---------------------------------------------------------------------------

def reset_overrides(elements):
    clean = OverrideGraphicSettings()
    count = 0
    for el in elements:
        try:
            view.SetElementOverrides(el.Id, clean)
            count += 1
        except:
            pass
    return count


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

try:
    result = ask_options()

    if result is None:
        raise System.OperationCanceledException()

    if not result["categories"]:
        fail(
            u"Не выбрано ни одной категории."
        )

    # Собираем элементы по выбранным категориям (раздельно по категориям)
    elements_by_cat = []
    all_elements = []
    for name, cat in result["categories"]:
        try:
            els = collect_elements_on_view(cat)
        except:
            els = []
        elements_by_cat.append((name, els))
        all_elements.extend(els)

    if not all_elements:
        fail(
            u"На активном виде не найдено элементов выбранных категорий."
        )

    # Режим сброса
    if result["reset_colors"]:
        t = Transaction(doc, u"PP: Сброс окрашивания задвоений")
        t.Start()
        reset_count = reset_overrides(all_elements)
        t.Commit()
        pp_wpf.show_report(
            u"Вид: {}\nСброшено элементов: {}".format(view.Name, reset_count),
            title=u"Готово",
            subtitle=TITLE
        )
        raise SystemExit

    # Общее число сравнений для прогресс-бара
    total = 0
    for _name, els in elements_by_cat:
        n = len(els)
        total += n * (n - 1) // 2
    total = max(total, 1)

    all_pairs = []
    per_cat_counts = []
    checked = [0]

    with forms.ProgressBar(
        title=u"Поиск задвоений ({value} из {max_value})",
        cancellable=True,
        step=1
    ) as pb:
        for name, els in elements_by_cat:
            if pb.cancelled:
                break
            pairs = find_duplicate_pairs(els, result["overlap_ratio"], pb, checked, total)
            all_pairs.extend(pairs)
            per_cat_counts.append((name, len(pairs)))

    # Назначение цветов: первый в паре → красный, второй → фиолетовый
    red_ids    = set()
    violet_ids = set()
    for id_a, id_b in all_pairs:
        red_ids.add(id_a)
        if id_b not in red_ids:
            violet_ids.add(id_b)

    solid_id   = get_solid_fill_id()
    red_ogs    = make_ogs(GOLD_COLOR, solid_id, surface_color=RED_COLOR)
    violet_ogs = make_ogs(GOLD_COLOR, solid_id, surface_color=VIOLET_COLOR)

    red_count    = 0
    violet_count = 0

    t = Transaction(doc, u"PP: Окрасить задвоение")
    t.Start()
    for el in all_elements:
        try:
            eid = el.Id.IntegerValue
            if eid in red_ids:
                view.SetElementOverrides(el.Id, red_ogs)
                red_count += 1
            elif eid in violet_ids:
                view.SetElementOverrides(el.Id, violet_ogs)
                violet_count += 1
        except:
            pass
    t.Commit()

    detail = u""
    for name, cnt in per_cat_counts:
        detail += u"\n  — {}: {} пар".format(name, cnt)

    pp_wpf.show_report(
        u"Вид: {}\n"
        u"Порог наложения: {:.0f}%\n"
        u"Найдено пар задвоений: {}{}\n\n"
        u"Окрашено: красных {}, фиолетовых {}".format(
        view.Name,
        result["overlap_ratio"] * 100.0,
        len(all_pairs),
        detail,
        red_count,
        violet_count
        ),
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

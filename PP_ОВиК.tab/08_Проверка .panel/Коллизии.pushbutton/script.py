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
    IntersectionResultArray,
    SetComparisonResult,
    BooleanOperationsUtils,
    BooleanOperationsType,
)

import System

from pyrevit import forms

import pp_wpf
import pp_paint_dialog

TITLE = u"Проверка коллизий"


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


VENTILATION_CATEGORIES = [
    (u"Воздуховоды",            BuiltInCategory.OST_DuctCurves),
    (u"Гибкие воздуховоды",     BuiltInCategory.OST_FlexDuctCurves),
    (u"Соединительные детали",  BuiltInCategory.OST_DuctFitting),
    (u"Арматура воздуховодов",  BuiltInCategory.OST_DuctAccessory),
    (u"Воздухораспределители",  BuiltInCategory.OST_DuctTerminal),
    (u"Трубы",                  BuiltInCategory.OST_PipeCurves),
    (u"Гибкие трубы",           BuiltInCategory.OST_FlexPipeCurves),
    (u"Фасонные детали труб",   BuiltInCategory.OST_PipeFitting),
    (u"Арматура труб",          BuiltInCategory.OST_PipeAccessory),
]

GRAY_COLOR   = Color(160, 160, 160)
RED_COLOR    = Color(220, 60, 60)
VIOLET_COLOR = Color(150, 50, 210)
GOLD_COLOR   = Color(218, 165, 32)

GEOMETRY_TOLERANCE = 0.0001


# ---------------------------------------------------------------------------
# Диалог
# ---------------------------------------------------------------------------

def ask_options():
    u"""Общий диалог окраски. Возврат прежний: два флага."""
    result = pp_paint_dialog.ask({
        u"title": TITLE,
        u"subtitle": u"Воздуховоды и трубы на активном виде окрашиваются в серый. "
                     u"Найденная пара пересечений подсвечивается красным и фиолетовым.",
        u"switches": [
            {
                u"key": u"fittings",
                u"label": u"Включить фасонные детали в проверку",
                u"hint": u"Отводы и переходы дают много ложных пересечений в узлах, "
                         u"поэтому по умолчанию они не проверяются.",
                u"value": False,
            },
        ],
        u"ready_label": u"Поиск коллизий",
        u"run_label": u"Найти коллизии",
        u"reset_label": u"Сбросить окрашивание вместо поиска",
        u"reset_hint": u"Снимет переопределения с элементов активного вида.",
    })

    if result is None:
        return None

    return {
        "reset_colors":   result[u"reset"],
        "check_fittings": result[u"switches"][u"fittings"],
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
    """line_color — цвет рёбер/контуров; surface_color — цвет граней (если None = берётся line_color)."""
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


def collect_ventilation_elements():
    result = []
    for _name, cat_id in VENTILATION_CATEGORIES:
        try:
            result.extend(collect_elements_on_view(cat_id))
        except:
            pass
    return result


def collect_collision_candidates(include_fittings=False):
    """Воздуховоды и трубы (прямые и гибкие).
    Фасонные детали добавляются только если include_fittings=True —
    при этом подключённые пары фильтруются через are_connected()."""
    cat_ids = [
        BuiltInCategory.OST_DuctCurves,
        BuiltInCategory.OST_FlexDuctCurves,
        BuiltInCategory.OST_PipeCurves,
        BuiltInCategory.OST_FlexPipeCurves,
    ]
    if include_fittings:
        cat_ids += [
            BuiltInCategory.OST_DuctFitting,
            BuiltInCategory.OST_PipeFitting,
        ]
    candidates = []
    for cat_id in cat_ids:
        try:
            candidates.extend(collect_elements_on_view(cat_id))
        except:
            pass
    return candidates


# ---------------------------------------------------------------------------
# Геометрические проверки
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


def solids_intersect(solids_a, solids_b):
    """Возвращает True только при ненулевом объёме пересечения.
    Касание по грани/ребру (нормальный стык) не считается коллизией."""
    for sa in solids_a:
        for sb in solids_b:
            try:
                intersection = BooleanOperationsUtils.ExecuteBooleanOperation(
                    sa, sb, BooleanOperationsType.Intersect
                )
                if intersection is not None and intersection.Volume > GEOMETRY_TOLERANCE:
                    return True
            except:
                pass
    return False


def _point_inside_bbox(point, bbox):
    return (
        bbox.Min.X - GEOMETRY_TOLERANCE <= point.X <= bbox.Max.X + GEOMETRY_TOLERANCE and
        bbox.Min.Y - GEOMETRY_TOLERANCE <= point.Y <= bbox.Max.Y + GEOMETRY_TOLERANCE and
        bbox.Min.Z - GEOMETRY_TOLERANCE <= point.Z <= bbox.Max.Z + GEOMETRY_TOLERANCE
    )


def curve_points_inside_bbox(curve_a, curve_b, bbox_a, bbox_b):
    try:
        pts_a = [curve_a.GetEndPoint(0), curve_a.GetEndPoint(1)]
        pts_b = [curve_b.GetEndPoint(0), curve_b.GetEndPoint(1)]
    except:
        return False
    for pt in pts_a:
        if pt is not None and _point_inside_bbox(pt, bbox_b):
            return True
    for pt in pts_b:
        if pt is not None and _point_inside_bbox(pt, bbox_a):
            return True
    return False


def curves_intersect(elem_a, elem_b, bbox_a, bbox_b):
    try:
        curve_a = elem_a.Location.Curve
        curve_b = elem_b.Location.Curve
    except:
        return False

    try:
        ref = clr.Reference[IntersectionResultArray]()
        if curve_a.Intersect(curve_b, ref) != SetComparisonResult.Disjoint:
            return True
    except:
        pass

    if bbox_a is not None and bbox_b is not None:
        return curve_points_inside_bbox(curve_a, curve_b, bbox_a, bbox_b)

    return False


# ---------------------------------------------------------------------------
# Проверка коннекторов
# ---------------------------------------------------------------------------

def are_connected(elem_a, elem_b):
    """True если элементы соединены через общий коннектор Revit.
    Используется чтобы не считать коллизией нормальную врезку/отвод."""
    id_b = elem_b.Id
    try:
        for conn in elem_a.ConnectorManager.Connectors:
            try:
                for ref in conn.AllRefs:
                    if ref.Owner.Id == id_b:
                        return True
            except:
                pass
    except:
        pass
    return False


# ---------------------------------------------------------------------------
# Поиск коллизий — возвращает список пар (id_a, id_b)
# ---------------------------------------------------------------------------

def find_collision_pairs(elements, filter_connected=False):
    pairs      = []
    bbox_cache   = {}
    solids_cache = {}
    n = len(elements)

    for el in elements:
        bbox_cache[el.Id.IntegerValue] = get_element_bbox(el)

    total   = max(n * (n - 1) // 2, 1)
    checked = [0]

    with forms.ProgressBar(
        title=u"Проверка коллизий ({value} из {max_value})",
        cancellable=True,
        step=1
    ) as pb:
        for i in range(n):
            if pb.cancelled:
                break

            a    = elements[i]
            id_a = a.Id.IntegerValue
            bb_a = bbox_cache.get(id_a)

            for j in range(i + 1, n):
                checked[0] += 1
                pb.update_progress(checked[0], total)

                if pb.cancelled:
                    break

                b    = elements[j]
                id_b = b.Id.IntegerValue
                bb_b = bbox_cache.get(id_b)

                # 1) Быстрый bbox-фильтр
                if not bboxes_touch(bb_a, bb_b):
                    continue

                # 2) Точная проверка по солидам
                if id_a not in solids_cache:
                    solids_cache[id_a] = get_element_solids(a)
                if id_b not in solids_cache:
                    solids_cache[id_b] = get_element_solids(b)

                if solids_intersect(solids_cache[id_a], solids_cache[id_b]):
                    # Если включены фитинги — пропускаем легитимные подключения
                    if filter_connected and are_connected(a, b):
                        continue
                    pairs.append((id_a, id_b))
                    continue

                # 3) Резерв: пересечение осевых линий
                if curves_intersect(a, b, bb_a, bb_b):
                    if filter_connected and are_connected(a, b):
                        continue
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

    vent_elements        = collect_ventilation_elements()
    collision_candidates = collect_collision_candidates(include_fittings=result["check_fittings"])

    if not vent_elements:
        fail(
            u"На активном виде не найдено элементов вентиляции."
        )

    if result["reset_colors"]:
        t = Transaction(doc, u"PP: Сброс окрашивания коллизий")
        t.Start()
        reset_count = reset_overrides(vent_elements)
        t.Commit()
        pp_wpf.show_report(
            u"Готово.\n\nВид: {}\nСброшено элементов: {}".format(view.Name, reset_count),
            title=u"Готово",
            subtitle=TITLE
        )
        raise SystemExit

    solid_id   = get_solid_fill_id()
    gray_ogs   = make_ogs(GRAY_COLOR,   solid_id)
    # Заливка: красный/фиолетовый, линии рёбер: золото у обоих
    red_ogs    = make_ogs(GOLD_COLOR, solid_id, surface_color=RED_COLOR)
    violet_ogs = make_ogs(GOLD_COLOR, solid_id, surface_color=VIOLET_COLOR)

    collision_pairs = find_collision_pairs(
        collision_candidates,
        filter_connected=result["check_fittings"]
    )

    # Назначение цветов по парам:
    #   первый элемент пары → красный
    #   второй элемент пары → фиолетовый (если ещё не стал красным в другой паре)
    red_ids    = set()
    violet_ids = set()
    for id_a, id_b in collision_pairs:
        red_ids.add(id_a)
        if id_b not in red_ids:
            violet_ids.add(id_b)

    gray_count   = 0
    red_count    = 0
    violet_count = 0

    t = Transaction(doc, u"PP: Проверка коллизий")
    t.Start()

    for el in vent_elements:
        try:
            view.SetElementOverrides(el.Id, gray_ogs)
            gray_count += 1
        except:
            pass

    for duct in collision_candidates:
        try:
            eid = duct.Id.IntegerValue
            if eid in red_ids:
                view.SetElementOverrides(duct.Id, red_ogs)
                red_count += 1
            elif eid in violet_ids:
                view.SetElementOverrides(duct.Id, violet_ogs)
                violet_count += 1
        except:
            pass

    t.Commit()

    pp_wpf.show_report(
        u"Готово.\n\nВид: {}\n"
        u"Окрашено в серый: {}\n"
        u"Коллизионных пар: {}\n"
        u"  — красных: {}\n"
        u"  — фиолетовых: {}".format(
        view.Name,
        gray_count,
        len(collision_pairs),
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

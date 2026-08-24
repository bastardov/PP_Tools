# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")

from System.Collections.Generic import List

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    Floor,
    SpatialElementBoundaryOptions,
    SpatialElementBoundaryLocation,
    CurveLoop,
    Line,
    XYZ,
    Outline,
    BoundingBoxIntersectsFilter,
    BuiltInParameter,
    Transaction,
    ElementId
)

from Autodesk.Revit.DB.Mechanical import Space as MEPSpace
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import forms


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


# ─────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────

FT = 3.28084
SEARCH_H_M = 6.0
EXPAND_XY_M = 2.0


# ─────────────────────────────────────────────
# ФИЛЬТР ВЫБОРА ПРОСТРАНСТВ
# ─────────────────────────────────────────────

class SpaceFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            return isinstance(elem, MEPSpace)
        except:
            return False

    def AllowReference(self, ref, point):
        return False


# ─────────────────────────────────────────────
# ФУНКЦИИ
# ─────────────────────────────────────────────

def get_boundary_loops(space):
    opts = SpatialElementBoundaryOptions()
    opts.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Finish

    result = []

    segments = space.GetBoundarySegments(opts)

    if not segments:
        return result

    for seg_loop in segments:
        if len(seg_loop) < 3:
            continue

        cl = CurveLoop()

        for seg in seg_loop:
            cl.Append(seg.GetCurve())

        result.append(cl)

    return result


def find_slab_below(space):
    bbox = space.get_BoundingBox(None)

    if bbox is None:
        return None, u"нет BoundingBox у пространства"

    space_bot_z = bbox.Min.Z
    expand = EXPAND_XY_M * FT
    depth = SEARCH_H_M * FT

    min_pt = XYZ(
        bbox.Min.X - expand,
        bbox.Min.Y - expand,
        space_bot_z - depth
    )

    max_pt = XYZ(
        bbox.Max.X + expand,
        bbox.Max.Y + expand,
        space_bot_z + 0.5
    )

    outline = Outline(min_pt, max_pt)
    bb_filter = BoundingBoxIntersectsFilter(outline)

    candidates = list(
        FilteredElementCollector(doc)
        .OfClass(Floor)
        .WherePasses(bb_filter)
        .ToElements()
    )

    if not candidates:
        candidates = list(
            FilteredElementCollector(doc)
            .OfClass(Floor)
            .ToElements()
        )

        if not candidates:
            return None, u"в проекте нет ни одного Floor"

    best = None
    min_dist = float("inf")

    for elem in candidates:
        fb = elem.get_BoundingBox(None)

        if fb is None:
            continue

        dist = abs(fb.Max.Z - space_bot_z)

        if dist < min_dist:
            min_dist = dist
            best = elem

    if best is None:
        return None, u"кандидаты есть, но все без BoundingBox"

    dist_m = min_dist / FT

    return best, u"найден Floor ID:{} delta={:.2f}м".format(
        best.Id.IntegerValue,
        dist_m
    )


def project_loop_to_z(curve_loop, z):
    projected = CurveLoop()

    for curve in curve_loop:
        p0 = curve.GetEndPoint(0)
        p1 = curve.GetEndPoint(1)

        projected.Append(
            Line.CreateBound(
                XYZ(p0.X, p0.Y, z),
                XYZ(p1.X, p1.Y, z)
            )
        )

    return projected


def make_curve_loop_list(loops):
    result = List[CurveLoop]()

    for loop in loops:
        result.Add(loop)

    return result


def space_label(space):
    try:
        num = space.get_Parameter(BuiltInParameter.ROOM_NUMBER).AsString() or u"?"
    except:
        num = u"?"

    try:
        name = space.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u"?"
    except:
        name = u"?"

    return u"{} {}".format(num, name)


def get_floor_offset(floor):
    try:
        p = floor.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)

        if p and p.HasValue:
            return p.AsDouble()
    except:
        pass

    return 0.0


# ─────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────

try:
    refs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        SpaceFilter(),
        u"Выберите MEP-пространства для создания полов и нажмите Готово"
    )

    spaces = []

    for r in refs:
        el = doc.GetElement(r.ElementId)

        if el:
            spaces.append(el)

    if not spaces:
        forms.alert(
            u"Пространства не выбраны.",
            title=u"Нарезка полов по пространствам",
            exitscript=True
        )

    created_floors = []
    source_slabs = []
    source_ids_seen = set()
    log = []

    n_floors = FilteredElementCollector(doc).OfClass(Floor).GetElementCount()
    log.append(u"Проект: Floor={}".format(n_floors))
    log.append(u"Выбрано пространств: {}".format(len(spaces)))

    t = Transaction(doc, u"PP: Нарезка полов по пространствам")
    t.Start()

    for space in spaces:
        lbl = space_label(space)
        log.append(u"--- [{}] ---".format(lbl))

        try:
            bbox = space.get_BoundingBox(None)

            if bbox:
                log.append(
                    u"  Z низ={:.3f}фт Z верх={:.3f}фт".format(
                        bbox.Min.Z,
                        bbox.Max.Z
                    )
                )

            loops = get_boundary_loops(space)
            log.append(u"  контуров: {}".format(len(loops)))

            if not loops:
                log.append(u"  ПРОПУСК: контур пуст")
                continue

            slab, find_msg = find_slab_below(space)
            log.append(u"  " + find_msg)

            if slab is None:
                log.append(u"  ПРОПУСК: плита не найдена")
                continue

            f_type_id = slab.FloorType.Id
            f_level_id = slab.LevelId
            f_level = doc.GetElement(f_level_id)

            f_offset = get_floor_offset(slab)
            target_z = f_level.Elevation + f_offset

            log.append(
                u"  уровень: {} смещение: {:.3f}фт Z={:.3f}фт".format(
                    f_level.Name,
                    f_offset,
                    target_z
                )
            )

            projected = []

            for cl in loops:
                projected.append(
                    project_loop_to_z(cl, target_z)
                )

            curve_loop_list = make_curve_loop_list(projected)

            new_floor = Floor.Create(
                doc,
                curve_loop_list,
                f_type_id,
                f_level_id
            )

            np_param = new_floor.get_Parameter(
                BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM
            )

            if np_param and not np_param.IsReadOnly:
                np_param.Set(f_offset)

            created_floors.append(new_floor.Id.IntegerValue)

            log.append(
                u"  СОЗДАН пол ID:{}".format(
                    new_floor.Id.IntegerValue
                )
            )

            sid = slab.Id.IntegerValue

            if sid not in source_ids_seen:
                source_ids_seen.add(sid)
                source_slabs.append(sid)

        except Exception as ex:
            log.append(u"  ОШИБКА: {}".format(unicode(ex)))

    t.Commit()

    log.append(u"=" * 35)
    log.append(u"Создано полов: {}".format(len(created_floors)))
    log.append(u"Исходных плит-оснований: {}".format(len(source_slabs)))

    msg = u"Готово.\n\nВыбрано пространств: {}\nСоздано полов: {}\nИсходных плит-оснований: {}".format(
        len(spaces),
        len(created_floors),
        len(source_slabs)
    )

    if len(created_floors) == 0:
        msg += u"\n\nПервые строки лога:\n" + u"\n".join(log[:25])

    forms.alert(
        msg,
        title=u"Нарезка полов по пространствам"
    )

except OperationCanceledException:
    pass

except Exception as ex:
    try:
        if "t" in globals() and t.HasStarted():
            t.RollBack()
    except:
        pass

    forms.alert(
        u"Ошибка:\n\n{}".format(unicode(ex)),
        title=u"Нарезка полов по пространствам"
    )
# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")

from System.Collections.Generic import List

from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Mechanical import Space as MEPSpace
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import forms


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


FT = 3.28084
SEARCH_H_M = 6.0
EXPAND_XY_M = 2.0


class SpaceFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            return isinstance(elem, MEPSpace)
        except:
            return False

    def AllowReference(self, ref, point):
        return False


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


def find_slab_above(space):
    bbox = space.get_BoundingBox(None)

    if bbox is None:
        return None, u"нет BoundingBox у пространства"

    space_top_z = bbox.Max.Z
    expand = EXPAND_XY_M * FT
    height = SEARCH_H_M * FT

    min_pt = XYZ(
        bbox.Min.X - expand,
        bbox.Min.Y - expand,
        space_top_z - 0.5
    )

    max_pt = XYZ(
        bbox.Max.X + expand,
        bbox.Max.Y + expand,
        space_top_z + height
    )

    outline = Outline(min_pt, max_pt)
    bb_filter = BoundingBoxIntersectsFilter(outline)

    floors = FilteredElementCollector(doc) \
        .OfClass(Floor) \
        .WherePasses(bb_filter) \
        .ToElements()

    roofs = FilteredElementCollector(doc) \
        .OfClass(RoofBase) \
        .WherePasses(bb_filter) \
        .ToElements()

    candidates = list(floors) + list(roofs)

    if not candidates:
        candidates = list(
            FilteredElementCollector(doc).OfClass(Floor).ToElements()
        ) + list(
            FilteredElementCollector(doc).OfClass(RoofBase).ToElements()
        )

        if not candidates:
            return None, u"в проекте нет Floor / RoofBase"

    best = None
    min_dist = float("inf")

    for elem in candidates:
        fb = elem.get_BoundingBox(None)

        if fb is None:
            continue

        dist = abs(fb.Min.Z - space_top_z)

        if dist < min_dist:
            min_dist = dist
            best = elem

    if best is None:
        return None, u"кандидаты есть, но все без BoundingBox"

    if isinstance(best, Floor):
        kind = u"Floor"
    else:
        kind = u"RoofBase"

    return best, u"найдена {} ID:{} delta={:.2f}м".format(
        kind,
        best.Id.IntegerValue,
        min_dist / FT
    )


def get_first_floor_type_id():
    floor = FilteredElementCollector(doc) \
        .OfClass(Floor) \
        .WhereElementIsNotElementType() \
        .FirstElement()

    if floor:
        return floor.FloorType.Id

    floor_type = FilteredElementCollector(doc) \
        .OfClass(FloorType) \
        .FirstElement()

    if floor_type:
        return floor_type.Id

    return None


def get_floor_type_id(source_elem, fallback_floor_type_id):
    if isinstance(source_elem, Floor):
        return source_elem.FloorType.Id

    return fallback_floor_type_id


def get_level_id(source_elem):
    try:
        if source_elem.LevelId and source_elem.LevelId != ElementId.InvalidElementId:
            return source_elem.LevelId
    except:
        pass

    try:
        p = source_elem.get_Parameter(BuiltInParameter.ROOF_BASE_LEVEL_PARAM)
        if p:
            return p.AsElementId()
    except:
        pass

    return ElementId.InvalidElementId


def get_offset(source_elem):
    try:
        p = source_elem.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
        if p and p.HasValue:
            return p.AsDouble()
    except:
        pass

    try:
        p = source_elem.get_Parameter(BuiltInParameter.ROOF_LEVEL_OFFSET_PARAM)
        if p and p.HasValue:
            return p.AsDouble()
    except:
        pass

    return 0.0


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


try:
    refs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        SpaceFilter(),
        u"Выберите MEP-пространства для нарезки кровли / верхних плит и нажмите Готово"
    )

    spaces = []

    for r in refs:
        el = doc.GetElement(r.ElementId)

        if el:
            spaces.append(el)

    if not spaces:
        forms.alert(
            u"Пространства не выбраны.",
            title=u"Нарезка кровли по пространствам",
            exitscript=True
        )

    fallback_floor_type_id = get_first_floor_type_id()

    if fallback_floor_type_id is None:
        forms.alert(
            u"В проекте не найден ни один тип перекрытия FloorType.\n\nСоздать новые плиты невозможно.",
            title=u"Нарезка кровли по пространствам",
            exitscript=True
        )

    created_floors = []
    source_slabs = []
    source_ids_seen = set()
    log = []

    n_floors = FilteredElementCollector(doc).OfClass(Floor).GetElementCount()
    n_roofs = FilteredElementCollector(doc).OfClass(RoofBase).GetElementCount()

    log.append(u"Проект: Floor={} RoofBase={}".format(n_floors, n_roofs))
    log.append(u"Выбрано пространств: {}".format(len(spaces)))

    t = Transaction(doc, u"PP: Нарезка кровли по пространствам")
    t.Start()

    for space in spaces:
        lbl = space_label(space)
        log.append(u"--- [{}] ---".format(lbl))

        try:
            loops = get_boundary_loops(space)
            log.append(u"  контуров: {}".format(len(loops)))

            if not loops:
                log.append(u"  ПРОПУСК: контур пуст")
                continue

            slab, find_msg = find_slab_above(space)
            log.append(u"  " + find_msg)

            if slab is None:
                log.append(u"  ПРОПУСК: плита / кровля не найдена")
                continue

            f_type_id = get_floor_type_id(slab, fallback_floor_type_id)

            if f_type_id is None:
                log.append(u"  ПРОПУСК: нет FloorType")
                continue

            f_level_id = get_level_id(slab)

            if f_level_id == ElementId.InvalidElementId:
                log.append(u"  ПРОПУСК: не найден уровень")
                continue

            f_level = doc.GetElement(f_level_id)

            if f_level is None:
                log.append(u"  ПРОПУСК: уровень не найден")
                continue

            f_offset = get_offset(slab)
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
                u"  СОЗДАНА плита ID:{}".format(
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
    log.append(u"Создано плит: {}".format(len(created_floors)))
    log.append(u"Исходных плит/кровель: {}".format(len(source_slabs)))

    msg = u"Готово.\n\nВыбрано пространств: {}\nСоздано плит: {}\nИсходных плит/кровель: {}".format(
        len(spaces),
        len(created_floors),
        len(source_slabs)
    )

    if len(created_floors) == 0:
        msg += u"\n\nПервые строки лога:\n" + u"\n".join(log[:25])

    forms.alert(
        msg,
        title=u"Нарезка кровли по пространствам"
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
        title=u"Нарезка кровли по пространствам"
    )
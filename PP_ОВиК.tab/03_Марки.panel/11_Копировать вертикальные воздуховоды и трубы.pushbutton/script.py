# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

import pp_wpf
from pp_settings import show_report


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView


MM_TO_FT = 1.0 / 304.8

VERTICAL_XY_TOL_MM = 10.0
VERTICAL_MIN_Z_MM = 100.0

VERTICAL_XY_TOL = VERTICAL_XY_TOL_MM * MM_TO_FT
VERTICAL_MIN_Z = VERTICAL_MIN_Z_MM * MM_TO_FT


TARGET_CATEGORIES = [
    int(BuiltInCategory.OST_DuctCurves),
    int(BuiltInCategory.OST_PipeCurves)
]

CATEGORY_NAMES = {
    int(BuiltInCategory.OST_DuctCurves): u"Воздуховоды",
    int(BuiltInCategory.OST_PipeCurves): u"Трубы"
}


def xyz_add(a, b):
    return XYZ(a.X + b.X, a.Y + b.Y, a.Z + b.Z)


def xyz_sub(a, b):
    return XYZ(a.X - b.X, a.Y - b.Y, a.Z - b.Z)


def get_category_id(el):
    try:
        if el and el.Category:
            return el.Category.Id.IntegerValue
    except:
        pass
    return None


def get_category_name(el):
    cid = get_category_id(el)
    if cid in CATEGORY_NAMES:
        return CATEGORY_NAMES[cid]
    try:
        return el.Category.Name
    except:
        return u"Неизвестная категория"


def is_target_element(el):
    return get_category_id(el) in TARGET_CATEGORIES


def get_tagged_refs(tag):
    try:
        return list(tag.GetTaggedReferences())
    except:
        return []


def get_tagged_element_from_tag(tag):
    try:
        el_id = tag.TaggedLocalElementId
        if el_id and el_id != ElementId.InvalidElementId:
            return doc.GetElement(el_id)
    except:
        pass

    try:
        refs = tag.GetTaggedReferences()
        if refs and len(refs) > 0:
            return doc.GetElement(refs[0].ElementId)
    except:
        pass

    return None


def get_first_reference_from_tag(tag):
    refs = get_tagged_refs(tag)
    if refs and len(refs) > 0:
        return refs[0]
    return None


def get_anchor_point(el):
    try:
        loc = el.Location
        if isinstance(loc, LocationCurve):
            return loc.Curve.Evaluate(0.5, True)
    except:
        pass

    try:
        bbox = el.get_BoundingBox(view)
        if bbox:
            return XYZ(
                (bbox.Min.X + bbox.Max.X) / 2.0,
                (bbox.Min.Y + bbox.Max.Y) / 2.0,
                (bbox.Min.Z + bbox.Max.Z) / 2.0
            )
    except:
        pass

    return None


def is_vertical_mep_curve(el):
    try:
        loc = el.Location
        if not isinstance(loc, LocationCurve):
            return False

        curve = loc.Curve
        p1 = curve.GetEndPoint(0)
        p2 = curve.GetEndPoint(1)

        dx = abs(p1.X - p2.X)
        dy = abs(p1.Y - p2.Y)
        dz = abs(p1.Z - p2.Z)

        return dz >= VERTICAL_MIN_Z and dx <= VERTICAL_XY_TOL and dy <= VERTICAL_XY_TOL
    except:
        return False


def safe_get_leader_end(tag, ref):
    try:
        if ref:
            return tag.GetLeaderEnd(ref)
    except:
        pass
    return None


def safe_set_leader_end(tag, ref, pt):
    try:
        if ref and pt:
            tag.SetLeaderEnd(ref, pt)
            return True
    except:
        pass
    return False


def safe_get_leader_elbow(tag, ref):
    try:
        if ref:
            return tag.GetLeaderElbow(ref)
    except:
        pass
    return None


def safe_set_leader_elbow(tag, ref, pt):
    try:
        if ref and pt:
            tag.SetLeaderElbow(ref, pt)
            return True
    except:
        pass
    return False


def get_leader_end_condition(tag):
    try:
        if not tag.HasLeader:
            return "no_leader"
    except:
        pass

    try:
        cond_str = str(tag.LeaderEndCondition)
        if "Free" in cond_str:
            return "free"
        if "Attached" in cond_str:
            return "attached"
    except:
        pass

    try:
        ref = get_first_reference_from_tag(tag)
        pt = tag.GetLeaderEnd(ref)
        if pt is not None:
            return "free"
    except:
        pass

    return "attached"


def set_leader_end_condition(tag, mode):
    try:
        if mode == "no_leader":
            tag.HasLeader = False
            return True

        tag.HasLeader = True

        if mode == "free":
            tag.LeaderEndCondition = LeaderEndCondition.Free
            return True

        if mode == "attached":
            tag.LeaderEndCondition = LeaderEndCondition.Attached
            return True
    except:
        pass

    return False


def try_change_tag_type(tag, type_id):
    try:
        if type_id and type_id != ElementId.InvalidElementId:
            tag.ChangeTypeId(type_id)
            return True
    except:
        pass
    return False


def element_has_tag_in_view(el):
    try:
        collector = FilteredElementCollector(doc, view.Id).OfClass(IndependentTag)

        for tag in collector:
            refs = get_tagged_refs(tag)
            for r in refs:
                try:
                    if r.ElementId == el.Id:
                        return True
                except:
                    pass

            try:
                tagged_el = get_tagged_element_from_tag(tag)
                if tagged_el and tagged_el.Id == el.Id:
                    return True
            except:
                pass
    except:
        pass

    return False


class VerticalMepTagSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            if not isinstance(elem, IndependentTag):
                return False

            tagged_el = get_tagged_element_from_tag(elem)

            if tagged_el is None:
                return False

            if not is_target_element(tagged_el):
                return False

            return is_vertical_mep_curve(tagged_el)
        except:
            return False

    def AllowReference(self, reference, point):
        return False


class SameCategoryVerticalElementSelectionFilter(ISelectionFilter):
    def __init__(self, category_id):
        self.category_id = category_id

    def AllowElement(self, elem):
        try:
            if get_category_id(elem) != self.category_id:
                return False

            return is_vertical_mep_curve(elem)
        except:
            return False

    def AllowReference(self, reference, point):
        return False


TOOL_TITLE = u"Копировать марки выбранных вертикальных воздуховодов/труб"


class Stop(Exception):
    pass


def fail(message, title):
    u"""Показать окно ошибки и прервать сценарий."""
    pp_wpf.show_report(message, title=title, subtitle=TOOL_TITLE, is_error=True)
    raise Stop()


try:
    ref_pick = uidoc.Selection.PickObject(
        ObjectType.Element,
        VerticalMepTagSelectionFilter(),
        u"Выберите эталонную марку вертикального воздуховода или трубы"
    )

    ref_tag = doc.GetElement(ref_pick.ElementId)
    ref_element = get_tagged_element_from_tag(ref_tag)

    if ref_element is None:
        fail(
            u"У эталонной марки не найден связанный элемент.",
            u"Копировать марки выбранных вертикальных воздуховодов/труб"
        )

    ref_cat_id = get_category_id(ref_element)
    ref_cat_name = get_category_name(ref_element)

    ref_anchor = get_anchor_point(ref_element)
    ref_head = ref_tag.TagHeadPosition
    ref_tag_type_id = ref_tag.GetTypeId()

    if ref_anchor is None or ref_head is None:
        fail(
            u"Не удалось определить базовую точку или голову эталонной марки.",
            u"Копировать марки выбранных вертикальных воздуховодов/труб"
        )

    head_offset = xyz_sub(ref_head, ref_anchor)

    ref_ref = get_first_reference_from_tag(ref_tag)
    ref_leader_mode = get_leader_end_condition(ref_tag)

    ref_leader_end = safe_get_leader_end(ref_tag, ref_ref)
    ref_leader_elbow = safe_get_leader_elbow(ref_tag, ref_ref)

    end_offset_from_head = None
    elbow_offset_from_head = None
    elbow_offset_from_anchor = None

    if ref_leader_end is not None:
        end_offset_from_head = xyz_sub(ref_leader_end, ref_head)

    if ref_leader_elbow is not None:
        elbow_offset_from_head = xyz_sub(ref_leader_elbow, ref_head)
        elbow_offset_from_anchor = xyz_sub(ref_leader_elbow, ref_anchor)

    target_picks = uidoc.Selection.PickObjects(
        ObjectType.Element,
        SameCategoryVerticalElementSelectionFilter(ref_cat_id),
        u"Выберите вертикальные элементы, которым нужно создать марки, и нажмите 'Готово'"
    )

    if not target_picks or len(target_picks) == 0:
        fail(
            u"Целевые элементы не выбраны.",
            u"Копировать марки выбранных вертикальных воздуховодов/труб"
        )

    elements = []

    for p in target_picks:
        el = doc.GetElement(p.ElementId)
        if el and el.Id != ref_element.Id:
            elements.append(el)

    if len(elements) == 0:
        fail(
            u"После исключения эталонного элемента ничего не осталось для обработки.",
            u"Копировать марки выбранных вертикальных воздуховодов/труб"
        )

    created = []
    skipped = []

    t = Transaction(doc, u"PP: Копировать марки выбранных вертикальных воздуховодов/труб")
    t.Start()

    for el in elements:
        try:
            if element_has_tag_in_view(el):
                skipped.append(
                    u"Элемент {}: марка уже есть".format(el.Id.IntegerValue)
                )
                continue

            target_anchor = get_anchor_point(el)

            if target_anchor is None:
                skipped.append(
                    u"Элемент {}: не найдена базовая точка".format(el.Id.IntegerValue)
                )
                continue

            new_head = xyz_add(target_anchor, head_offset)
            add_leader_on_create = ref_leader_mode != "no_leader"

            try:
                new_tag = IndependentTag.Create(
                    doc,
                    view.Id,
                    Reference(el),
                    add_leader_on_create,
                    TagMode.TM_ADDBY_CATEGORY,
                    TagOrientation.Horizontal,
                    new_head
                )
            except Exception as ex:
                new_tag = None
                skipped.append(
                    u"Элемент {}: не удалось создать марку: {}".format(
                        el.Id.IntegerValue,
                        str(ex)
                    )
                )

            if new_tag is None:
                continue

            try_change_tag_type(new_tag, ref_tag_type_id)

            set_leader_end_condition(new_tag, ref_leader_mode)
            new_tag.TagHeadPosition = new_head

            target_ref = get_first_reference_from_tag(new_tag)

            set_leader_end_condition(new_tag, ref_leader_mode)
            new_tag.TagHeadPosition = new_head

            if ref_leader_mode == "attached":
                if elbow_offset_from_anchor is not None and target_ref is not None:
                    target_elbow = xyz_add(target_anchor, elbow_offset_from_anchor)
                    safe_set_leader_elbow(new_tag, target_ref, target_elbow)

            elif ref_leader_mode == "free":
                target_elbow = None
                target_end = None

                if elbow_offset_from_head is not None:
                    target_elbow = xyz_add(new_head, elbow_offset_from_head)

                if end_offset_from_head is not None:
                    target_end = xyz_add(new_head, end_offset_from_head)

                if target_elbow is not None and target_ref is not None:
                    safe_set_leader_elbow(new_tag, target_ref, target_elbow)

                if target_end is not None and target_ref is not None:
                    safe_set_leader_end(new_tag, target_ref, target_end)

                set_leader_end_condition(new_tag, "free")

                if target_elbow is not None and target_ref is not None:
                    safe_set_leader_elbow(new_tag, target_ref, target_elbow)

                if target_end is not None and target_ref is not None:
                    safe_set_leader_end(new_tag, target_ref, target_end)

                new_tag.TagHeadPosition = new_head
                set_leader_end_condition(new_tag, "free")

            elif ref_leader_mode == "no_leader":
                try:
                    new_tag.HasLeader = False
                except:
                    pass

            created.append(new_tag.Id.IntegerValue)

        except Exception as ex:
            skipped.append(
                u"Элемент {}: {}".format(el.Id.IntegerValue, str(ex))
            )

    t.Commit()

    success_msg = u"Готово.\n\nКатегория: {}\nВыбрано элементов: {}\nСоздано марок: {}\nПропущено: {}".format(
        ref_cat_name,
        len(elements),
        len(created),
        len(skipped)
    )

    warning_msg = success_msg

    if skipped:
        warning_msg += u"\n\nПервые ошибки/пропуски:\n" + u"\n".join(skipped[:15])

    show_report(
        None,
        u"Копировать марки выбранных вертикальных воздуховодов/труб",
        success_msg,
        warning_msg,
        len(skipped) > 0
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
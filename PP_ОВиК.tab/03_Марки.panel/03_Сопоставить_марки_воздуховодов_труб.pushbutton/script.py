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


class DuctPipeTagSelectionFilter(ISelectionFilter):
    def __init__(self, required_category_id=None):
        self.required_category_id = required_category_id

    def AllowElement(self, elem):
        try:
            if not isinstance(elem, IndependentTag):
                return False

            tagged_el = get_tagged_element_from_tag(elem)
            if tagged_el is None:
                return False

            if not is_target_element(tagged_el):
                return False

            if self.required_category_id is not None:
                return get_category_id(tagged_el) == self.required_category_id

            return True
        except:
            return False

    def AllowReference(self, reference, point):
        return False


TOOL_TITLE = u"Сопоставить марки воздуховодов/труб"


class Stop(Exception):
    pass


def fail(message, title):
    u"""Показать окно ошибки и прервать сценарий."""
    pp_wpf.show_report(message, title=title, subtitle=TOOL_TITLE, is_error=True)
    raise Stop()


try:
    ref_pick = uidoc.Selection.PickObject(
        ObjectType.Element,
        DuctPipeTagSelectionFilter(),
        u"Выберите эталонную марку воздуховода или трубы"
    )

    ref_tag = doc.GetElement(ref_pick.ElementId)
    ref_element = get_tagged_element_from_tag(ref_tag)

    if ref_element is None:
        fail(
            u"У эталонной марки не найден связанный элемент.",
            u"Сопоставить марки воздуховодов/труб"
        )

    ref_cat_id = get_category_id(ref_element)
    ref_cat_name = get_category_name(ref_element)

    ref_anchor = get_anchor_point(ref_element)
    ref_head = ref_tag.TagHeadPosition
    ref_tag_type_id = ref_tag.GetTypeId()

    if ref_anchor is None or ref_head is None:
        fail(
            u"Не удалось определить базовую точку или голову эталонной марки.",
            u"Сопоставить марки воздуховодов/труб"
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
        DuctPipeTagSelectionFilter(ref_cat_id),
        u"Выберите марки той же категории, которым нужно сопоставить оформление, и нажмите 'Готово'"
    )

    if not target_picks or len(target_picks) == 0:
        fail(
            u"Целевые марки не выбраны.",
            u"Сопоставить марки воздуховодов/труб"
        )

    target_tags = []

    for p in target_picks:
        tag = doc.GetElement(p.ElementId)
        if tag and tag.Id != ref_tag.Id:
            target_tags.append(tag)

    if len(target_tags) == 0:
        fail(
            u"После исключения эталонной марки ничего не осталось для обработки.",
            u"Сопоставить марки воздуховодов/труб"
        )

    updated = []
    skipped = []

    t = Transaction(doc, u"PP: Сопоставить марки воздуховодов/труб")
    t.Start()

    for tag in target_tags:
        try:
            target_el = get_tagged_element_from_tag(tag)

            if target_el is None:
                skipped.append(
                    u"Марка {}: не найден связанный элемент".format(tag.Id.IntegerValue)
                )
                continue

            target_anchor = get_anchor_point(target_el)

            if target_anchor is None:
                skipped.append(
                    u"Марка {}: не найдена базовая точка элемента".format(tag.Id.IntegerValue)
                )
                continue

            try_change_tag_type(tag, ref_tag_type_id)

            new_head = xyz_add(target_anchor, head_offset)

            set_leader_end_condition(tag, ref_leader_mode)
            tag.TagHeadPosition = new_head

            target_ref = get_first_reference_from_tag(tag)

            set_leader_end_condition(tag, ref_leader_mode)
            tag.TagHeadPosition = new_head

            if ref_leader_mode == "attached":
                if elbow_offset_from_anchor is not None and target_ref is not None:
                    target_elbow = xyz_add(target_anchor, elbow_offset_from_anchor)
                    safe_set_leader_elbow(tag, target_ref, target_elbow)

            elif ref_leader_mode == "free":
                target_elbow = None
                target_end = None

                if elbow_offset_from_head is not None:
                    target_elbow = xyz_add(new_head, elbow_offset_from_head)

                if end_offset_from_head is not None:
                    target_end = xyz_add(new_head, end_offset_from_head)

                if target_elbow is not None and target_ref is not None:
                    safe_set_leader_elbow(tag, target_ref, target_elbow)

                if target_end is not None and target_ref is not None:
                    safe_set_leader_end(tag, target_ref, target_end)

                set_leader_end_condition(tag, "free")

                if target_elbow is not None and target_ref is not None:
                    safe_set_leader_elbow(tag, target_ref, target_elbow)

                if target_end is not None and target_ref is not None:
                    safe_set_leader_end(tag, target_ref, target_end)

                tag.TagHeadPosition = new_head
                set_leader_end_condition(tag, "free")

            elif ref_leader_mode == "no_leader":
                try:
                    tag.HasLeader = False
                except:
                    pass

            updated.append(tag.Id.IntegerValue)

        except Exception as ex:
            try:
                skipped.append(
                    u"Марка {}: {}".format(tag.Id.IntegerValue, str(ex))
                )
            except:
                skipped.append(u"Ошибка: {}".format(str(ex)))

    t.Commit()

    success_msg = u"Готово.\n\nКатегория: {}\nВыбрано марок: {}\nОбновлено марок: {}\nПропущено: {}".format(
        ref_cat_name,
        len(target_tags),
        len(updated),
        len(skipped)
    )

    warning_msg = success_msg

    if skipped:
        warning_msg += u"\n\nПервые ошибки/пропуски:\n" + u"\n".join(skipped[:15])

    show_report(
        None,
        u"Сопоставить марки воздуховодов/труб",
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
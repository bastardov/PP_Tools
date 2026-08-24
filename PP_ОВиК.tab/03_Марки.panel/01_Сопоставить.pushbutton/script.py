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


ALLOWED_CATEGORIES = [
    int(BuiltInCategory.OST_DuctTerminal),
    int(BuiltInCategory.OST_DuctAccessory),
    int(BuiltInCategory.OST_MechanicalEquipment),
    int(BuiltInCategory.OST_PipeAccessory),
    int(BuiltInCategory.OST_GenericModel)
]

ALLOWED_CATEGORY_NAMES = {
    int(BuiltInCategory.OST_DuctTerminal): u"Воздухораспределители",
    int(BuiltInCategory.OST_DuctAccessory): u"Арматура воздуховодов",
    int(BuiltInCategory.OST_MechanicalEquipment): u"Оборудование",
    int(BuiltInCategory.OST_PipeAccessory): u"Арматура трубопроводов",
    int(BuiltInCategory.OST_GenericModel): u"Обобщенные модели"
}


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


def get_element_category_id(el):
    try:
        if el and el.Category:
            return el.Category.Id.IntegerValue
    except:
        pass
    return None


def get_category_name(el):
    try:
        cid = get_element_category_id(el)
        if cid in ALLOWED_CATEGORY_NAMES:
            return ALLOWED_CATEGORY_NAMES[cid]
        if el and el.Category:
            return el.Category.Name
    except:
        pass
    return u"Неизвестная категория"


def is_allowed_tag(tag):
    if not isinstance(tag, IndependentTag):
        return False

    el = get_tagged_element_from_tag(tag)

    if el is None:
        return False

    return get_element_category_id(el) in ALLOWED_CATEGORIES


def get_element_anchor_point(el):
    if el is None:
        return None

    try:
        loc = el.Location
        if isinstance(loc, LocationPoint):
            return loc.Point
    except:
        pass

    try:
        bbox = el.get_BoundingBox(None)
        if bbox:
            return XYZ(
                (bbox.Min.X + bbox.Max.X) / 2.0,
                (bbox.Min.Y + bbox.Max.Y) / 2.0,
                (bbox.Min.Z + bbox.Max.Z) / 2.0
            )
    except:
        pass

    return None


def xyz_sub(a, b):
    return XYZ(a.X - b.X, a.Y - b.Y, a.Z - b.Z)


def xyz_add(a, b):
    return XYZ(a.X + b.X, a.Y + b.Y, a.Z + b.Z)


def get_first_reference(tag):
    try:
        refs = tag.GetTaggedReferences()
        if refs and len(refs) > 0:
            return refs[0]
    except:
        pass
    return None


def get_leader_end_condition(tag):
    try:
        if not tag.HasLeader:
            return "no_leader"
    except:
        pass

    try:
        cond = tag.LeaderEndCondition
        cond_str = str(cond)

        if "Free" in cond_str:
            return "free"
        if "Attached" in cond_str:
            return "attached"
    except:
        pass

    try:
        ref = get_first_reference(tag)
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


class AllowedTagSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            return is_allowed_tag(elem)
        except:
            return False

    def AllowReference(self, reference, point):
        return False


class SameCategoryTagSelectionFilter(ISelectionFilter):
    def __init__(self, required_cat_id):
        self.required_cat_id = required_cat_id

    def AllowElement(self, elem):
        try:
            if not is_allowed_tag(elem):
                return False

            tagged_el = get_tagged_element_from_tag(elem)

            if tagged_el is None:
                return False

            return get_element_category_id(tagged_el) == self.required_cat_id
        except:
            return False

    def AllowReference(self, reference, point):
        return False


TOOL_TITLE = u"Сопоставить марки"


class Stop(Exception):
    pass


def fail(message, title):
    u"""Показать окно ошибки и прервать сценарий."""
    pp_wpf.show_report(message, title=title, subtitle=TOOL_TITLE, is_error=True)
    raise Stop()


try:
    ref_pick = uidoc.Selection.PickObject(
        ObjectType.Element,
        AllowedTagSelectionFilter(),
        u"Выберите эталонную марку"
    )

    ref_tag = doc.GetElement(ref_pick.ElementId)
    ref_element = get_tagged_element_from_tag(ref_tag)

    if ref_element is None:
        fail(
            u"У эталонной марки не найден связанный элемент.",
            u"Ошибка"
        )

    ref_cat_id = get_element_category_id(ref_element)
    ref_cat_name = get_category_name(ref_element)

    target_picks = uidoc.Selection.PickObjects(
        ObjectType.Element,
        SameCategoryTagSelectionFilter(ref_cat_id),
        u"Выберите марки для сопоставления и нажмите 'Готово'"
    )

    if not target_picks or len(target_picks) == 0:
        fail(
            u"Целевые марки не выбраны.",
            u"Сопоставить марки"
        )

    ref_anchor = get_element_anchor_point(ref_element)
    ref_head = ref_tag.TagHeadPosition

    if ref_anchor is None:
        fail(
            u"Не удалось определить базовую точку эталонного элемента.",
            u"Ошибка"
        )

    if ref_head is None:
        fail(
            u"Не удалось определить положение эталонной марки.",
            u"Ошибка"
        )

    head_offset = xyz_sub(ref_head, ref_anchor)

    ref_ref = get_first_reference(ref_tag)
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

    updated = []
    skipped = []

    t = Transaction(doc, u"PP: Сопоставить марки")
    t.Start()

    for p in target_picks:
        try:
            tag = doc.GetElement(p.ElementId)

            if tag.Id == ref_tag.Id:
                skipped.append(
                    u"Марка {}: эталонная марка пропущена".format(
                        tag.Id.IntegerValue
                    )
                )
                continue

            target_element = get_tagged_element_from_tag(tag)

            if target_element is None:
                skipped.append(
                    u"Марка {}: не найден связанный элемент".format(
                        tag.Id.IntegerValue
                    )
                )
                continue

            target_anchor = get_element_anchor_point(target_element)

            if target_anchor is None:
                skipped.append(
                    u"Марка {}: не найдена базовая точка элемента".format(
                        tag.Id.IntegerValue
                    )
                )
                continue

            new_head = xyz_add(target_anchor, head_offset)

            set_leader_end_condition(tag, ref_leader_mode)
            tag.TagHeadPosition = new_head

            target_ref = get_first_reference(tag)

            set_leader_end_condition(tag, ref_leader_mode)
            tag.TagHeadPosition = new_head

            if ref_leader_mode == "attached":
                if elbow_offset_from_anchor is not None and target_ref is not None:
                    target_elbow = xyz_add(target_anchor, elbow_offset_from_anchor)
                    safe_set_leader_elbow(tag, target_ref, target_elbow)

                tag.TagHeadPosition = new_head

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
                    u"Марка {}: {}".format(
                        tag.Id.IntegerValue,
                        str(ex)
                    )
                )
            except:
                skipped.append(u"Ошибка: {}".format(str(ex)))

    t.Commit()

    success_msg = u"Готово.\n\nКатегория: {}\nОбновлено марок: {}\nПропущено: {}".format(
        ref_cat_name,
        len(updated),
        len(skipped)
    )

    warning_msg = u"Обновлено: {}\nПропущено: {}\n\nПервые ошибки/пропуски:\n{}".format(
        len(updated),
        len(skipped),
        u"\n".join(skipped[:15])
    )

    show_report(
        None,
        u"Сопоставить марки",
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
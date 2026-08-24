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
    int(BuiltInCategory.OST_GenericModel),

    int(BuiltInCategory.OST_PipeCurves),
    int(BuiltInCategory.OST_DuctCurves)
]


ALLOWED_CATEGORY_NAMES = {
    int(BuiltInCategory.OST_DuctTerminal): u"Воздухораспределители",
    int(BuiltInCategory.OST_DuctAccessory): u"Арматура воздуховодов",
    int(BuiltInCategory.OST_MechanicalEquipment): u"Оборудование",
    int(BuiltInCategory.OST_PipeAccessory): u"Арматура трубопроводов",
    int(BuiltInCategory.OST_GenericModel): u"Обобщенные модели",

    int(BuiltInCategory.OST_PipeCurves): u"Трубы",
    int(BuiltInCategory.OST_DuctCurves): u"Воздуховоды"
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


def is_allowed_element(el):
    return get_element_category_id(el) in ALLOWED_CATEGORIES


def is_allowed_tag(tag):
    try:
        if not isinstance(tag, IndependentTag):
            return False

        el = get_tagged_element_from_tag(tag)
        if el is None:
            return False

        return is_allowed_element(el)
    except:
        return False


def get_tag_type_name(tag):
    try:
        t = doc.GetElement(tag.GetTypeId())
        if t:
            p = t.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
            if p:
                return p.AsString()
            return t.Name
    except:
        pass

    return u"Неизвестный тип"


def safe_change_type(tag, type_id):
    try:
        if type_id and type_id != ElementId.InvalidElementId:
            tag.ChangeTypeId(type_id)
            return True
    except:
        pass

    return False


class AllowedTagSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return is_allowed_tag(elem)

    def AllowReference(self, reference, point):
        return False


class SameCategoryTagSelectionFilter(ISelectionFilter):
    def __init__(self, ref_category_id):
        self.ref_category_id = ref_category_id

    def AllowElement(self, elem):
        try:
            if not isinstance(elem, IndependentTag):
                return False

            el = get_tagged_element_from_tag(elem)
            if el is None:
                return False

            return get_element_category_id(el) == self.ref_category_id
        except:
            return False

    def AllowReference(self, reference, point):
        return False


TOOL_TITLE = u"Применить тип марки"


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
    ref_tag_type_id = ref_tag.GetTypeId()
    ref_tag_type_name = get_tag_type_name(ref_tag)

    target_picks = uidoc.Selection.PickObjects(
        ObjectType.Element,
        SameCategoryTagSelectionFilter(ref_cat_id),
        u"Выберите марки той же категории, которым нужно применить тип, и нажмите 'Готово'"
    )

    if not target_picks or len(target_picks) == 0:
        fail(
            u"Целевые марки не выбраны.",
            u"Применить тип марки"
        )

    updated = []
    skipped = []

    t = Transaction(doc, u"PP: Применить тип марки")
    t.Start()

    for p in target_picks:
        try:
            tag = doc.GetElement(p.ElementId)

            if tag is None:
                skipped.append(u"Пропуск: марка не найдена")
                continue

            if tag.Id == ref_tag.Id:
                skipped.append(
                    u"Марка {}: эталонная марка пропущена".format(
                        tag.Id.IntegerValue
                    )
                )
                continue

            current_type_id = tag.GetTypeId()

            if current_type_id == ref_tag_type_id:
                skipped.append(
                    u"Марка {}: тип уже совпадает".format(
                        tag.Id.IntegerValue
                    )
                )
                continue

            ok = safe_change_type(tag, ref_tag_type_id)

            if ok:
                updated.append(tag.Id.IntegerValue)
            else:
                skipped.append(
                    u"Марка {}: не удалось применить тип '{}'".format(
                        tag.Id.IntegerValue,
                        ref_tag_type_name
                    )
                )

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

    success_msg = u"Готово.\n\nКатегория: {}\nТип эталона: {}\n\nОбновлено марок: {}\nПропущено: {}".format(
        ref_cat_name,
        ref_tag_type_name,
        len(updated),
        len(skipped)
    )

    warning_msg = success_msg

    if skipped:
        warning_msg += u"\n\nПервые ошибки/пропуски:\n" + u"\n".join(skipped[:15])

    show_report(
        None,
        u"Применить тип марки",
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
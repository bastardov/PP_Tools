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


def get_family_name(el):
    try:
        if hasattr(el, "Symbol") and el.Symbol and el.Symbol.Family:
            return el.Symbol.Family.Name
    except:
        pass

    try:
        t = doc.GetElement(el.GetTypeId())
        if t and hasattr(t, "FamilyName"):
            return t.FamilyName
    except:
        pass

    return None


def get_type_id(el):
    try:
        return el.GetTypeId()
    except:
        return ElementId.InvalidElementId


def get_type_name(el):
    try:
        t = doc.GetElement(el.GetTypeId())
        if t:
            p = t.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
            if p:
                return p.AsString()
            return t.Name
    except:
        pass
    return None


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


def collect_matching_tags_in_active_view(ref_cat_id, ref_family_name, ref_type_id, ref_tag_id, ref_element_id):
    result = []
    collector = FilteredElementCollector(doc, view.Id).OfClass(IndependentTag)

    for tag in collector:
        try:
            if tag.Id == ref_tag_id:
                continue

            tagged_el = get_tagged_element_from_tag(tag)

            if tagged_el is None:
                continue

            if tagged_el.Id == ref_element_id:
                continue

            if get_element_category_id(tagged_el) != ref_cat_id:
                continue

            if get_family_name(tagged_el) != ref_family_name:
                continue

            if get_type_id(tagged_el) != ref_type_id:
                continue

            result.append(tag)

        except:
            pass

    return result


class AllowedTagSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            return is_allowed_tag(elem)
        except:
            return False

    def AllowReference(self, reference, point):
        return False


TOOL_TITLE = u"Применить тип марки по типу"


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

    if not is_allowed_element(ref_element):
        fail(
            u"Категория эталонной марки не поддерживается.",
            u"Ошибка"
        )

    ref_cat_id = get_element_category_id(ref_element)
    ref_cat_name = get_category_name(ref_element)
    ref_family_name = get_family_name(ref_element)
    ref_type_id = get_type_id(ref_element)
    ref_type_name = get_type_name(ref_element)

    ref_tag_type_id = ref_tag.GetTypeId()
    ref_tag_type_name = get_tag_type_name(ref_tag)

    target_tags = collect_matching_tags_in_active_view(
        ref_cat_id,
        ref_family_name,
        ref_type_id,
        ref_tag.Id,
        ref_element.Id
    )

    if not target_tags:
        fail(
            u"Подходящие марки не найдены.\n\nКатегория: {}\nСемейство: {}\nТип элемента: {}\nТип марки эталона: {}".format(
                ref_cat_name,
                ref_family_name,
                ref_type_name,
                ref_tag_type_name
            ),
            u"Применить тип марки по типу"
        )

    updated = []
    skipped = []

    t = Transaction(doc, u"PP: Применить тип марки по типу")
    t.Start()

    for tag in target_tags:
        try:
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

    success_msg = u"Готово.\n\nКатегория: {}\nСемейство: {}\nТип элемента: {}\nТип марки эталона: {}\n\nНайдено марок: {}\nОбновлено марок: {}\nПропущено: {}".format(
        ref_cat_name,
        ref_family_name,
        ref_type_name,
        ref_tag_type_name,
        len(target_tags),
        len(updated),
        len(skipped)
    )

    warning_msg = success_msg

    if skipped:
        warning_msg += u"\n\nПервые ошибки/пропуски:\n" + u"\n".join(skipped[:15])

    show_report(
        None,
        u"Применить тип марки по типу",
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
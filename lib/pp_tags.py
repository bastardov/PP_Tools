# -*- coding: utf-8 -*-

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ISelectionFilter


ALLOWED_CATEGORIES = [
    int(BuiltInCategory.OST_DuctTerminal),
    int(BuiltInCategory.OST_DuctAccessory),
    int(BuiltInCategory.OST_MechanicalEquipment),
    int(BuiltInCategory.OST_PipeAccessory)
]

ALLOWED_CATEGORY_NAMES = {
    int(BuiltInCategory.OST_DuctTerminal): u"Воздухораспределители",
    int(BuiltInCategory.OST_DuctAccessory): u"Арматура воздуховодов",
    int(BuiltInCategory.OST_MechanicalEquipment): u"Оборудование",
    int(BuiltInCategory.OST_PipeAccessory): u"Арматура трубопроводов"
}


def get_tagged_element_from_tag(doc, tag):
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


def is_allowed_tag(doc, tag):
    try:
        if not isinstance(tag, IndependentTag):
            return False

        el = get_tagged_element_from_tag(doc, tag)

        if el is None:
            return False

        return is_allowed_element(el)
    except:
        return False


def get_tag_type_name(doc, tag):
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
    def __init__(self, doc):
        self.doc = doc

    def AllowElement(self, elem):
        try:
            return is_allowed_tag(self.doc, elem)
        except:
            return False

    def AllowReference(self, reference, point):
        return False


class SameCategoryTagSelectionFilter(ISelectionFilter):
    def __init__(self, doc, required_cat_id):
        self.doc = doc
        self.required_cat_id = required_cat_id

    def AllowElement(self, elem):
        try:
            if not is_allowed_tag(self.doc, elem):
                return False

            tagged_el = get_tagged_element_from_tag(self.doc, elem)

            if tagged_el is None:
                return False

            return get_element_category_id(tagged_el) == self.required_cat_id
        except:
            return False

    def AllowReference(self, reference, point):
        return False
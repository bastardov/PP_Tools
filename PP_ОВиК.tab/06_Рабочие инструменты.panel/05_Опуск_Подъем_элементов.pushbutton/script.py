# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

u"""Простое групповое перемещение выбранных MEP-элементов по вертикали."""

import clr
import os
import sys

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from System.Collections.Generic import List

from Autodesk.Revit.DB import (
    BuiltInCategory,
    CategoryType,
    ElementId,
    ElementTransformUtils,
    FailureProcessingResult,
    FailureSeverity,
    FilteredElementCollector,
    IFailuresPreprocessor,
    Level,
    Transaction,
    TransactionStatus,
    XYZ,
)
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

from pyrevit import script

import pp_wpf


_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

import pp_element_move_window


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

TITLE = u"Опуск / Подъём элементов"
MM_TO_FT = 1.0 / 304.8
EPS_MM = 0.05


ALLOWED_CATEGORY_IDS = set([
    int(BuiltInCategory.OST_PipeCurves),
    int(BuiltInCategory.OST_FlexPipeCurves),
    int(BuiltInCategory.OST_PipeFitting),
    int(BuiltInCategory.OST_PipeAccessory),
    int(BuiltInCategory.OST_DuctCurves),
    int(BuiltInCategory.OST_FlexDuctCurves),
    int(BuiltInCategory.OST_DuctFitting),
    int(BuiltInCategory.OST_DuctAccessory),
    int(BuiltInCategory.OST_DuctTerminal),
    int(BuiltInCategory.OST_MechanicalEquipment),
    int(BuiltInCategory.OST_PlumbingFixtures),
    int(BuiltInCategory.OST_Sprinklers),
])


def category_id(element):
    try:
        return element.Category.Id.IntegerValue
    except Exception:
        return None


def is_allowed(element):
    if element is None:
        return False

    try:
        if element.Category.CategoryType != CategoryType.Model:
            return False
    except Exception:
        return False

    return category_id(element) in ALLOWED_CATEGORY_IDS


class MepElementSelectionFilter(ISelectionFilter):

    def AllowElement(self, element):
        return is_allowed(element)

    def AllowReference(self, reference, point):
        return False


class MoveFailurePreprocessor(IFailuresPreprocessor):

    def __init__(self):
        self.warnings = []

    def PreprocessFailures(self, failures_accessor):
        try:
            messages = failures_accessor.GetFailureMessages()
        except Exception:
            return FailureProcessingResult.Continue

        for message in messages:
            try:
                if message.GetSeverity() != FailureSeverity.Warning:
                    continue

                self.warnings.append(unicode(message.GetDescriptionText()))
                failures_accessor.DeleteWarning(message)
            except Exception:
                pass

        return FailureProcessingResult.Continue


def show_error(title, body):
    pp_wpf.show_report(
        body,
        title=title,
        subtitle=TITLE,
        is_error=True)


def pick_elements():
    preselected_ids = list(uidoc.Selection.GetElementIds())
    elements = []
    ignored = 0

    for element_id in preselected_ids:
        element = doc.GetElement(element_id)

        if is_allowed(element):
            elements.append(element)
        else:
            ignored += 1

    if elements:
        return elements, ignored

    try:
        references = uidoc.Selection.PickObjects(
            ObjectType.Element,
            MepElementSelectionFilter(),
            u"Выберите трубы, воздуховоды, фитинги, арматуру и оборудование; затем нажмите «Готово»")
    except OperationCanceledException:
        return None, 0

    result = []

    for reference in references:
        element = doc.GetElement(reference.ElementId)

        if is_allowed(element):
            result.append(element)

    return result, 0


def element_label(element):
    try:
        return u"{} (id {})".format(element.Category.Name, element.Id.IntegerValue)
    except Exception:
        return u"Элемент"


def invalid_locked_elements(elements):
    pinned = []
    grouped = []

    for element in elements:
        try:
            if element.Pinned:
                pinned.append(element)
        except Exception:
            pass

        try:
            if element.GroupId != ElementId.InvalidElementId:
                grouped.append(element)
        except Exception:
            pass

    return pinned, grouped


def connector_manager(element):
    try:
        return element.ConnectorManager
    except Exception:
        pass

    try:
        return element.MEPModel.ConnectorManager
    except Exception:
        return None


def connector_list(element):
    manager = connector_manager(element)

    if manager is None:
        return []

    result = []

    try:
        for connector in manager.Connectors:
            result.append(connector)
    except Exception:
        pass

    return result


def external_neighbor_ids(elements):
    selected_ids = set(element.Id.IntegerValue for element in elements)
    external_ids = set()

    for element in elements:
        for connector in connector_list(element):
            try:
                for reference in connector.AllRefs:
                    owner = reference.Owner

                    if owner is None:
                        continue

                    try:
                        if owner.Category is None or \
                                owner.Category.CategoryType != CategoryType.Model:
                            continue
                    except Exception:
                        continue

                    owner_id = owner.Id.IntegerValue

                    if owner_id not in selected_ids:
                        external_ids.add(owner_id)
            except Exception:
                pass

    return external_ids


def element_anchor_z(element):
    connector_zs = []

    for connector in connector_list(element):
        try:
            connector_zs.append(connector.Origin.Z)
        except Exception:
            pass

    if connector_zs:
        return sum(connector_zs) / float(len(connector_zs))

    try:
        location = element.Location
        point = location.Point
        return point.Z
    except Exception:
        pass

    try:
        curve = element.Location.Curve
        return (curve.GetEndPoint(0).Z + curve.GetEndPoint(1).Z) / 2.0
    except Exception:
        pass

    z_min, z_max = element_z_range(element)

    if z_min is not None and z_max is not None:
        return (z_min + z_max) / 2.0

    return None


def element_z_range(element):
    try:
        box = element.get_BoundingBox(None)
    except Exception:
        box = None

    if box is None:
        return None, None

    points = []

    for x in (box.Min.X, box.Max.X):
        for y in (box.Min.Y, box.Max.Y):
            for z in (box.Min.Z, box.Max.Z):
                point = XYZ(x, y, z)

                try:
                    point = box.Transform.OfPoint(point)
                except Exception:
                    pass

                points.append(point.Z)

    if not points:
        return None, None

    return min(points), max(points)


def median(values):
    ordered = sorted(values)
    count = len(ordered)

    if not count:
        return None

    middle = count // 2

    if count % 2:
        return ordered[middle]

    return (ordered[middle - 1] + ordered[middle]) / 2.0


def selection_references_mm(elements):
    anchors = []
    lows = []
    highs = []

    for element in elements:
        anchor = element_anchor_z(element)

        if anchor is not None:
            anchors.append(anchor)

        z_min, z_max = element_z_range(element)

        if z_min is not None:
            lows.append(z_min)

        if z_max is not None:
            highs.append(z_max)

    anchor_ft = median(anchors)
    bottom_ft = min(lows) if lows else anchor_ft
    top_ft = max(highs) if highs else anchor_ft

    return {
        pp_element_move_window.REF_ANCHOR:
            anchor_ft / MM_TO_FT if anchor_ft is not None else None,
        pp_element_move_window.REF_BOTTOM:
            bottom_ft / MM_TO_FT if bottom_ft is not None else None,
        pp_element_move_window.REF_TOP:
            top_ft / MM_TO_FT if top_ft is not None else None,
    }


def format_level_elevation(value_mm):
    if abs(value_mm) < 0.5:
        return u"±0.000"

    sign = u"+" if value_mm > 0 else u"-"
    return u"{}{:.3f}".format(sign, abs(value_mm) / 1000.0)


def collect_levels():
    levels = []
    collector = FilteredElementCollector(doc).OfClass(Level).WhereElementIsNotElementType()

    for level in collector:
        try:
            elevation_ft = level.Elevation
            name = level.Name
        except Exception:
            continue

        elevation_mm = elevation_ft / MM_TO_FT
        levels.append({
            u"key": name,
            u"elev_ft": elevation_ft,
            u"elev_mm": elevation_mm,
            u"label": u"{}    {}".format(name, format_level_elevation(elevation_mm)),
        })

    levels.sort(key=lambda item: item[u"elev_ft"])
    return levels


def nearest_level_key(levels, anchor_mm):
    if not levels:
        return None

    below = [level for level in levels if level[u"elev_mm"] <= anchor_mm + EPS_MM]

    if below:
        return below[-1][u"key"]

    return levels[0][u"key"]


def load_settings(levels, refs_mm):
    config = script.get_config(u"pp_element_move")

    try:
        mode = config.get_option(u"mode", pp_element_move_window.MODE_UP)
    except Exception:
        mode = pp_element_move_window.MODE_UP

    try:
        value_mm = float(config.get_option(u"value_mm", 500.0))
    except Exception:
        value_mm = 500.0

    try:
        elev_mm = float(config.get_option(u"elev_mm", 0.0))
    except Exception:
        elev_mm = 0.0

    try:
        level_key = config.get_option(u"level_key", None)
    except Exception:
        level_key = None

    level_keys = set(level[u"key"] for level in levels)

    if level_key not in level_keys:
        anchor_mm = refs_mm.get(pp_element_move_window.REF_ANCHOR) or 0.0
        level_key = nearest_level_key(levels, anchor_mm)

    try:
        ref_kind = config.get_option(
            u"ref_kind", pp_element_move_window.REF_ANCHOR)
    except Exception:
        ref_kind = pp_element_move_window.REF_ANCHOR

    return mode, value_mm, elev_mm, level_key, ref_kind


def save_settings(result):
    try:
        config = script.get_config(u"pp_element_move")
        config.mode = result[u"mode"]

        if result[u"value_mm"] is not None:
            config.value_mm = result[u"value_mm"]

        if result[u"elev_mm"] is not None:
            config.elev_mm = result[u"elev_mm"]

        if result[u"level_key"] is not None:
            config.level_key = result[u"level_key"]

        config.ref_kind = result[u"ref_kind"]
        script.save_config()
    except Exception:
        pass


def find_level(levels, level_key):
    for level in levels:
        if level[u"key"] == level_key:
            return level

    return None


def calculate_move_mm(settings, levels, refs_mm):
    mode = settings[u"mode"]

    if mode == pp_element_move_window.MODE_DOWN:
        return -float(settings[u"value_mm"])

    if mode == pp_element_move_window.MODE_UP:
        return float(settings[u"value_mm"])

    level = find_level(levels, settings[u"level_key"])

    if level is None:
        return None

    current_mm = refs_mm.get(settings[u"ref_kind"])

    if current_mm is None:
        return None

    target_mm = float(level[u"elev_mm"]) + float(settings[u"elev_mm"])
    return target_mm - float(current_mm)


def to_id_list(elements):
    ids = List[ElementId]()

    for element in elements:
        ids.Add(element.Id)

    return ids


def move_elements(elements, move_mm):
    transaction = Transaction(doc, u"PP: Опуск / Подъём элементов")
    preprocessor = MoveFailurePreprocessor()

    try:
        transaction.Start()

        options = transaction.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(preprocessor)
        options.SetClearAfterRollback(True)
        transaction.SetFailureHandlingOptions(options)

        ids = to_id_list(elements)
        ElementTransformUtils.MoveElements(
            doc, ids, XYZ(0.0, 0.0, move_mm * MM_TO_FT))
        doc.Regenerate()

        status = transaction.Commit()

        if status != TransactionStatus.Committed:
            return False, u"Revit отменил перемещение.", preprocessor.warnings

        return True, None, preprocessor.warnings
    except Exception as ex:
        try:
            if transaction.GetStatus() == TransactionStatus.Started:
                transaction.RollBack()
        except Exception:
            pass

        return False, unicode(ex), preprocessor.warnings


def report_success(elements, move_mm, settings, external_ids, ignored, warnings):
    direction = u"Поднято" if move_mm > 0 else u"Опущено"
    lines = [
        u"Перемещено элементов: {}".format(len(elements)),
        u"{} на: {:.1f} мм".format(direction, abs(move_mm)),
    ]

    if settings[u"mode"] == pp_element_move_window.MODE_LEVEL:
        lines.append(u"На отметку поставлено: {}".format(settings[u"ref_kind"]))
        lines.append(u"Уровень: {}".format(settings[u"level_key"]))
        lines.append(u"Отметка от уровня: {:.1f} мм".format(settings[u"elev_mm"]))

    if ignored:
        lines.extend([
            u"",
            u"Не поддерживаемых элементов в исходном выделении пропущено: {}".format(ignored),
        ])

    if external_ids:
        lines.extend([
            u"",
            u"Внешних подключённых элементов: {}. Проверьте соседние участки.".format(
                len(external_ids)),
        ])

    if warnings:
        lines.extend([u"", u"Предупреждения Revit:"])
        lines.extend(u"• {}".format(text) for text in warnings[:10])

    pp_wpf.show_report(u"\n".join(lines), title=u"Готово", subtitle=TITLE)


def main():
    elements, ignored = pick_elements()

    if elements is None:
        return

    if not elements:
        show_error(u"Нечего перемещать", u"Не выбрано ни одного поддерживаемого элемента.")
        return

    pinned, grouped = invalid_locked_elements(elements)

    if pinned or grouped:
        lines = [u"Выборку нельзя переместить одной группой."]

        if pinned:
            lines.append(u"Закреплённых элементов: {}. Сначала снимите закрепление.".format(
                len(pinned)))

        if grouped:
            lines.append(u"Элементов внутри групп: {}. Сначала выйдите из группы или выберите другой набор.".format(
                len(grouped)))

        examples = pinned + grouped

        if examples:
            lines.append(u"")
            lines.append(u"Примеры:")
            lines.extend(u"• {}".format(element_label(element)) for element in examples[:8])

        show_error(u"Есть неподвижные элементы", u"\n".join(lines))
        return

    refs_mm = selection_references_mm(elements)

    if refs_mm.get(pp_element_move_window.REF_ANCHOR) is None:
        show_error(
            u"Не читается геометрия",
            u"Не удалось определить высоту выбранных элементов.")
        return

    levels = collect_levels()
    external_ids = external_neighbor_ids(elements)
    mode, value_mm, elev_mm, level_key, ref_kind = load_settings(levels, refs_mm)

    settings = pp_element_move_window.ask_settings(
        mode,
        value_mm,
        elev_mm,
        levels,
        level_key,
        ref_kind,
        refs_mm,
        len(elements),
        len(external_ids))

    if settings is None:
        return

    save_settings(settings)
    move_mm = calculate_move_mm(settings, levels, refs_mm)

    if move_mm is None:
        show_error(u"Не удалось рассчитать сдвиг", u"Проверьте уровень и отметку.")
        return

    if abs(move_mm) < EPS_MM:
        pp_wpf.show_report(
            u"Выбранная опорная отметка уже находится на заданной высоте.",
            title=u"Перемещение не требуется",
            subtitle=TITLE)
        return

    ok, error, warnings = move_elements(elements, move_mm)

    if not ok:
        lines = [
            u"Revit не смог переместить выбранную группу.",
            u"",
            error or u"Причина не указана.",
            u"",
            u"Проверьте закрепление, рабочие наборы, внешние соединения и зависимости элементов.",
        ]
        show_error(u"Перемещение отменено", u"\n".join(lines))
        return

    ids = to_id_list(elements)

    try:
        uidoc.Selection.SetElementIds(ids)
    except Exception:
        pass

    report_success(elements, move_mm, settings, external_ids, ignored, warnings)


main()

# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

u"""Разместить на листы: каждый отмеченный план — на свой новый лист.

Схема работы (см. Memory.md, «Оформление / Разместить на листы»):

1. Лист создаётся ПУСТЫМ (без основной надписи), на него копируется рамка
   с листа-образца — вместе со всеми параметрами экземпляра («Формат А»,
   «Кратность», ориентация...). Семейство рамки искать не нужно: готовый
   лист в проекте есть всегда.
2. План ставится на лист, и его реальный габарит на листе читается через
   Viewport.GetBoxOutline() — он уже учитывает подрезку, масштаб и подпись.
   Считать размер заранее из crop box ненадёжно.
3. Формат перебирается от А3 к А0 (потом удлинённые через «Кратность»,
   кратность 2 пропускается — в семействе это лист в двойном масштабе):
   параметр ставится, документ регенерируется, габарит рамки читается из
   BoundingBox. Первый формат, в чью рабочую область (рамка минус поля)
   план влез, — итоговый.
4. Видовой экран сдвигается в центр рабочей области.
"""

import os
import sys

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from System.Collections.Generic import List

from Autodesk.Revit.DB import (
    BuiltInCategory, CopyPasteOptions, ElementId, ElementTransformUtils,
    FilteredElementCollector, StorageType, Transaction, Transform, View,
    ViewSheet, ViewType, Viewport, XYZ
)

from pyrevit import forms

_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

import pp_wpf
import pp_sheet_picker
import pp_sheet_naming
import pp_place_window

from pp_settings import load_settings, save_settings


uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

TOOL_TITLE = u"Разместить на листы"
SETTINGS_KEY = "sheet_place_settings"

MM = 304.8  # футов в миллиметре: feet * MM = mm

PLAN_TYPES = (ViewType.FloorPlan, ViewType.CeilingPlan,
              ViewType.AreaPlan, ViewType.EngineeringPlan)

PARAM_FORMAT = u"Формат А"
PARAM_MULT = u"Кратность"
PARAM_PORTRAIT = u"Книжная ориентация"
PARAM_CUSTOM = u"Нестандартный размер"

# Стороны форматов по ГОСТ 2.301 (короткая, длинная), мм — только для
# порядка перебора; реальный размер рамки читается из модели.
FORMAT_SIDES = {
    4: (210.0, 297.0),
    3: (297.0, 420.0),
    2: (420.0, 594.0),
    1: (594.0, 841.0),
    0: (841.0, 1189.0),
}

# Удлинённые форматы ГОСТ 2.301 без кратности 2.
ELONGATED = {
    3: (3, 4, 5, 6, 7),
    2: (3, 4, 5),
    1: (3, 4),
    0: (3,),
}


class Stop(Exception):
    u"""Осмысленная остановка: сообщение уходит в окно отчёта."""
    pass


def fail(message):
    raise Stop(message)


# ── Сбор данных для окна ───────────────────────────────────────────────────────

def views_on_sheets():
    ids = set()

    for viewport in FilteredElementCollector(doc).OfClass(Viewport):
        try:
            ids.add(viewport.ViewId.IntegerValue)
        except Exception:
            pass

    return ids


def template_name(view):
    try:
        template_id = view.ViewTemplateId
        if template_id is None or template_id == ElementId.InvalidElementId:
            return u""
        template = doc.GetElement(template_id)
        return unicode(template.Name) if template is not None else u""
    except Exception:
        return u""


def collect_plans():
    u"""[(подпись, {view, template}), ...] — планы, ещё не лежащие на листах."""
    placed = views_on_sheets()
    rows = []

    for view in FilteredElementCollector(doc).OfClass(View):
        try:
            if view.IsTemplate or view.ViewType not in PLAN_TYPES:
                continue
        except Exception:
            continue

        if view.Id.IntegerValue in placed:
            continue

        try:
            name = unicode(view.Name)
        except Exception:
            continue

        rows.append((name, view))

    rows.sort(key=lambda row: pp_sheet_picker.natural_key(row[0]))

    labels = {}
    for name, _view in rows:
        labels[name] = labels.get(name, 0) + 1

    items = []
    for name, view in rows:
        label = name
        if labels[name] > 1:
            label = u"{0}   ·   id {1}".format(name, view.Id.IntegerValue)
        items.append((label, {u"view": view, u"template": template_name(view)}))

    return items


def sheet_name_for(view):
    try:
        proposed, _warning = pp_sheet_naming.propose([view.Name])
    except Exception:
        proposed = u""

    return proposed or unicode(view.Name)


def load_defaults(plans, sheets):
    settings = load_settings()
    stored = settings.get(SETTINGS_KEY) or {}

    margins = stored.get(u"margins") or [20.0, 5.0, 5.0, 60.0]

    defaults = {
        u"margins": tuple(float(v) for v in margins),
        u"suffix": stored.get(u"suffix", u"ОВ"),
        u"portrait": bool(stored.get(u"portrait", True)),
        u"sample_id": None,
        u"current": None,
        u"preselect": [],
    }

    active = doc.ActiveView

    if isinstance(active, ViewSheet):
        defaults[u"sample_id"] = active.Id
    else:
        for label, item in plans:
            if item[u"view"].Id == active.Id:
                defaults[u"current"] = label
                defaults[u"preselect"] = [label]
                break

        # Образец по умолчанию — последний лист прошлого запуска, если жив
        stored_id = stored.get(u"sample_id")
        if stored_id:
            for row in sheets:
                if row[u"sheet"].Id.IntegerValue == stored_id:
                    defaults[u"sample_id"] = row[u"sheet"].Id
                    break

    return settings, defaults


def store_defaults(settings, options):
    settings[SETTINGS_KEY] = {
        u"margins": list(options[u"margins"]),
        u"suffix": options[u"suffix"],
        u"portrait": options[u"portrait"],
        u"sample_id": options[u"sample"].Id.IntegerValue,
    }

    try:
        save_settings(settings)
    except Exception:
        pass


# ── Рамка: параметры и габарит ─────────────────────────────────────────────────

def title_blocks_on(sheet):
    return list(
        FilteredElementCollector(doc, sheet.Id)
        .OfCategory(BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsNotElementType()
    )


def set_int(element, name, value):
    parameter = element.LookupParameter(name)

    if parameter is None or parameter.IsReadOnly:
        return False

    try:
        if parameter.StorageType == StorageType.Integer:
            parameter.Set(int(value))
        elif parameter.StorageType == StorageType.Double:
            parameter.Set(float(value))
        else:
            return False
        return True
    except Exception:
        return False


def outline_mm(outline):
    lo = outline.MinimumPoint
    hi = outline.MaximumPoint
    return lo.X * MM, lo.Y * MM, hi.X * MM, hi.Y * MM


def bbox_mm(element, view):
    box = element.get_BoundingBox(view)

    if box is None:
        raise Stop(u"Не удалось прочитать габарит рамки на листе.")

    return box.Min.X * MM, box.Min.Y * MM, box.Max.X * MM, box.Max.Y * MM


def format_candidates(portrait_too):
    u"""[(формат, кратность, книжная), ...] в порядке возрастания площади."""
    orientations = (0, 1) if portrait_too else (0,)
    candidates = []

    for fmt in (3, 2, 1, 0):
        for portrait in orientations:
            candidates.append((fmt, 1, portrait))

    elongated = []
    for fmt, mults in ELONGATED.items():
        short, long_ = FORMAT_SIDES[fmt]
        for mult in mults:
            elongated.append((short * long_ * mult, fmt, mult))

    elongated.sort()

    for _area, fmt, mult in elongated:
        for portrait in orientations:
            candidates.append((fmt, mult, portrait))

    return candidates


def describe_format(fmt, mult, portrait):
    text = u"А{0}".format(fmt)

    if mult > 1:
        text = u"{0}×{1}".format(text, mult)

    return u"{0} {1}".format(text, u"книжный" if portrait else u"альбомный")


def apply_format(title_block, fmt, mult, portrait):
    if not set_int(title_block, PARAM_FORMAT, fmt):
        raise Stop(
            u"В основной надписи нет параметра «{0}» — подбор формата "
            u"невозможен.".format(PARAM_FORMAT))

    set_int(title_block, PARAM_MULT, mult)
    set_int(title_block, PARAM_PORTRAIT, 1 if portrait else 0)
    set_int(title_block, PARAM_CUSTOM, 0)

    doc.Regenerate()


def work_area(title_block, sheet, margins):
    u"""Рабочая область (x0, y0, x1, y1) в мм: рамка минус поля."""
    left, right, top, bottom = margins
    x0, y0, x1, y1 = bbox_mm(title_block, sheet)

    return x0 + left, y0 + bottom, x1 - right, y1 - top


# ── Создание листа ─────────────────────────────────────────────────────────────

def copy_sheet_params(source, target):
    u"""Общие и проектные параметры листа — с образца. Встроенные не трогаем."""
    for parameter in source.Parameters:
        try:
            if parameter.Id.IntegerValue < 0 or parameter.IsReadOnly:
                continue

            name = parameter.Definition.Name
            dest = target.LookupParameter(name)

            if dest is None or dest.IsReadOnly:
                continue

            storage = parameter.StorageType

            if storage == StorageType.String:
                value = parameter.AsString()
                if value:
                    dest.Set(value)
            elif storage == StorageType.Integer:
                dest.Set(parameter.AsInteger())
            elif storage == StorageType.Double:
                dest.Set(parameter.AsDouble())
            elif storage == StorageType.ElementId:
                dest.Set(parameter.AsElementId())
        except Exception:
            continue


def set_section(sheet, title_block, name, value):
    u"""Раздел проекта: параметр может сидеть на листе или на рамке."""
    if not name or not value:
        return

    for element in (sheet, title_block):
        if element is None:
            continue

        try:
            parameter = element.LookupParameter(name)
            if parameter is not None and not parameter.IsReadOnly \
                    and parameter.StorageType == StorageType.String:
                parameter.Set(value)
        except Exception:
            pass


def set_sheet_name(sheet, name):
    base = (name or u"").strip() or u"Лист"

    try:
        sheet.Name = base
        return base
    except Exception:
        pass

    for index in range(2, 1000):
        candidate = u"{0} ({1})".format(base, index)
        try:
            sheet.Name = candidate
            return candidate
        except Exception:
            continue

    return sheet.Name


def sample_viewport_type(sample):
    try:
        for viewport_id in sample.GetAllViewports():
            viewport = doc.GetElement(viewport_id)
            if viewport is not None:
                return viewport.GetTypeId()
    except Exception:
        pass

    return None


def create_sheet(view, number, options, section_param, viewport_type,
                 candidates):
    u"""Один лист под один план. Возврат: (лист, описание формата, замечание)."""
    sample = options[u"sample"]
    margins = options[u"margins"]

    sheet = ViewSheet.Create(doc, ElementId.InvalidElementId)

    # Рамка с образца — со всеми параметрами экземпляра
    source_blocks = title_blocks_on(sample)
    if not source_blocks:
        raise Stop(u"На листе-образце нет основной надписи.")

    ids = List[ElementId]([block.Id for block in source_blocks[:1]])
    ElementTransformUtils.CopyElements(
        sample, ids, sheet, Transform.Identity, CopyPasteOptions())

    doc.Regenerate()

    blocks = title_blocks_on(sheet)
    if not blocks:
        raise Stop(u"Не удалось скопировать основную надпись с образца.")
    title_block = blocks[0]

    copy_sheet_params(sample, sheet)
    set_section(sheet, title_block, section_param, options[u"section"])

    sheet.SheetNumber = number
    set_sheet_name(sheet, sheet_name_for(view))

    # План — на лист, габарит — по факту
    if not Viewport.CanAddViewToSheet(doc, sheet.Id, view.Id):
        raise Stop(u"План «{0}» нельзя разместить на лист (уже размещён "
                   u"или не подходит по типу).".format(view.Name))

    viewport = Viewport.Create(doc, sheet.Id, view.Id, XYZ.Zero)

    if viewport_type is not None:
        try:
            viewport.ChangeTypeId(viewport_type)
        except Exception:
            pass

    doc.Regenerate()

    vx0, vy0, vx1, vy1 = outline_mm(viewport.GetBoxOutline())
    need_w = vx1 - vx0
    need_h = vy1 - vy0

    # Подбор формата: первый, куда влезло
    chosen = None
    warning = u""

    for fmt, mult, portrait in candidates:
        apply_format(title_block, fmt, mult, portrait)
        ax0, ay0, ax1, ay1 = work_area(title_block, sheet, margins)

        if need_w <= ax1 - ax0 and need_h <= ay1 - ay0:
            chosen = (fmt, mult, portrait)
            break

    if chosen is None:
        # Самый большой альбомный из перебранных — хоть куда-то положить
        chosen = [item for item in candidates if not item[2]][-1]
        apply_format(title_block, *chosen)
        warning = (u"план {0:.0f}×{1:.0f} мм не поместился ни на один "
                   u"формат — оставлен {2}".format(
                       need_w, need_h, describe_format(*chosen)))

    # Центрирование в рабочей области — сдвигом на дельту центров
    ax0, ay0, ax1, ay1 = work_area(title_block, sheet, margins)
    vx0, vy0, vx1, vy1 = outline_mm(viewport.GetBoxOutline())

    dx = ((ax0 + ax1) - (vx0 + vx1)) / 2.0
    dy = ((ay0 + ay1) - (vy0 + vy1)) / 2.0

    ElementTransformUtils.MoveElement(
        doc, viewport.Id, XYZ(dx / MM, dy / MM, 0.0))

    return sheet, describe_format(*chosen), warning


# ── Запуск ─────────────────────────────────────────────────────────────────────

def run():
    plans = collect_plans()

    if not plans:
        fail(u"В проекте нет планов, которые ещё не размещены на листах.")

    section_param = pp_sheet_picker.get_section_param_name()
    sheets = pp_sheet_picker.collect_rows(doc, section_param)

    if not sheets:
        fail(u"В проекте нет ни одного листа — не с чего скопировать "
             u"основную надпись. Создайте первый лист вручную.")

    taken = set(row[u"number"] for row in sheets)

    settings, defaults = load_defaults(plans, sheets)

    options = pp_place_window.ask(
        _HERE, plans, sheets, taken, sheet_name_for, defaults)

    if not options:
        return

    store_defaults(settings, options)

    views = options[u"views"]
    numbers = pp_place_window.allocate_numbers(
        options[u"start"], options[u"suffix"], taken, len(views))

    viewport_type = sample_viewport_type(options[u"sample"])
    candidates = format_candidates(options[u"portrait"])

    created = []
    warnings = []

    transaction = Transaction(doc, TOOL_TITLE)
    transaction.Start()

    try:
        with forms.ProgressBar(title=TOOL_TITLE + u" — {value} из {max_value}",
                               cancellable=False) as bar:
            for index, (view, number) in enumerate(zip(views, numbers)):
                bar.update_progress(index, len(views))

                sheet, fmt_text, warning = create_sheet(
                    view, number, options, section_param, viewport_type,
                    candidates)

                created.append((sheet, fmt_text))

                if warning:
                    warnings.append(u"{0}: {1}".format(number, warning))

            bar.update_progress(len(views), len(views))

        transaction.Commit()
    except Exception:
        transaction.RollBack()
        raise

    lines = [u"Создано {0} {1}.".format(
        len(created),
        pp_place_window.plural(len(created), (u"лист", u"листа", u"листов")))]
    lines.append(u"")

    for sheet, fmt_text in created:
        lines.append(u"{0} — {1} — {2}".format(
            sheet.SheetNumber, sheet.Name, fmt_text))

    if warnings:
        lines.append(u"")
        lines.append(u"Проверьте:")
        lines.extend(u"• " + line for line in warnings)

    pp_wpf.show_report(
        u"\n".join(lines),
        title=u"Готово" if not warnings else u"Готово, есть замечания",
        subtitle=TOOL_TITLE
    )

    if created:
        try:
            uidoc.ActiveView = created[0][0]
        except Exception:
            pass


try:
    run()

except Stop as ex:
    pp_wpf.show_report(
        unicode(ex), title=u"Не выполнено", subtitle=TOOL_TITLE, is_error=True)

except Exception as ex:
    pp_wpf.show_report(
        unicode(ex), title=u"Ошибка", subtitle=TOOL_TITLE, is_error=True)

# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

u"""Разместить на листы — два режима.

«Планы»: каждый отмеченный план — на свой новый лист, по центру.
«Виды»: 3D-виды, разрезы, фасады, чертёжные виды — все на один лист рядами;
не поместились даже в самый большой формат — остаток переносится на
следующий лист (с предупреждением в отчёте).

Общая схема (см. Memory.md, «Оформление / Разместить на листы»):

1. Лист создаётся ПУСТЫМ (без основной надписи), на него копируется рамка
   с листа-образца вместе со всеми параметрами экземпляра («Формат А»,
   «Кратность», ориентация...). Семейство рамки искать не нужно.
2. Виды ставятся на лист, и их реальный габарит читается по факту:
   Viewport.GetBoxOutline() (сам вид) плюс GetLabelOutline() (подпись —
   в GetBoxOutline она не входит). Считать размер из crop box ненадёжно.
3. Формат перебирается от А3 к А0, потом удлинённые через «Кратность»
   (кратность 2 пропускается — в семействе это лист в двойном масштабе):
   параметр ставится, документ регенерируется, габарит рамки читается из
   BoundingBox. Первый формат, где всё влезло в рабочую область (рамка минус
   поля), — итоговый.
4. Виды сдвигаются на свои места MoveElement-ом на разность прямоугольников.
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
import pp_place_layout
import pp_place_window

from pp_settings import load_settings, save_settings


uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

TOOL_TITLE = u"Разместить на листы"
SETTINGS_KEY = "sheet_place_settings"

MODE_PLANS = pp_place_window.MODE_PLANS
MODE_VIEWS = pp_place_window.MODE_VIEWS

MM = 304.8  # футов в миллиметре: feet * MM = mm

PLAN_TYPES = (ViewType.FloorPlan, ViewType.CeilingPlan,
              ViewType.AreaPlan, ViewType.EngineeringPlan)

# Режим «Виды». Спецификации и легенды сюда не входят: спецификация на листе —
# не видовой экран, а легенда может стоять на многих листах сразу.
OTHER_VIEW_TYPES = (ViewType.ThreeD, ViewType.Section, ViewType.Elevation,
                    ViewType.Detail, ViewType.DraftingView)

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

# Лист, на который переносятся виды, когда все не влезли никуда: самый
# большой СТАНДАРТНЫЙ формат. Удлинённый лист частями выглядел бы странно.
OVERFLOW_FORMAT = (0, 1, 0)

NO_CROP = u"нет подрезки"


class Stop(Exception):
    u"""Осмысленная остановка: сообщение уходит в окно отчёта."""
    pass


def fail(message):
    raise Stop(message)


def plural(count, forms_):
    return pp_place_window.plural(count, forms_)


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


def view_warning(view):
    u"""Замечание к виду в списке. 3D без подрезки на листе — размером с модель."""
    try:
        if view.ViewType == ViewType.ThreeD and not view.CropBoxActive:
            return NO_CROP
    except Exception:
        pass

    return u""


def collect_views(types, sort_key):
    u"""[(подпись, {view, template, warn}), ...] — виды, ещё не лежащие на листах."""
    placed = views_on_sheets()
    rows = []

    for view in FilteredElementCollector(doc).OfClass(View):
        try:
            if view.IsTemplate or view.ViewType not in types:
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

    rows.sort(key=lambda row: sort_key(row[0]))

    counts = {}
    for name, _view in rows:
        counts[name] = counts.get(name, 0) + 1

    items = []
    for name, view in rows:
        label = name

        if counts[name] > 1:
            label = u"{0}   ·   id {1}".format(name, view.Id.IntegerValue)

        warn = view_warning(view)
        if warn:
            label = u"{0}   ·   {1}".format(label, warn)

        items.append((label, {
            u"view": view,
            u"template": template_name(view),
            u"warn": warn,
        }))

    return items


def plan_sheet_name(view):
    try:
        proposed, _warning = pp_sheet_naming.propose([view.Name])
    except Exception:
        proposed = u""

    return proposed or unicode(view.Name)


def views_sheet_name(prefix, views):
    try:
        proposed, _warning = pp_sheet_naming.propose_views(
            prefix, [view.Name for view in views])
    except Exception:
        proposed = u""

    return proposed or (prefix or u"").strip() or unicode(views[0].Name)


def name_for(mode, views, prefix):
    u"""Имя листа для предпросмотра в окне."""
    if not views:
        return u""

    if mode == MODE_PLANS:
        return plan_sheet_name(views[0])

    return views_sheet_name(prefix, views)


def load_defaults(lists, sheets):
    settings = load_settings()
    stored = settings.get(SETTINGS_KEY) or {}

    margins = stored.get(u"margins") or [20.0, 5.0, 5.0, 60.0]

    defaults = {
        u"margins": tuple(float(v) for v in margins),
        u"suffix": stored.get(u"suffix", u"ОВ"),
        u"portrait": bool(stored.get(u"portrait", True)),
        u"gap": float(stored.get(u"gap", 10.0)),
        u"prefix": stored.get(u"prefix"),
        u"mode": stored.get(u"mode", MODE_PLANS),
        u"sample_id": None,
        u"current": {},
    }

    active = doc.ActiveView

    if isinstance(active, ViewSheet):
        defaults[u"sample_id"] = active.Id
    else:
        # Активный вид сразу отмечен и открывает свой режим
        for mode, items in lists.items():
            for label, item in items:
                if item[u"view"].Id == active.Id:
                    defaults[u"current"][mode] = label
                    defaults[u"mode"] = mode
                    break

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
        u"gap": options[u"gap"],
        u"prefix": options[u"prefix"],
        u"mode": options[u"mode"],
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


def largest_landscape(candidates):
    return [item for item in candidates if not item[2]][-1]


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


# ── Видовой экран: габарит с подписью и перенос ────────────────────────────────

def viewport_rect(viewport):
    u"""Прямоугольник вида на листе в мм — вместе с подписью.

    GetBoxOutline() подпись не включает; GetLabelOutline() появился в
    Revit 2022. Если подписи нет (скрыта типом экрана), её прямоугольник
    вырожден — такой не учитываем.
    """
    x0, y0, x1, y1 = outline_mm(viewport.GetBoxOutline())

    try:
        lx0, ly0, lx1, ly1 = outline_mm(viewport.GetLabelOutline())

        if lx1 - lx0 > 0.1 and ly1 - ly0 > 0.1:
            x0 = min(x0, lx0)
            y0 = min(y0, ly0)
            x1 = max(x1, lx1)
            y1 = max(y1, ly1)
    except Exception:
        pass

    return x0, y0, x1, y1


def move_viewport(viewport, rect, target_x0, target_y0):
    dx = target_x0 - rect[0]
    dy = target_y0 - rect[1]

    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return

    ElementTransformUtils.MoveElement(
        doc, viewport.Id, XYZ(dx / MM, dy / MM, 0.0))


def add_viewport(sheet, view, viewport_type):
    viewport = Viewport.Create(doc, sheet.Id, view.Id, XYZ.Zero)

    if viewport_type is not None:
        try:
            viewport.ChangeTypeId(viewport_type)
        except Exception:
            pass

    return viewport


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


class Run(object):
    u"""Общее состояние одного запуска: образец, нумерация, итоги."""

    def __init__(self, options, section_param, taken):
        self.options = options
        self.section_param = section_param
        self.taken = set(taken)
        self.next_value = options[u"start"]
        self.viewport_type = sample_viewport_type(options[u"sample"])
        self.candidates = format_candidates(options[u"portrait"])
        self.created = []    # [(лист, формат, число видов)]
        self.warnings = []
        self.done = set()      # id видов, легших на листы
        self.skipped = set()   # id видов, которые Revit не даёт ставить на лист

        self.source_blocks = title_blocks_on(options[u"sample"])
        if not self.source_blocks:
            raise Stop(u"На листе-образце нет основной надписи.")

    def next_number(self):
        u"""Следующий свободный номер «N<суффикс>» — занятые пропускаются."""
        suffix = self.options[u"suffix"]
        number = pp_place_window.allocate_numbers(
            self.next_value, suffix, self.taken, 1)[0]

        self.taken.add(number)
        self.next_value = int(number[:len(number) - len(suffix)]) + 1

        return number

    def new_sheet(self):
        u"""Пустой лист + рамка и параметры образца + раздел + номер."""
        sample = self.options[u"sample"]
        sheet = ViewSheet.Create(doc, ElementId.InvalidElementId)

        ids = List[ElementId]([self.source_blocks[0].Id])
        ElementTransformUtils.CopyElements(
            sample, ids, sheet, Transform.Identity, CopyPasteOptions())

        doc.Regenerate()

        blocks = title_blocks_on(sheet)
        if not blocks:
            raise Stop(u"Не удалось скопировать основную надпись с образца.")
        title_block = blocks[0]

        copy_sheet_params(sample, sheet)
        set_section(sheet, title_block, self.section_param,
                    self.options[u"section"])

        sheet.SheetNumber = self.next_number()

        return sheet, title_block


# ── Режим «Планы»: один план — один лист ───────────────────────────────────────

def place_plan(run, view):
    margins = run.options[u"margins"]
    sheet, title_block = run.new_sheet()
    set_sheet_name(sheet, plan_sheet_name(view))

    if not Viewport.CanAddViewToSheet(doc, sheet.Id, view.Id):
        raise Stop(u"План «{0}» нельзя разместить на лист (уже размещён "
                   u"или не подходит по типу).".format(view.Name))

    viewport = add_viewport(sheet, view, run.viewport_type)
    doc.Regenerate()

    rect = viewport_rect(viewport)
    need_w = rect[2] - rect[0]
    need_h = rect[3] - rect[1]

    chosen = None

    for candidate in run.candidates:
        apply_format(title_block, *candidate)
        ax0, ay0, ax1, ay1 = work_area(title_block, sheet, margins)

        if need_w <= ax1 - ax0 and need_h <= ay1 - ay0:
            chosen = candidate
            break

    if chosen is None:
        chosen = largest_landscape(run.candidates)
        apply_format(title_block, *chosen)
        run.warnings.append(
            u"{0}: план {1:.0f}×{2:.0f} мм не поместился ни на один формат — "
            u"оставлен {3}".format(sheet.SheetNumber, need_w, need_h,
                                   describe_format(*chosen)))

    ax0, ay0, ax1, ay1 = work_area(title_block, sheet, margins)
    move_viewport(viewport, rect,
                  (ax0 + ax1 - need_w) / 2.0,
                  (ay0 + ay1 - need_h) / 2.0)

    run.created.append((sheet, describe_format(*chosen), 1))


# ── Режим «Виды»: все на один лист, остаток — на следующий ─────────────────────

def place_views_sheet(run, views):
    u"""Один лист под первые виды из списка.

    Возврат: сколько видов из начала списка обработано — легли на лист или
    пропущены, потому что Revit не даёт поставить их на лист.
    """
    margins = run.options[u"margins"]
    gap = run.options[u"gap"]
    sheet, title_block = run.new_sheet()

    usable = []
    for view in views:
        if Viewport.CanAddViewToSheet(doc, sheet.Id, view.Id):
            usable.append(view)
        else:
            run.warnings.append(
                u"«{0}»: Revit не даёт разместить вид на лист — пропущен".format(
                    view.Name))
            run.skipped.add(view.Id.IntegerValue)

    if not usable:
        doc.Delete(sheet.Id)
        return 0

    views = usable

    viewports = []
    for view in views:
        viewports.append(add_viewport(sheet, view, run.viewport_type))

    doc.Regenerate()

    rects = [viewport_rect(viewport) for viewport in viewports]
    sizes = [(r[2] - r[0], r[3] - r[1]) for r in rects]

    chosen = None

    for candidate in run.candidates:
        apply_format(title_block, *candidate)
        area = work_area(title_block, sheet, margins)
        rows, placed = pp_place_layout.pack_rows(
            sizes, area[2] - area[0], area[3] - area[1], gap)

        if placed == len(views):
            chosen = (candidate, area, rows, placed)
            break

    if chosen is None:
        # Все не влезли никуда — заполняем стандартный А0, остальное дальше
        candidate = OVERFLOW_FORMAT
        apply_format(title_block, *candidate)
        area = work_area(title_block, sheet, margins)
        rows, placed = pp_place_layout.pack_rows(
            sizes, area[2] - area[0], area[3] - area[1], gap)

        if placed == 0:
            # Первый вид больше А0 — пробуем удлинённые, иначе кладём как есть
            w, h = sizes[0]
            candidate = None

            for item in run.candidates:
                apply_format(title_block, *item)
                area = work_area(title_block, sheet, margins)
                if w <= area[2] - area[0] and h <= area[3] - area[1]:
                    candidate = item
                    break

            if candidate is None:
                candidate = largest_landscape(run.candidates)
                apply_format(title_block, *candidate)
                area = work_area(title_block, sheet, margins)
                run.warnings.append(
                    u"{0}: вид «{1}» ({2:.0f}×{3:.0f} мм) не поместился ни на "
                    u"один формат — оставлен {4}".format(
                        sheet.SheetNumber, views[0].Name, w, h,
                        describe_format(*candidate)))

            rows, placed = pp_place_layout.single_row(sizes), 1

        chosen = (candidate, area, rows, placed)

    candidate, area, rows, placed = chosen

    # Не поместившиеся виды снимаем с этого листа — они уйдут на следующий
    for viewport in viewports[placed:]:
        doc.Delete(viewport.Id)

    positions = pp_place_layout.place_rows(rows, sizes, area, gap)

    for index in range(placed):
        x0, y0 = positions[index]
        move_viewport(viewports[index], rects[index], x0, y0)

    set_sheet_name(sheet, views_sheet_name(run.options[u"prefix"],
                                           views[:placed]))

    run.created.append((sheet, describe_format(*candidate), placed))

    for view in views[:placed]:
        run.done.add(view.Id.IntegerValue)

    return placed


def place_views(run, views, bar):
    remaining = list(views)
    total = len(views)

    # Виды без подрезки — предупреждаем, но не трогаем: подрезку решает человек
    for view in remaining:
        if view_warning(view):
            run.warnings.append(
                u"«{0}»: {1} — на листе вид размером со всю модель".format(
                    view.Name, NO_CROP))

    while remaining:
        bar.update_progress(total - len(remaining), total)

        placed = place_views_sheet(run, remaining)

        remaining = [view for view in remaining
                     if view.Id.IntegerValue not in run.done
                     and view.Id.IntegerValue not in run.skipped]
        left = len(remaining)

        if placed and left:
            sheet = run.created[-1][0]
            run.warnings.append(
                u"{0}: все виды не поместились даже на самый большой формат — "
                u"{1} {2} перенесено на следующий лист".format(
                    sheet.SheetNumber, left,
                    plural(left, (u"вид", u"вида", u"видов"))))

    bar.update_progress(total, total)


# ── Запуск ─────────────────────────────────────────────────────────────────────

def run_tool():
    lists = {
        MODE_PLANS: collect_views(PLAN_TYPES, pp_sheet_picker.natural_key),
        MODE_VIEWS: collect_views(OTHER_VIEW_TYPES,
                                  pp_sheet_naming.view_order_key),
    }

    if not lists[MODE_PLANS] and not lists[MODE_VIEWS]:
        fail(u"В проекте нет планов и видов, которые ещё не размещены на листах.")

    section_param = pp_sheet_picker.get_section_param_name()
    sheets = pp_sheet_picker.collect_rows(doc, section_param)

    if not sheets:
        fail(u"В проекте нет ни одного листа — не с чего скопировать "
             u"основную надпись. Создайте первый лист вручную.")

    taken = set(row[u"number"] for row in sheets)

    settings, defaults = load_defaults(lists, sheets)

    options = pp_place_window.ask(
        _HERE, lists, sheets, taken, name_for,
        pp_sheet_naming.get_prefix_options(), defaults)

    if not options:
        return

    store_defaults(settings, options)

    run = Run(options, section_param, taken)
    views = options[u"views"]

    transaction = Transaction(doc, TOOL_TITLE)
    transaction.Start()

    try:
        with forms.ProgressBar(title=TOOL_TITLE + u" — {value} из {max_value}",
                               cancellable=False) as bar:
            if options[u"mode"] == MODE_PLANS:
                for index, view in enumerate(views):
                    bar.update_progress(index, len(views))
                    place_plan(run, view)
                bar.update_progress(len(views), len(views))
            else:
                place_views(run, views, bar)

        transaction.Commit()
    except Exception:
        transaction.RollBack()
        raise

    report(run)

    if run.created:
        try:
            uidoc.ActiveView = run.created[0][0]
        except Exception:
            pass


def report(run):
    count = len(run.created)
    lines = [u"Создано {0} {1}.".format(
        count, plural(count, (u"лист", u"листа", u"листов")))]

    if run.options[u"mode"] == MODE_VIEWS and count > 1:
        lines.append(u"")
        lines.append(u"ВНИМАНИЕ: виды не поместились на один лист и разнесены "
                     u"на {0} {1}.".format(
                         count, plural(count, (u"лист", u"листа", u"листов"))))

    lines.append(u"")

    for sheet, fmt_text, placed in run.created:
        line = u"{0} — {1} — {2}".format(sheet.SheetNumber, sheet.Name, fmt_text)

        if run.options[u"mode"] == MODE_VIEWS:
            line = u"{0} — {1} {2}".format(
                line, placed, plural(placed, (u"вид", u"вида", u"видов")))

        lines.append(line)

    if run.warnings:
        lines.append(u"")
        lines.append(u"Проверьте:")
        lines.extend(u"• " + line for line in run.warnings)

    pp_wpf.show_report(
        u"\n".join(lines),
        title=u"Готово" if not run.warnings else u"Готово, есть замечания",
        subtitle=TOOL_TITLE,
        width=620
    )


try:
    run_tool()

except Stop as ex:
    pp_wpf.show_report(
        unicode(ex), title=u"Не выполнено", subtitle=TOOL_TITLE, is_error=True)

except Exception as ex:
    pp_wpf.show_report(
        unicode(ex), title=u"Ошибка", subtitle=TOOL_TITLE, is_error=True)

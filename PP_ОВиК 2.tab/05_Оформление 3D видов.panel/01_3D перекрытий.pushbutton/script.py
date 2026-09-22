# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import os
import sys
import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit.DB import (
    FilteredElementCollector, Level, View3D,
    ViewDuplicateOption, Transaction, XYZ
)

import pp_wpf
import pp_params_dialog


doc = __revit__.ActiveUIDocument.Document

MM_TO_FT = 1.0 / 304.8
TOOL_TITLE = u"3D перекрытий"


def fail(message):
    u"""Скрипт плоский, общего try нет: показываем окно и выходим на месте."""
    pp_wpf.show_report(
        message,
        title=u"Не выполнено",
        subtitle=TOOL_TITLE,
        is_error=True
    )
    sys.exit(0)


def fmt(value):
    try:
        if float(value) == int(float(value)):
            return unicode(int(float(value)))
    except Exception:
        pass

    return unicode(value)


DIAGRAM = u"""
<Canvas xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        Width="300" Height="86">
  <Rectangle Canvas.Left="40" Canvas.Top="22" Width="220" Height="42"
             RadiusX="2" RadiusY="2" Fill="#1A0E6E8C"/>
  <Path Data="M 20,43 L 280,43" Stroke="#0E6E8C" StrokeThickness="2.5"/>
  <Path Data="M 40,22 L 260,22" Stroke="#6B7C85" StrokeThickness="1.2" StrokeDashArray="4 3"/>
  <Path Data="M 40,64 L 260,64" Stroke="#6B7C85" StrokeThickness="1.2" StrokeDashArray="4 3"/>
  <TextBlock Canvas.Left="20" Canvas.Top="26" FontSize="10.5" Foreground="#6B7C85" Text="сверху"/>
  <TextBlock Canvas.Left="20" Canvas.Top="66" FontSize="10.5" Foreground="#6B7C85" Text="снизу"/>
  <TextBlock Canvas.Left="212" Canvas.Top="45" FontSize="10.5" Foreground="#16232B" Text="отметка уровня"/>
</Canvas>
"""


def ask_offsets():
    return pp_params_dialog.ask({
        u"title": TOOL_TITLE,
        u"subtitle": u"Копия активного 3D вида на каждый уровень: секущий бокс режет полосу вокруг отметки.",
        u"diagram": DIAGRAM,
        u"fields": [
            {
                u"key": u"bottom",
                u"label": u"ОТСТУП СНИЗУ ОТ ОТМЕТКИ УРОВНЯ",
                u"short": u"снизу",
                u"hint": u"На сколько рез уходит ниже отметки уровня.",
                u"unit": u"мм",
                u"value": 600,
                u"min": 0,
            },
            {
                u"key": u"top",
                u"label": u"ОТСТУП СВЕРХУ ОТ ОТМЕТКИ УРОВНЯ",
                u"short": u"сверху",
                u"hint": u"На сколько рез уходит выше отметки уровня.",
                u"unit": u"мм",
                u"value": 300,
                u"min": 0,
            },
        ],
        u"run_label": u"Создать виды",
    })


active_view = doc.ActiveView

if not isinstance(active_view, View3D):
    fail(
        u"Активный вид должен быть 3D видом."
    )

# ─────────────────────────────────────────────
# ДИАЛОГ
# ─────────────────────────────────────────────

values = ask_offsets()

if values is None:
    sys.exit(0)

bottom_offset_mm = values[u"bottom"]
top_offset_mm = values[u"top"]
bottom_offset = bottom_offset_mm * MM_TO_FT
top_offset = top_offset_mm * MM_TO_FT

# ─────────────────────────────────────────────
# СБОР УРОВНЕЙ
# ─────────────────────────────────────────────

levels = list(
    FilteredElementCollector(doc)
    .OfClass(Level)
)
levels.sort(key=lambda x: x.Elevation)

if not levels:
    fail(
        u"В проекте не найдено ни одного уровня."
    )

# ─────────────────────────────────────────────
# ОСНОВНАЯ ЛОГИКА
# ─────────────────────────────────────────────

created_views = []
errors = []

t = Transaction(doc, u"PP: 3D перекрытий — нарезка по уровням")

try:
    t.Start()

    if not active_view.IsSectionBoxActive:
        active_view.IsSectionBoxActive = True

    for level in levels:
        try:
            new_view_id = active_view.Duplicate(ViewDuplicateOption.Duplicate)
            new_view = doc.GetElement(new_view_id)

            # Уникальное имя вида
            base_name = u"Плита_{}".format(level.Name)
            view_name = base_name
            counter = 1

            while True:
                try:
                    new_view.Name = view_name
                    break
                except Exception:
                    counter += 1
                    view_name = u"{}_{}".format(base_name, counter)

            # Section box новой копии
            new_box = new_view.GetSectionBox()
            transform = new_box.Transform
            inverse = transform.Inverse

            # Мировые отметки реза
            world_bottom = level.Elevation - bottom_offset
            world_top = level.Elevation + top_offset

            # Перевод мировых Z в локальные координаты section box
            local_bottom_pt = inverse.OfPoint(XYZ(0, 0, world_bottom))
            local_top_pt = inverse.OfPoint(XYZ(0, 0, world_top))

            min_pt = new_box.Min
            max_pt = new_box.Max

            z_min = min(local_bottom_pt.Z, local_top_pt.Z)
            z_max = max(local_bottom_pt.Z, local_top_pt.Z)

            new_box.Min = XYZ(min_pt.X, min_pt.Y, z_min)
            new_box.Max = XYZ(max_pt.X, max_pt.Y, z_max)

            new_view.IsSectionBoxActive = True
            new_view.SetSectionBox(new_box)

            created_views.append(view_name)

        except Exception as ex:
            errors.append(u"Уровень '{}': {}".format(level.Name, unicode(ex)))

    t.Commit()

except Exception as ex:
    try:
        if t.HasStarted():
            t.RollBack()
    except Exception:
        pass
    pp_wpf.show_report(
        unicode(ex),
        title=u"Ошибка",
        subtitle=TOOL_TITLE,
        is_error=True
    )
    sys.exit(1)

# ─────────────────────────────────────────────
# ОТЧЁТ
# ─────────────────────────────────────────────

msg = u"Всего уровней: {}\nСоздано видов: {}\nОтступ снизу: {} мм\nОтступ сверху: {} мм".format(
    len(levels),
    len(created_views),
    fmt(bottom_offset_mm),
    fmt(top_offset_mm)
)

if created_views:
    msg += u"\n\nСозданные виды:\n" + u"\n".join(created_views[:20])
    if len(created_views) > 20:
        msg += u"\n... и ещё {}".format(len(created_views) - 20)

if errors:
    msg += u"\n\nОшибки:\n" + u"\n".join(errors[:10])

pp_wpf.show_report(
    msg,
    title=u"Готово",
    subtitle=TOOL_TITLE
)

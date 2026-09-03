# -*- coding: utf-8 -*-
u"""Окно инструмента «Переставить стор. света» (панель «Теплопотери»).

Окно ничего не знает про Revit: скрипт заранее считает, сколько элементов
активного 3D вида записаны с каждой стороной света, и отдаёт сюда готовый
разбор. Обратно возвращается только выбранный поворот.

    from pp_compass_shift_window import ask_options

    opts = ask_options(plan)

    if opts is None:
        ...                       # пользователь закрыл окно
    else:
        opts[u"steps"]            # сдвиг по часовой стрелке, 1..7 (шаг 45°)
        opts[u"recalc_coeff"]     # пересчитывать ли «Добавка на сторону света»

Разбор от скрипта:
    plan = {
        u"counts":    {u"С": 12, u"СВ": 0, ...},   # по всем восьми сторонам
        u"total":     47,
        u"unknown":   3,                            # значения не из списка
        u"by_kind":   [(u"Стены", 20), (u"Окна", 22), ...],
        u"view_name": u"{3D}",
    }
"""

import math
import os

import pp_wpf

from System.Windows import (
    Application, FontWeights, HorizontalAlignment, TextAlignment, Thickness
)
from System.Windows.Controls import Canvas, StackPanel, RadioButton, TextBlock
from System.Windows.Shapes import Ellipse, Line


# Порядок важен: индекс = сектор по часовой стрелке от севера.
SECTORS = [u"С", u"СВ", u"В", u"ЮВ", u"Ю", u"ЮЗ", u"З", u"СЗ"]

STEP_DEG = 45

# Геометрия рисунка компаса (размер холста задан в ui.xaml)
CANVAS_SIZE = 222.0
RING_RADIUS = 66.0
LABEL_RADIUS = 84.0
LABEL_WIDTH = 48.0
LABEL_HEIGHT = 36.0


def _plural(count, one, few, many):
    count = abs(int(count))

    if count % 10 == 1 and count % 100 != 11:
        return one

    if count % 10 in (2, 3, 4) and not (11 <= count % 100 <= 14):
        return few

    return many


def plural_elements(count):
    return _plural(count, u"элемент", u"элемента", u"элементов")


def shift(sector, steps):
    u"""Сторона света после поворота на steps шагов по часовой стрелке."""
    try:
        index = SECTORS.index(sector)
    except ValueError:
        return sector

    return SECTORS[(index + int(steps)) % 8]


def _brush(key):
    try:
        return Application.Current.Resources[key]
    except Exception:
        return None


# ======================================================================
#  Рисунок компаса
# ======================================================================

def _place(element, center_x, center_y, width, height):
    Canvas.SetLeft(element, center_x - width / 2.0)
    Canvas.SetTop(element, center_y - height / 2.0)


def _sector_point(index, radius):
    u"""Центр сектора: индекс растёт по часовой стрелке, ноль — вверх."""
    angle = math.radians(index * STEP_DEG)
    half = CANVAS_SIZE / 2.0

    return (
        half + radius * math.sin(angle),
        half - radius * math.cos(angle)
    )


def _label_block(text, count, active):
    block = StackPanel()
    block.Width = LABEL_WIDTH

    name = TextBlock()
    name.Text = text
    name.FontSize = 15
    name.TextAlignment = TextAlignment.Center
    name.HorizontalAlignment = HorizontalAlignment.Center
    name.Foreground = _brush(u"Accent" if active else u"Muted")

    if active:
        name.FontWeight = FontWeights.SemiBold

    block.Children.Add(name)

    number = TextBlock()
    number.Text = unicode(count) if count else u"—"
    number.FontSize = 10.5
    number.TextAlignment = TextAlignment.Center
    number.HorizontalAlignment = HorizontalAlignment.Center
    number.Margin = Thickness(0, 1, 0, 0)
    number.Foreground = _brush(u"Ink" if active else u"Muted")

    block.Children.Add(number)

    return block


def draw_compass(canvas, counts, steps):
    u"""Перерисовать компас.

    steps = 0 — как записано сейчас; steps > 0 — та же роза, повёрнутая
    по часовой стрелке: буква с позиции j уезжает на позицию j + steps.
    """
    canvas.Children.Clear()

    half = CANVAS_SIZE / 2.0

    ring = Ellipse()
    ring.Width = RING_RADIUS * 2.0
    ring.Height = RING_RADIUS * 2.0
    ring.Stroke = _brush(u"Line")
    ring.StrokeThickness = 1.5
    ring.Fill = _brush(u"Surface")
    _place(ring, half, half, RING_RADIUS * 2.0, RING_RADIUS * 2.0)
    canvas.Children.Add(ring)

    # тонкие оси внутри кольца
    for index in (0, 1, 2, 3):
        x1, y1 = _sector_point(index, RING_RADIUS)
        x2, y2 = _sector_point(index + 4, RING_RADIUS)

        axis = Line()
        axis.X1, axis.Y1, axis.X2, axis.Y2 = x1, y1, x2, y2
        axis.Stroke = _brush(u"Line")
        axis.StrokeThickness = 1.0
        canvas.Children.Add(axis)

    # стрелка розы: смотрит туда, куда после поворота показывает буква «С»
    tip_x, tip_y = _sector_point(steps % 8, RING_RADIUS - 10.0)

    needle = Line()
    needle.X1, needle.Y1 = half, half
    needle.X2, needle.Y2 = tip_x, tip_y
    needle.Stroke = _brush(u"Accent")
    needle.StrokeThickness = 3.0
    canvas.Children.Add(needle)

    cap = Ellipse()
    cap.Width = 9.0
    cap.Height = 9.0
    cap.Fill = _brush(u"Accent")
    _place(cap, tip_x, tip_y, 9.0, 9.0)
    canvas.Children.Add(cap)

    pin = Ellipse()
    pin.Width = 7.0
    pin.Height = 7.0
    pin.Fill = _brush(u"Muted")
    _place(pin, half, half, 7.0, 7.0)
    canvas.Children.Add(pin)

    # буквы: на позиции i стоит буква, приехавшая с позиции i - steps
    for position in range(8):
        letter = SECTORS[(position - int(steps)) % 8]
        count = int((counts or {}).get(letter, 0))

        block = _label_block(letter, count, count > 0)

        x, y = _sector_point(position, LABEL_RADIUS)
        _place(block, x, y, LABEL_WIDTH, LABEL_HEIGHT)

        canvas.Children.Add(block)


# ======================================================================
#  ViewModel
# ======================================================================

class CompassShiftVM(pp_wpf.Notifier):
    u"""Состояние окна: разбор вида и выбранный поворот."""

    def __init__(self, plan):
        pp_wpf.Notifier.__init__(self)

        plan = plan or {}

        self.counts = plan.get(u"counts") or {}
        self.total = int(plan.get(u"total") or 0)
        self.unknown = int(plan.get(u"unknown") or 0)
        self.by_kind = plan.get(u"by_kind") or []
        self.view_name = plan.get(u"view_name") or u""

        self.steps = 0
        self.recalc = False

        self._status = self._build_status()
        self._status_error = False

    # -- свойства для биндингов -------------------------------------
    @property
    def Status(self):
        return self._status

    @property
    def StatusBrush(self):
        return _brush(u"Hot" if self._status_error else u"Muted")

    @property
    def ResultText(self):
        if not self.total:
            return u"Переставлять нечего: на виде нет элементов с заполненной ориентацией."

        if not self.steps:
            return u"Угол не выбран — значения останутся прежними."

        return u"Будет переписано {} {} — поворот на {}° по часовой стрелке.".format(
            self.total, plural_elements(self.total), self.steps * STEP_DEG
        )

    @property
    def MapText(self):
        if not self.total or not self.steps:
            return u""

        pairs = []

        for letter in SECTORS:
            if int(self.counts.get(letter, 0)) > 0:
                pairs.append(u"{} → {}".format(letter, shift(letter, self.steps)))

        if not pairs:
            return u""

        return u"  ·  ".join(pairs)

    @property
    def ResultBrush(self):
        return _brush(u"Ink" if self.IsValid else u"Muted")

    @property
    def IsValid(self):
        return self.total > 0 and self.steps > 0

    @property
    def AngleText(self):
        if not self.steps:
            return u"без поворота"

        return u"на {}° по часовой".format(self.steps * STEP_DEG)

    # -- изменения ---------------------------------------------------
    def refresh(self):
        self._status = self._build_status()
        self._status_error = False

        self.notify(u"Status", u"StatusBrush", u"ResultText", u"MapText",
                    u"ResultBrush", u"IsValid", u"AngleText")

    def _build_status(self):
        if not self.total:
            return u"Вид «{}»: элементов с заполненной ориентацией нет.".format(
                self.view_name
            )

        parts = []

        for name, count in self.by_kind:
            if count:
                parts.append(u"{} {}".format(name.lower(), count))

        line = u"Вид «{}»: {} {}".format(
            self.view_name, self.total, plural_elements(self.total)
        )

        if parts:
            line += u" — " + u", ".join(parts)

        if self.unknown:
            line += u". Не распознано значений: {}".format(self.unknown)

        return line + u"."

    def set_error(self, message):
        self._status = unicode(message or u"")
        self._status_error = True
        self.notify(u"Status", u"StatusBrush")

    def result(self):
        return {
            u"steps": int(self.steps),
            u"recalc_coeff": bool(self.recalc)
        }


# ======================================================================
#  Окно
# ======================================================================

class CompassShiftWindow(object):
    u"""Загрузка разметки, построение чипсов угла, модальный показ."""

    def __init__(self, plan):
        xaml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            u"ui.xaml"
        )

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = CompassShiftVM(plan)
        self.window.DataContext = self.vm
        self.accepted = False
        self.silent = False
        self.chips = []

        self._build_angles()
        self._wire()
        self._redraw()

        pp_wpf.set_owner(self.window)
        pp_wpf.fit_to_screen(self.window)

    # -- чипсы угла ---------------------------------------------------
    def _build_angles(self):
        panel = self.window.FindName("PnlAngles")
        style = None

        try:
            style = Application.Current.Resources[u"RadioButton.Chip"]
        except Exception:
            style = None

        for index in range(8):
            chip = RadioButton()
            chip.Content = u"{}°".format(index * STEP_DEG)
            chip.GroupName = u"PPCompassAngle"
            chip.Margin = Thickness(0, 0, 6, 6)

            if style is not None:
                chip.Style = style

            if index == 0:
                chip.ToolTip = u"Оставить стороны света как есть"
            else:
                chip.ToolTip = u"Здание повернули на {}° по часовой стрелке".format(
                    index * STEP_DEG
                )

            chip.IsChecked = (index == 0)
            chip.Checked += self._angle_handler(index)

            panel.Children.Add(chip)
            self.chips.append(chip)

    def _angle_handler(self, index):
        u"""Фабрика обработчика: замыкание по index в цикле работает неверно."""
        guarded = pp_wpf.guard(self.vm.set_error)

        @guarded
        def on_checked(sender, args):
            if self.silent:
                return

            self.vm.steps = index
            self._redraw()

        return on_checked

    def _set_steps(self, steps):
        steps = int(steps) % 8

        self.vm.steps = steps

        self.silent = True

        try:
            self.chips[steps].IsChecked = True
        finally:
            self.silent = False

        self._redraw()

    # -- подписки -----------------------------------------------------
    def _wire(self):
        find = self.window.FindName
        guarded = pp_wpf.guard(self.vm.set_error)

        @guarded
        def on_cw(sender, args):
            self._set_steps(self.vm.steps + 1)

        @guarded
        def on_ccw(sender, args):
            self._set_steps(self.vm.steps - 1)

        @guarded
        def on_coeff(sender, args):
            if self.silent:
                return

            self.vm.recalc = bool(sender.IsChecked)
            self.vm.refresh()

        @guarded
        def on_run(sender, args):
            self._accept()

        @guarded
        def on_cancel(sender, args):
            self.window.Close()

        find("BtnCw").Click += on_cw
        find("BtnCcw").Click += on_ccw

        coeff = find("ChkCoeff")
        coeff.IsChecked = self.vm.recalc
        coeff.Checked += on_coeff
        coeff.Unchecked += on_coeff

        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(
            self.window,
            on_accept=self._accept,
            on_cancel=self.window.Close
        )

    # -- перерисовка ---------------------------------------------------
    def _redraw(self):
        draw_compass(self.window.FindName("CanvasNow"), self.vm.counts, 0)
        draw_compass(self.window.FindName("CanvasNext"), self.vm.counts,
                     self.vm.steps)

        self.window.FindName("TxtAngle").Text = self.vm.AngleText

        self.vm.refresh()

    def _accept(self):
        if not self.vm.IsValid:
            return

        self.accepted = True
        self.window.DialogResult = True

    def show(self):
        self.window.ShowDialog()

        if not self.accepted:
            return None

        return self.vm.result()


def ask_options(plan):
    u"""Показать окно. Возврат: словарь поворота или None, если отменили."""
    return CompassShiftWindow(plan).show()

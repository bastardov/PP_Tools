# -*- coding: utf-8 -*-
u"""Окно параметров инструмента «Опуск / Подъем трассы».

Окно модальное: собирает направление, угол и величину, закрывается и
отдаёт значения скрипту. Работы с моделью здесь нет, поэтому ExternalEvent
не нужен.
"""

import os
import math

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

from System.Windows import Application


ANGLE_PRESETS = [30.0, 45.0, 60.0, 90.0]

MIN_ANGLE = 1.0
MAX_ANGLE = 90.0


def format_mm(value):
    u"""500.0 -> «500», 512.5 -> «512.5»."""
    try:
        if float(value) == int(float(value)):
            return unicode(int(float(value)))
    except Exception:
        pass

    return unicode(value)


def parse_number(text):
    u"""Возвращает float или None. Запятая принимается как разделитель."""
    if text is None:
        return None

    text = unicode(text).strip().replace(u",", u".")

    if not text:
        return None

    try:
        return float(text)
    except ValueError:
        return None


# ======================================================================
#  ViewModel
# ======================================================================

class DropRouteVM(pp_wpf.Notifier):

    def __init__(self, direction, angle, value_mm):
        pp_wpf.Notifier.__init__(self)

        self._direction = direction if direction in (u"Опуск", u"Подъем") else u"Опуск"

        if angle in ANGLE_PRESETS:
            self._angle_preset = angle
            self._custom_angle_text = format_mm(angle)
        else:
            self._angle_preset = None
            self._custom_angle_text = format_mm(angle)

        self._value_text = format_mm(value_mm)

        self._status = u""
        self._status_error = False

        self.revalidate()

    # ---------- состояние, читаемое разметкой ----------------------

    @property
    def IsDown(self):
        return self._direction == u"Опуск"

    @property
    def IsUp(self):
        return self._direction == u"Подъем"

    @property
    def IsCustomAngle(self):
        return self._angle_preset is None

    @property
    def RunLabel(self):
        return u"Опустить" if self.IsDown else u"Поднять"

    @property
    def Status(self):
        return self._status

    @property
    def StatusBrush(self):
        key = u"Hot" if self._status_error else u"Muted"

        try:
            return Application.Current.Resources[key]
        except Exception:
            return None

    @property
    def IsValid(self):
        return not self._status_error and self.value_mm is not None and self.angle is not None

    @property
    def Hint(self):
        offset = self.horizontal_offset_mm

        if offset is None:
            return u""

        if offset <= 0.0001:
            return u"Вставка вертикальная, горизонтального отступа нет."

        return u"Горизонтальный отступ вдоль трассы: {} мм.".format(
            format_mm(round(offset, 1))
        )

    # ---------- вычисляемые значения --------------------------------

    @property
    def direction(self):
        return self._direction

    @property
    def value_mm(self):
        value = parse_number(self._value_text)

        if value is None or value <= 0:
            return None

        return value

    @property
    def angle(self):
        if self._angle_preset is not None:
            return self._angle_preset

        value = parse_number(self._custom_angle_text)

        if value is None or value < MIN_ANGLE or value > MAX_ANGLE:
            return None

        return value

    @property
    def horizontal_offset_mm(self):
        value = self.value_mm
        angle = self.angle

        if value is None or angle is None:
            return None

        if angle >= 89.9:
            return 0.0

        return value / math.tan(math.radians(angle))

    # ---------- команды, которые дёргают обработчики ----------------

    def set_direction(self, direction):
        self._direction = direction
        self.notify(u"IsDown", u"IsUp", u"RunLabel")
        self.revalidate()

    def set_angle_preset(self, angle):
        self._angle_preset = angle
        self.notify(u"IsCustomAngle")
        self.revalidate()

    def set_custom_angle(self, text):
        self._custom_angle_text = text
        self.revalidate()

    def set_value(self, text):
        self._value_text = text
        self.revalidate()

    def revalidate(self):
        u"""Единственное место, где решается, готово окно к запуску или нет."""
        if parse_number(self._value_text) is None:
            self._set_status(u"Величина: введите число, например 500.", True)

        elif self.value_mm is None:
            self._set_status(u"Величина должна быть больше нуля.", True)

        elif self.angle is None:
            self._set_status(
                u"Угол: введите число от {} до {} градусов.".format(
                    format_mm(MIN_ANGLE), format_mm(MAX_ANGLE)
                ),
                True
            )

        else:
            self._set_status(
                u"{} на {} мм под углом {}°.".format(
                    u"Опуск" if self.IsDown else u"Подъем",
                    format_mm(self.value_mm),
                    format_mm(self.angle)
                ),
                False
            )

        self.notify(u"Hint", u"IsValid")

    def _set_status(self, text, is_error):
        self._status = text
        self._status_error = is_error
        self.notify(u"Status", u"StatusBrush")

    def result(self):
        return {
            u"direction": self.direction,
            u"value_mm": self.value_mm,
            u"angle": self.angle,
        }


# ======================================================================
#  Окно
# ======================================================================

class DropRouteWindow(object):

    def __init__(self, direction, angle, value_mm):
        xaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), u"ui.xaml")

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = DropRouteVM(direction, angle, value_mm)
        self.window.DataContext = self.vm

        self.accepted = False

        self._fill_controls()
        self._wire()

        pp_wpf.set_owner(self.window)

    # ---------- начальное состояние до подписки на события ----------

    def _fill_controls(self):
        find = self.window.FindName

        find("ChipDown").IsChecked = self.vm.IsDown
        find("ChipUp").IsChecked = self.vm.IsUp

        chips = self._angle_chips()

        for angle, chip in chips:
            chip.IsChecked = (self.vm._angle_preset == angle)

        find("ChipCustom").IsChecked = self.vm.IsCustomAngle

        find("TxtCustomAngle").Text = self.vm._custom_angle_text
        find("TxtValue").Text = self.vm._value_text

    def _angle_chips(self):
        find = self.window.FindName

        return [
            (30.0, find("Chip30")),
            (45.0, find("Chip45")),
            (60.0, find("Chip60")),
            (90.0, find("Chip90")),
        ]

    # ---------- подписки --------------------------------------------

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        @guard
        def on_down(sender, args):
            self.vm.set_direction(u"Опуск")

        @guard
        def on_up(sender, args):
            self.vm.set_direction(u"Подъем")

        find("ChipDown").Checked += on_down
        find("ChipUp").Checked += on_up

        # Фабрика обработчиков: общий цикл с lambda отдал бы всем чипсам
        # последнее значение угла.
        def make_angle_handler(angle):
            @guard
            def handler(sender, args):
                self.vm.set_angle_preset(angle)

            return handler

        for angle, chip in self._angle_chips():
            chip.Checked += make_angle_handler(angle)

        @guard
        def on_custom_chip(sender, args):
            self.vm.set_angle_preset(None)

        find("ChipCustom").Checked += on_custom_chip

        @guard
        def on_custom_angle(sender, args):
            self.vm.set_custom_angle(sender.Text)

        find("TxtCustomAngle").TextChanged += on_custom_angle

        @guard
        def on_value(sender, args):
            self.vm.set_value(sender.Text)

        find("TxtValue").TextChanged += on_value

        @guard
        def on_run(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    # ---------- действия ---------------------------------------------

    def _accept(self):
        if not self.vm.IsValid:
            return

        self.accepted = True

        # Присвоение DialogResult само закрывает модальное окно.
        self.window.DialogResult = True

    def _fail(self, message):
        self.vm._set_status(message, True)

    # ---------- запуск -------------------------------------------------

    def show(self):
        u"""Возвращает словарь параметров или None, если пользователь отказался."""
        self.window.ShowDialog()

        if not self.accepted:
            return None

        return self.vm.result()


def ask_settings(direction, angle, value_mm):
    return DropRouteWindow(direction, angle, value_mm).show()

# -*- coding: utf-8 -*-
u"""Окно параметров инструмента «Опуск / Подъем трассы».

Три режима:

* «Опуск» и «Подъем» — смещение на заданную величину по вертикали;
* «По отметке» — участок встаёт на отметку от выбранного уровня модели.
  Опуск это или подъем, скрипт решает сам по знаку разницы отметок.

Окно модальное: собирает режим, угол и величину (или уровень с отметкой),
закрывается и отдаёт значения скрипту. Работы с моделью здесь нет, поэтому
ExternalEvent не нужен.
"""

import os
import math

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

from System.Windows import Application


ANGLE_PRESETS = [30.0, 45.0, 60.0, 90.0]

MIN_ANGLE = 1.0
MAX_ANGLE = 90.0

MODE_DOWN = u"Опуск"
MODE_UP = u"Подъем"
MODE_LEVEL = u"Отметка"

REF_BOTTOM = u"низ"
REF_MIDDLE = u"середина"
REF_TOP = u"верх"

REF_KINDS = [REF_BOTTOM, REF_MIDDLE, REF_TOP]


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

    def __init__(self, mode, angle, value_mm, levels, level_key, ref_kind, elev_mm):
        pp_wpf.Notifier.__init__(self)

        self._mode = mode if mode in (MODE_DOWN, MODE_UP, MODE_LEVEL) else MODE_DOWN

        if angle in ANGLE_PRESETS:
            self._angle_preset = angle
            self._custom_angle_text = format_mm(angle)
        else:
            self._angle_preset = None
            self._custom_angle_text = format_mm(angle)

        self._value_text = format_mm(value_mm)

        # ---- отметка от уровня ----
        self._levels = levels or []
        self._level_index = self._index_of_level(level_key)

        self._ref_kind = ref_kind if ref_kind in REF_KINDS else REF_BOTTOM

        if elev_mm is None:
            self._elev_text = u""
        else:
            self._elev_text = format_mm(elev_mm)

        self._status = u""
        self._status_error = False

        self.revalidate()

    def _index_of_level(self, level_key):
        u"""Индекс сохранённого уровня; если такого нет — самый нижний."""
        for i, level in enumerate(self._levels):
            if level.get(u"key") == level_key:
                return i

        return 0 if self._levels else -1

    # ---------- состояние, читаемое разметкой ----------------------

    @property
    def IsDown(self):
        return self._mode == MODE_DOWN

    @property
    def IsUp(self):
        return self._mode == MODE_UP

    @property
    def IsByLevel(self):
        return self._mode == MODE_LEVEL

    @property
    def IsByOffset(self):
        return not self.IsByLevel

    @property
    def IsCustomAngle(self):
        return self._angle_preset is None

    @property
    def RunLabel(self):
        if self.IsByLevel:
            return u"Переместить"

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
        if self._status_error:
            return False

        if self.angle is None:
            return False

        if self.IsByLevel:
            return self.level is not None and self.elev_mm is not None

        return self.value_mm is not None

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

    @property
    def LevelHint(self):
        level = self.level

        if level is None:
            return u"В модели не найдено ни одного уровня."

        return (
            u"Отметка считается от «{}». Величину смещения и горизонтальный "
            u"отступ инструмент посчитает сам после выбора участка."
        ).format(level.get(u"key"))

    # ---------- вычисляемые значения --------------------------------

    @property
    def mode(self):
        return self._mode

    @property
    def direction(self):
        u"""Для режима «По отметке» знак смещения определяется уже в скрипте."""
        return self._mode

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
    def level(self):
        if self._level_index < 0 or self._level_index >= len(self._levels):
            return None

        return self._levels[self._level_index]

    @property
    def elev_mm(self):
        u"""Отметка бывает нулевой и отрицательной, поэтому проверяем только разбор."""
        return parse_number(self._elev_text)

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

    def set_mode(self, mode):
        self._mode = mode
        self.notify(
            u"IsDown", u"IsUp", u"IsByLevel", u"IsByOffset", u"RunLabel"
        )
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

    def set_level_index(self, index):
        self._level_index = index
        self.revalidate()

    def set_elevation(self, ref_kind, text):
        self._ref_kind = ref_kind
        self._elev_text = text
        self.revalidate()

    def revalidate(self):
        u"""Единственное место, где решается, готово окно к запуску или нет."""
        if self.IsByLevel:
            self._revalidate_level()
        else:
            self._revalidate_offset()

        self.notify(u"Hint", u"LevelHint", u"IsValid")

    def _revalidate_offset(self):
        if parse_number(self._value_text) is None:
            self._set_status(u"Величина: введите число, например 500.", True)

        elif self.value_mm is None:
            self._set_status(u"Величина должна быть больше нуля.", True)

        elif self.angle is None:
            self._set_status(self._angle_error(), True)

        else:
            self._set_status(
                u"{} на {} мм под углом {}°.".format(
                    u"Опуск" if self.IsDown else u"Подъем",
                    format_mm(self.value_mm),
                    format_mm(self.angle)
                ),
                False
            )

    def _revalidate_level(self):
        if self.level is None:
            self._set_status(u"Выберите уровень.", True)

        elif self.elev_mm is None:
            self._set_status(
                u"Отметка: заполните одно из полей, например 2750 или -150.",
                True
            )

        elif self.angle is None:
            self._set_status(self._angle_error(), True)

        else:
            self._set_status(
                u"{} на отм. {} мм от «{}», угол {}°.".format(
                    self._ref_kind[:1].upper() + self._ref_kind[1:],
                    format_mm(self.elev_mm),
                    self.level.get(u"key"),
                    format_mm(self.angle)
                ),
                False
            )

    def _angle_error(self):
        return u"Угол: введите число от {} до {} градусов.".format(
            format_mm(MIN_ANGLE), format_mm(MAX_ANGLE)
        )

    def _set_status(self, text, is_error):
        self._status = text
        self._status_error = is_error
        self.notify(u"Status", u"StatusBrush")

    def result(self):
        level = self.level

        return {
            u"mode": self.mode,
            u"direction": self.direction,
            u"value_mm": self.value_mm,
            u"angle": self.angle,
            u"level_key": level.get(u"key") if level else None,
            u"level_id": level.get(u"id") if level else None,
            u"ref_kind": self._ref_kind,
            u"elev_mm": self.elev_mm,
        }


# ======================================================================
#  Окно
# ======================================================================

class DropRouteWindow(object):

    def __init__(self, mode, angle, value_mm, levels, level_key, ref_kind, elev_mm):
        xaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), u"ui.xaml")

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = DropRouteVM(mode, angle, value_mm, levels, level_key, ref_kind, elev_mm)
        self.window.DataContext = self.vm

        self.accepted = False

        # Пока перекладываем текст между полями отметки, обработчики молчат
        self._suppress = False

        self._fill_controls()
        self._wire()

        pp_wpf.set_owner(self.window)
        pp_wpf.fit_to_screen(self.window)

    # ---------- начальное состояние до подписки на события ----------

    def _fill_controls(self):
        find = self.window.FindName

        find("ChipDown").IsChecked = self.vm.IsDown
        find("ChipUp").IsChecked = self.vm.IsUp
        find("ChipLevel").IsChecked = self.vm.IsByLevel

        for angle, chip in self._angle_chips():
            chip.IsChecked = (self.vm._angle_preset == angle)

        find("ChipCustom").IsChecked = self.vm.IsCustomAngle

        find("TxtCustomAngle").Text = self.vm._custom_angle_text
        find("TxtValue").Text = self.vm._value_text

        combo = find("CmbLevel")
        combo.Items.Clear()

        for level in self.vm._levels:
            combo.Items.Add(level.get(u"label"))

        if self.vm._level_index >= 0:
            combo.SelectedIndex = self.vm._level_index

        # Заполнено ровно одно поле — то, которым пользовались в прошлый раз
        self._suppress = True

        try:
            for kind, name in self._elev_fields():
                if kind == self.vm._ref_kind:
                    find(name).Text = self.vm._elev_text
                else:
                    find(name).Text = u""
        finally:
            self._suppress = False

    def _angle_chips(self):
        find = self.window.FindName

        return [
            (30.0, find("Chip30")),
            (45.0, find("Chip45")),
            (60.0, find("Chip60")),
            (90.0, find("Chip90")),
        ]

    def _elev_fields(self):
        return [
            (REF_BOTTOM, "TxtElevBottom"),
            (REF_MIDDLE, "TxtElevMiddle"),
            (REF_TOP, "TxtElevTop"),
        ]

    # ---------- подписки --------------------------------------------

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        @guard
        def on_down(sender, args):
            self.vm.set_mode(MODE_DOWN)

        @guard
        def on_up(sender, args):
            self.vm.set_mode(MODE_UP)

        @guard
        def on_level(sender, args):
            self.vm.set_mode(MODE_LEVEL)

        find("ChipDown").Checked += on_down
        find("ChipUp").Checked += on_up
        find("ChipLevel").Checked += on_level

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
        def on_level_changed(sender, args):
            self.vm.set_level_index(sender.SelectedIndex)

        find("CmbLevel").SelectionChanged += on_level_changed

        # Та же фабрика: каждому полю отметки нужен свой вид отметки.
        def make_elev_handler(kind):
            @guard
            def handler(sender, args):
                if self._suppress:
                    return

                self._clear_other_elev_fields(kind)
                self.vm.set_elevation(kind, sender.Text)

            return handler

        for kind, name in self._elev_fields():
            find(name).TextChanged += make_elev_handler(kind)

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

    def _clear_other_elev_fields(self, kind):
        u"""Заполнено всегда одно поле: два других чистим без реакции обработчиков."""
        find = self.window.FindName

        self._suppress = True

        try:
            for other_kind, name in self._elev_fields():
                if other_kind == kind:
                    continue

                box = find(name)

                if box.Text:
                    box.Text = u""
        finally:
            self._suppress = False

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


def ask_settings(mode, angle, value_mm, levels=None, level_key=None,
                 ref_kind=None, elev_mm=None):
    return DropRouteWindow(
        mode, angle, value_mm, levels, level_key, ref_kind, elev_mm
    ).show()

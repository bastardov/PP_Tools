# -*- coding: utf-8 -*-
u"""WPF-окно параметров простого вертикального перемещения MEP-элементов."""

import os

import pp_wpf

from System.Windows import Application


MODE_DOWN = u"Опустить"
MODE_UP = u"Поднять"
MODE_LEVEL = u"На отметку"

REF_ANCHOR = u"Опорная точка"
REF_BOTTOM = u"Низ группы"
REF_TOP = u"Верх группы"

XAML_FILE = u"ui.xaml"


def parse_number(text):
    if text is None:
        return None

    value = unicode(text).strip().replace(u",", u".")

    if not value:
        return None

    try:
        return float(value)
    except ValueError:
        return None


def format_number(value):
    try:
        number = float(value)
        if number == int(number):
            return unicode(int(number))
        return u"{:.2f}".format(number).rstrip(u"0").rstrip(u".")
    except Exception:
        return u""


def format_signed_mm(value):
    try:
        number = float(value)
    except Exception:
        return u"—"

    if abs(number) < 0.05:
        return u"0 мм"

    return u"{:+.1f} мм".format(number)


class ElementMoveVM(pp_wpf.Notifier):

    def __init__(self, mode, value_mm, elev_mm, levels, level_key, ref_kind,
                 refs_mm, selected_count, external_count):
        pp_wpf.Notifier.__init__(self)

        self._mode = mode if mode in (MODE_DOWN, MODE_UP, MODE_LEVEL) else MODE_UP
        self._value_text = format_number(value_mm if value_mm is not None else 500.0)
        self._elevation_text = format_number(elev_mm if elev_mm is not None else 0.0)
        self._levels = list(levels or [])
        self._level_index = self._find_level(level_key)
        self._ref_kind = ref_kind if ref_kind in (
            REF_ANCHOR, REF_BOTTOM, REF_TOP) else REF_ANCHOR
        self._refs_mm = dict(refs_mm or {})
        self._selected_count = int(selected_count or 0)
        self._external_count = int(external_count or 0)
        self._status = u""
        self._status_error = False

        self.revalidate()

    def _find_level(self, level_key):
        if not self._levels:
            return -1

        for index, level in enumerate(self._levels):
            if level.get(u"key") == level_key:
                return index

        return 0

    @property
    def Subtitle(self):
        text = u"Выбрано элементов: {}. Вся выборка перемещается одной группой.".format(
            self._selected_count)

        if self._external_count:
            text += u" Подключений к элементам вне выборки: {}.".format(
                self._external_count)

        return text

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
    def IsAnchor(self):
        return self._ref_kind == REF_ANCHOR

    @property
    def IsBottom(self):
        return self._ref_kind == REF_BOTTOM

    @property
    def IsTop(self):
        return self._ref_kind == REF_TOP

    @property
    def IsValid(self):
        if self.IsByLevel:
            return (
                self._level_index >= 0 and
                self.elevation_value() is not None and
                self._refs_mm.get(self._ref_kind) is not None
            )

        value = self.offset_value()
        return value is not None and value > 0

    @property
    def Preview(self):
        if not self.IsValid:
            return u"Заполните параметры перемещения."

        if self.IsByOffset:
            direction = u"опустится" if self.IsDown else u"поднимется"
            return u"Вся группа {} на {} мм; взаимное положение элементов сохранится.".format(
                direction, format_number(self.offset_value()))

        level = self.selected_level()
        current = self._refs_mm.get(self._ref_kind)

        if level is None or current is None:
            return u"Не удалось определить текущую опорную отметку группы."

        target = float(level[u"elev_mm"]) + self.elevation_value()
        delta = target - float(current)

        return u"{} переместится на отметку {} мм от «{}»; сдвиг группы {}.".format(
            self._ref_kind,
            format_number(self.elevation_value()),
            level[u"key"],
            format_signed_mm(delta))

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

    def offset_value(self):
        return parse_number(self._value_text)

    def elevation_value(self):
        return parse_number(self._elevation_text)

    def selected_level(self):
        if self._level_index < 0 or self._level_index >= len(self._levels):
            return None

        return self._levels[self._level_index]

    def set_mode(self, mode):
        self._mode = mode
        self.revalidate()
        self.notify(u"IsDown", u"IsUp", u"IsByLevel", u"IsByOffset", u"Preview")

    def set_offset(self, text):
        self._value_text = unicode(text)
        self.revalidate()

    def set_elevation(self, text):
        self._elevation_text = unicode(text)
        self.revalidate()

    def set_level_index(self, index):
        self._level_index = int(index)
        self.revalidate()

    def set_ref_kind(self, ref_kind):
        self._ref_kind = ref_kind
        self.revalidate()
        self.notify(u"IsAnchor", u"IsBottom", u"IsTop", u"Preview")

    def revalidate(self):
        if self.IsByLevel:
            if not self._levels:
                self._set_status(u"В модели нет уровней.", True)
            elif self.elevation_value() is None:
                self._set_status(u"Отметка: введите число в миллиметрах.", True)
            elif self._refs_mm.get(self._ref_kind) is None:
                self._set_status(u"Не удалось определить выбранную опору группы.", True)
            else:
                self._set_ready_status()
        else:
            value = self.offset_value()

            if value is None or value <= 0:
                self._set_status(u"Расстояние: введите число больше 0 мм.", True)
            else:
                self._set_ready_status()

        self.notify(u"IsValid", u"Preview")

    def _set_ready_status(self):
        if self._external_count:
            self._set_status(
                u"Есть внешние подключения — Revit может перестроить соседние участки.",
                False)
        else:
            self._set_status(u"Можно перемещать выбранную группу.", False)

    def _set_status(self, text, is_error):
        self._status = text
        self._status_error = bool(is_error)
        self.notify(u"Status", u"StatusBrush")

    def result(self):
        level = self.selected_level()

        return {
            u"mode": self._mode,
            u"value_mm": self.offset_value(),
            u"elev_mm": self.elevation_value(),
            u"level_key": level[u"key"] if level is not None else None,
            u"ref_kind": self._ref_kind,
        }


class ElementMoveWindow(object):

    def __init__(self, mode, value_mm, elev_mm, levels, level_key, ref_kind,
                 refs_mm, selected_count, external_count):
        xaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), XAML_FILE)

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = ElementMoveVM(
            mode, value_mm, elev_mm, levels, level_key, ref_kind,
            refs_mm, selected_count, external_count)
        self.window.DataContext = self.vm
        self.accepted = False

        self._fill()
        self._wire()

        pp_wpf.set_owner(self.window)
        pp_wpf.fit_to_screen(self.window)

    def _fill(self):
        find = self.window.FindName

        find("ChipDown").IsChecked = self.vm.IsDown
        find("ChipUp").IsChecked = self.vm.IsUp
        find("ChipLevel").IsChecked = self.vm.IsByLevel

        find("ChipAnchor").IsChecked = self.vm.IsAnchor
        find("ChipBottom").IsChecked = self.vm.IsBottom
        find("ChipTop").IsChecked = self.vm.IsTop

        find("TxtValue").Text = self.vm._value_text
        find("TxtElevation").Text = self.vm._elevation_text

        combo = find("CmbLevel")

        for level in self.vm._levels:
            combo.Items.Add(level[u"label"])

        if self.vm._level_index >= 0:
            combo.SelectedIndex = self.vm._level_index

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

        @guard
        def on_offset(sender, args):
            self.vm.set_offset(sender.Text)

        @guard
        def on_elevation(sender, args):
            self.vm.set_elevation(sender.Text)

        @guard
        def on_level_changed(sender, args):
            self.vm.set_level_index(sender.SelectedIndex)

        def make_ref_handler(ref_kind):
            @guard
            def handler(sender, args):
                self.vm.set_ref_kind(ref_kind)

            return handler

        @guard
        def on_run(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("ChipDown").Checked += on_down
        find("ChipUp").Checked += on_up
        find("ChipLevel").Checked += on_level
        find("TxtValue").TextChanged += on_offset
        find("TxtElevation").TextChanged += on_elevation
        find("CmbLevel").SelectionChanged += on_level_changed
        find("ChipAnchor").Checked += make_ref_handler(REF_ANCHOR)
        find("ChipBottom").Checked += make_ref_handler(REF_BOTTOM)
        find("ChipTop").Checked += make_ref_handler(REF_TOP)
        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    def _accept(self):
        if not self.vm.IsValid:
            return

        self.accepted = True
        self.window.DialogResult = True

    def _fail(self, message):
        self.vm._set_status(message, True)

    def show(self):
        self.window.ShowDialog()

        if not self.accepted:
            return None

        return self.vm.result()


def ask_settings(mode, value_mm, elev_mm, levels, level_key, ref_kind,
                 refs_mm, selected_count, external_count):
    return ElementMoveWindow(
        mode, value_mm, elev_mm, levels, level_key, ref_kind,
        refs_mm, selected_count, external_count).show()

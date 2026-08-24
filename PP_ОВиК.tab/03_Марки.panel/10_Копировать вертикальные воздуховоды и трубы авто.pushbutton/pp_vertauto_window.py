# -*- coding: utf-8 -*-
u"""Окно опций инструмента «Верт. авто» — марки вертикальных воздуховодов и труб.

Окно ничего не знает про Revit: скрипт сообщает ему только один факт —
есть ли в модели уровни. Без уровней фильтр «только стояки» работать не может,
поэтому кнопка блокируется, а причина уходит в строку статуса.

    from pp_vertauto_window import ask_options

    opts = ask_options(has_levels=True)

    if opts is None:
        ...           # пользователь закрыл окно
    else:
        opts["only_crossing"]      # bool
"""

import os

import pp_wpf

from System.Windows import Application


class VertAutoVM(pp_wpf.Notifier):
    u"""Состояние окна и проверка ввода."""

    def __init__(self, has_levels):
        pp_wpf.Notifier.__init__(self)

        self.has_levels = bool(has_levels)

        self._only_crossing = True
        self._status = u""
        self._status_error = False

        self.revalidate()

    # -- свойства для биндингов -------------------------------------
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
        return not self._status_error

    @property
    def OnlyCrossing(self):
        return self._only_crossing

    # -- изменения из окна ------------------------------------------
    def set_only_crossing(self, value):
        self._only_crossing = bool(value)
        self.revalidate()

    def revalidate(self):
        if self._only_crossing and not self.has_levels:
            self._set_status(
                u"В модели нет ни одного уровня — не с чем сверять пересечение. "
                u"Снимите галочку.",
                True
            )

        elif self._only_crossing:
            self._set_status(
                u"Марки получат только вертикальные участки, пересекающие уровень.",
                False
            )

        else:
            self._set_status(
                u"Марки получат все вертикальные участки на активном виде.",
                False
            )

        self.notify(u"IsValid")

    def set_error(self, message):
        self._set_status(message, True)
        self.notify(u"IsValid")

    def _set_status(self, message, is_error):
        self._status = message
        self._status_error = is_error
        self.notify(u"Status", u"StatusBrush")

    # -- результат ---------------------------------------------------
    def result(self):
        return {"only_crossing": self._only_crossing}


class VertAutoWindow(object):
    u"""Загрузка разметки, подписки, модальный показ."""

    def __init__(self, has_levels):
        xaml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            u"ui.xaml"
        )

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = VertAutoVM(has_levels)
        self.window.DataContext = self.vm
        self.accepted = False

        self._fill_controls()
        self._wire()

        pp_wpf.set_owner(self.window)

    def _fill_controls(self):
        self.window.FindName("ChkOnlyCrossing").IsChecked = self.vm.OnlyCrossing

    def _wire(self):
        find = self.window.FindName
        guarded = pp_wpf.guard(self.vm.set_error)

        @guarded
        def on_toggle(sender, args):
            self.vm.set_only_crossing(sender.IsChecked)

        @guarded
        def on_run(sender, args):
            self._accept()

        @guarded
        def on_cancel(sender, args):
            self.window.Close()

        chk = find("ChkOnlyCrossing")
        chk.Checked += on_toggle
        chk.Unchecked += on_toggle

        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(
            self.window,
            on_accept=self._accept,
            on_cancel=self.window.Close
        )

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


def ask_options(has_levels):
    u"""Показать окно. Возврат: словарь опций или None, если отменили."""
    return VertAutoWindow(has_levels).show()

# -*- coding: utf-8 -*-
u"""Окно инструмента «Объединить стены» (панель «Теплопотери»).

Окно ничего не знает про Revit: скрипт разбирает выделение сам и передаёт сюда
готовые строки разбора и список типов.

    from pp_wall_merge_window import ask_options

    opts = ask_options(rows, types, sets_count, walls_count)

    if opts is None:
        ...                      # пользователь закрыл окно
    else:
        opts[u"type_key"]        # u"auto" либо строка с id типа стены

rows  — [{u"text": u"Набор 1 · 3 стены · …", u"bad": False}, …]
types — [{u"key": u"auto", u"title": u"Автоматически — …"}, …]
"""

import os

import pp_wpf

from System.Windows import Application, Visibility, TextWrapping
from System.Windows.Controls import TextBlock


def plural_walls(count):
    count = abs(int(count))

    if count % 10 == 1 and count % 100 != 11:
        return u"стена"

    if count % 10 in (2, 3, 4) and not (11 <= count % 100 <= 14):
        return u"стены"

    return u"стен"


def plural_sets(count):
    count = abs(int(count))

    if count % 10 == 1 and count % 100 != 11:
        return u"набор"

    if count % 10 in (2, 3, 4) and not (11 <= count % 100 <= 14):
        return u"набора"

    return u"наборов"


class WallMergeVM(pp_wpf.Notifier):
    u"""Состояние окна: считать нечего, всё решено до открытия."""

    def __init__(self, sets_count, walls_count, reject_count):
        pp_wpf.Notifier.__init__(self)

        self.sets_count = int(sets_count or 0)
        self.walls_count = int(walls_count or 0)
        self.reject_count = int(reject_count or 0)

        self.type_key = u"auto"

        self._status = self._build_status()
        self._status_error = False

    # -- свойства для биндингов -------------------------------------
    @property
    def Status(self):
        return self._status

    @property
    def StatusBrush(self):
        return self._brush(u"Hot" if self._status_error else u"Muted")

    @property
    def ResultText(self):
        if not self.sets_count:
            return u"Объединять нечего: в выделении нет двух кусков одной стены."

        return u"Из {} {} получится {} {}.".format(
            self.walls_count, plural_walls(self.walls_count),
            self.sets_count, plural_walls(self.sets_count)
        )

    @property
    def ResultBrush(self):
        return self._brush(u"Muted" if not self.sets_count else u"Ink")

    @property
    def IsValid(self):
        return self.sets_count > 0

    def _brush(self, key):
        try:
            return Application.Current.Resources[key]
        except Exception:
            return None

    def _build_status(self):
        if not self.sets_count and not self.reject_count:
            return u"Стены для объединения не выбраны."

        parts = []

        if self.sets_count:
            parts.append(u"{} {} на объединение".format(
                self.sets_count, plural_sets(self.sets_count)
            ))

        if self.reject_count:
            parts.append(u"не получится: {}".format(self.reject_count))

        return u" · ".join(parts)

    # -- изменения из окна ------------------------------------------
    def set_type_key(self, key):
        self.type_key = key

    def set_error(self, message):
        self._status = unicode(message or u"")
        self._status_error = True
        self.notify(u"Status", u"StatusBrush")

    def result(self):
        return {u"type_key": self.type_key}


class WallMergeWindow(object):
    u"""Загрузка разметки, подписки, модальный показ."""

    def __init__(self, rows, types, sets_count, walls_count, reject_count):
        xaml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            u"ui.xaml"
        )

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = WallMergeVM(sets_count, walls_count, reject_count)
        self.types = list(types or [])
        self.window.DataContext = self.vm
        self.accepted = False

        self._fill_plan(rows or [])
        self._fill_types()
        self._wire()
        self._select_type()

        pp_wpf.set_owner(self.window)
        pp_wpf.fit_to_screen(self.window)

    def _fill_plan(self, rows):
        lst = self.window.FindName("LstPlan")

        hot = self._brush(u"Hot")
        ink = self._brush(u"Ink")

        for row in rows:
            block = TextBlock()
            block.Text = row.get(u"text", u"")
            block.TextWrapping = TextWrapping.Wrap

            brush = hot if row.get(u"bad") else ink

            if brush is not None:
                block.Foreground = brush

            lst.Items.Add(block)

        if not rows:
            lst.Visibility = Visibility.Collapsed
            self.window.FindName("TxtEmpty").Visibility = Visibility.Visible

    def _fill_types(self):
        combo = self.window.FindName("CmbType")

        for item in self.types:
            combo.Items.Add(item.get(u"title", u""))

        combo.IsEnabled = len(self.types) > 1

    def _select_type(self):
        u"""Начальный выбор — отдельным шагом после подписок (см. UI.md)."""
        combo = self.window.FindName("CmbType")

        if self.types:
            combo.SelectedIndex = 0
            self.vm.set_type_key(self.types[0].get(u"key", u"auto"))

    def _brush(self, key):
        try:
            return Application.Current.Resources[key]
        except Exception:
            return None

    def _wire(self):
        find = self.window.FindName
        guarded = pp_wpf.guard(self.vm.set_error)

        @guarded
        def on_type(sender, args):
            index = sender.SelectedIndex

            if 0 <= index < len(self.types):
                self.vm.set_type_key(self.types[index].get(u"key", u"auto"))

        @guarded
        def on_run(sender, args):
            self._accept()

        @guarded
        def on_cancel(sender, args):
            self.window.Close()

        find("CmbType").SelectionChanged += on_type
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


def ask_options(rows, types, sets_count, walls_count, reject_count=0):
    u"""Показать окно. Возврат: словарь настроек или None, если отменили."""
    return WallMergeWindow(
        rows, types, sets_count, walls_count, reject_count
    ).show()

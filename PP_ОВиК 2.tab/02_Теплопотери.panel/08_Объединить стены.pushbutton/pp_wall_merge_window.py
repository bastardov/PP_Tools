# -*- coding: utf-8 -*-
u"""Окно инструмента «Объединить стены» (панель «Теплопотери»).

Окно ничего не знает про Revit: скрипт разбирает выделение сам и передаёт сюда
готовые строки разбора и список типов. Галочка «Переносить проёмы» меняет сам
разбор, поэтому окно умеет попросить скрипт пересчитать его — через колбэк
`replan(move_inserts)`, который отдаёт новый словарь того же вида.

    from pp_wall_merge_window import ask_options

    opts = ask_options(replan(False), replan)

    if opts is None:
        ...                      # пользователь закрыл окно
    else:
        opts[u"type_key"]        # u"auto" либо строка с id типа стены
        opts[u"move_inserts"]    # bool

Словарь разбора: rows, types, sets_count, walls_count, reject_count.
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
    u"""Состояние окна: считает скрипт, окно только показывает."""

    def __init__(self, data):
        pp_wpf.Notifier.__init__(self)

        self.move_inserts = False
        self.type_key = u"auto"

        self._status = u""
        self._status_error = False

        self.apply(data)

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

        text = u"Из {} {} получится {} {}.".format(
            self.walls_count, plural_walls(self.walls_count),
            self.sets_count, plural_walls(self.sets_count)
        )

        if self.move_inserts:
            text += u" Окна и двери будут вставлены заново."

        return text

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

    # -- изменения ---------------------------------------------------
    def apply(self, data):
        u"""Принять свежий разбор от скрипта."""
        self.sets_count = int(data.get(u"sets_count", 0))
        self.walls_count = int(data.get(u"walls_count", 0))
        self.reject_count = int(data.get(u"reject_count", 0))

        self._status = self._build_status()
        self._status_error = False

        self.notify(u"Status", u"StatusBrush", u"ResultText",
                    u"ResultBrush", u"IsValid")

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

    def set_type_key(self, key):
        self.type_key = key

    def set_move_inserts(self, value):
        self.move_inserts = bool(value)
        self.notify(u"ResultText")

    def set_error(self, message):
        self._status = unicode(message or u"")
        self._status_error = True
        self.notify(u"Status", u"StatusBrush")

    def result(self):
        return {
            u"type_key": self.type_key,
            u"move_inserts": self.move_inserts
        }


class WallMergeWindow(object):
    u"""Загрузка разметки, подписки, модальный показ."""

    def __init__(self, data, replan):
        xaml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            u"ui.xaml"
        )

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = WallMergeVM(data)
        self.replan = replan
        self.types = []
        self.window.DataContext = self.vm
        self.accepted = False

        self._show(data)
        self._wire()
        self._select_type()

        pp_wpf.set_owner(self.window)
        pp_wpf.fit_to_screen(self.window)

    # -- наполнение --------------------------------------------------
    def _show(self, data):
        self._fill_plan(data.get(u"rows") or [])
        self._fill_types(data.get(u"types") or [])

    def _fill_plan(self, rows):
        lst = self.window.FindName("LstPlan")
        lst.Items.Clear()

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

        empty = self.window.FindName("TxtEmpty")

        lst.Visibility = Visibility.Collapsed if not rows else Visibility.Visible
        empty.Visibility = Visibility.Visible if not rows else Visibility.Collapsed

    def _fill_types(self, types):
        self.types = list(types)

        combo = self.window.FindName("CmbType")
        combo.Items.Clear()

        for item in self.types:
            combo.Items.Add(item.get(u"title", u""))

        combo.IsEnabled = len(self.types) > 1

    def _select_type(self, key=None):
        u"""Начальный выбор — отдельным шагом после подписок (см. UI.md)."""
        combo = self.window.FindName("CmbType")

        if not self.types:
            self.vm.set_type_key(u"auto")
            return

        index = 0

        if key is not None:
            for position, item in enumerate(self.types):
                if item.get(u"key") == key:
                    index = position
                    break

        combo.SelectedIndex = index
        self.vm.set_type_key(self.types[index].get(u"key", u"auto"))

    def _brush(self, key):
        try:
            return Application.Current.Resources[key]
        except Exception:
            return None

    # -- подписки ----------------------------------------------------
    def _wire(self):
        find = self.window.FindName
        guarded = pp_wpf.guard(self.vm.set_error)

        @guarded
        def on_type(sender, args):
            index = sender.SelectedIndex

            if 0 <= index < len(self.types):
                self.vm.set_type_key(self.types[index].get(u"key", u"auto"))

        @guarded
        def on_inserts(sender, args):
            self.vm.set_move_inserts(sender.IsChecked)
            self._refresh()

        @guarded
        def on_run(sender, args):
            self._accept()

        @guarded
        def on_cancel(sender, args):
            self.window.Close()

        find("CmbType").SelectionChanged += on_type

        check = find("ChkMoveInserts")
        check.Checked += on_inserts
        check.Unchecked += on_inserts

        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(
            self.window,
            on_accept=self._accept,
            on_cancel=self.window.Close
        )

    def _refresh(self):
        u"""Галочка меняет сам разбор — просим скрипт пересчитать."""
        if self.replan is None:
            return

        keep = self.vm.type_key
        data = self.replan(self.vm.move_inserts)

        self._show(data)
        self.vm.apply(data)
        self._select_type(keep)

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


def ask_options(data, replan=None):
    u"""Показать окно. Возврат: словарь настроек или None, если отменили."""
    return WallMergeWindow(data, replan).show()

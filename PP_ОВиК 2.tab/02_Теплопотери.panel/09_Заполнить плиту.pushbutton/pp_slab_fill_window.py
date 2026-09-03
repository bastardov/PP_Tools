# -*- coding: utf-8 -*-
u"""Окно инструмента «Заполнить плиту» (панель «Теплопотери»).

Окно ничего не знает про Revit: разбор делает скрипт и отдаёт сюда готовые
строки. Любая галочка и порог меняют сам разбор, поэтому окно просит скрипт
пересчитать его колбэком `replan(options)`.

    from pp_slab_fill_window import ask_options

    opts = ask_options(start_options, replan)

    if opts is None:
        ...                      # пользователь закрыл окно
    else:
        opts[u"by_column"]       # bool
        opts[u"by_size"]         # bool
        opts[u"max_mm"]          # float
        opts[u"fill_holes"]      # bool

Словарь разбора от `replan`: rows, pockets_count, slabs_count, reject_count.
rows — [{u"text": u"Перекрытия · Плита 200 · id 123 · 2 выреза", u"bad": False}, …]
"""

import os

import pp_wpf

from System.Windows import Application, Visibility, TextWrapping
from System.Windows.Controls import TextBlock


DEFAULT_MAX_MM = 600.0


def _plural(count, one, few, many):
    count = abs(int(count))

    if count % 10 == 1 and count % 100 != 11:
        return one

    if count % 10 in (2, 3, 4) and not (11 <= count % 100 <= 14):
        return few

    return many


def plural_pockets(count):
    return _plural(count, u"вырез", u"выреза", u"вырезов")


def plural_slabs_in(count):
    return _plural(count, u"плите", u"плитах", u"плитах")


def parse_mm(text):
    u"""Число из поля: запятая тоже считается десятичным разделителем."""
    value = unicode(text or u"").strip().replace(u",", u".")

    if not value:
        return None

    try:
        number = float(value)
    except ValueError:
        return None

    if number <= 0.0:
        return None

    return number


class SlabFillVM(pp_wpf.Notifier):
    u"""Состояние окна: условия поиска и последний разбор от скрипта."""

    def __init__(self, start):
        pp_wpf.Notifier.__init__(self)

        start = start or {}

        self.by_column = bool(start.get(u"by_column", True))
        self.by_size = bool(start.get(u"by_size", True))
        self.fill_holes = bool(start.get(u"fill_holes", True))
        self.max_mm = float(start.get(u"max_mm") or DEFAULT_MAX_MM)
        self.size_text = u"{:.0f}".format(self.max_mm)

        self.pockets_count = 0
        self.slabs_count = 0
        self.reject_count = 0

        self._status = u""
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
        if self.size_error():
            return u"Укажите размер выемки в миллиметрах — например 600."

        if not self.by_column and not self.by_size:
            return u"Отмечено ни одного условия: инструменту нечего искать."

        if not self.pockets_count:
            return u"Заполнять нечего: в выбранных плитах таких вырезов нет."

        return u"Будет заполнено {} {} в {} {}.".format(
            self.pockets_count, plural_pockets(self.pockets_count),
            self.slabs_count, plural_slabs_in(self.slabs_count)
        )

    @property
    def ResultBrush(self):
        return self._brush(u"Ink" if self.IsValid else u"Muted")

    @property
    def IsValid(self):
        if self.size_error():
            return False

        return self.pockets_count > 0

    def _brush(self, key):
        try:
            return Application.Current.Resources[key]
        except Exception:
            return None

    # -- изменения ---------------------------------------------------
    def size_error(self):
        return self.by_size and parse_mm(self.size_text) is None

    def options(self):
        value = parse_mm(self.size_text)

        return {
            u"by_column": self.by_column,
            u"by_size": self.by_size,
            u"max_mm": value if value is not None else self.max_mm,
            u"fill_holes": self.fill_holes
        }

    def apply(self, data):
        u"""Принять свежий разбор от скрипта."""
        data = data or {}

        self.pockets_count = int(data.get(u"pockets_count", 0))
        self.slabs_count = int(data.get(u"slabs_count", 0))
        self.reject_count = int(data.get(u"reject_count", 0))

        value = parse_mm(self.size_text)

        if value is not None:
            self.max_mm = value

        self._status = self._build_status()
        self._status_error = False

        self.refresh()

    def refresh(self):
        self.notify(u"Status", u"StatusBrush", u"ResultText",
                    u"ResultBrush", u"IsValid")

    def _build_status(self):
        if self.size_error():
            return u"Размер выемки должен быть числом больше нуля."

        if not self.by_column and not self.by_size:
            return u"Отметьте хотя бы одно условие поиска."

        parts = []

        if self.pockets_count:
            parts.append(u"{} {} в {} {}".format(
                self.pockets_count, plural_pockets(self.pockets_count),
                self.slabs_count, plural_slabs_in(self.slabs_count)
            ))
        else:
            parts.append(u"вырезов не нашлось")

        if self.reject_count:
            parts.append(u"не получится: {}".format(self.reject_count))

        return u" · ".join(parts)

    def set_error(self, message):
        self._status = unicode(message or u"")
        self._status_error = True
        self.notify(u"Status", u"StatusBrush")

    def result(self):
        return self.options()


class SlabFillWindow(object):
    u"""Загрузка разметки, подписки, модальный показ."""

    def __init__(self, start, replan):
        xaml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            u"ui.xaml"
        )

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = SlabFillVM(start)
        self.replan = replan
        self.window.DataContext = self.vm
        self.accepted = False
        self.silent = False

        self._fill_controls()
        self._wire()
        self._refresh()

        pp_wpf.set_owner(self.window)
        pp_wpf.fit_to_screen(self.window)

    # -- наполнение --------------------------------------------------
    def _fill_controls(self):
        find = self.window.FindName

        self.silent = True
        find("ChkColumns").IsChecked = self.vm.by_column
        find("ChkSize").IsChecked = self.vm.by_size
        find("ChkHoles").IsChecked = self.vm.fill_holes
        find("TxtSize").Text = self.vm.size_text
        self.silent = False

    def _fill_plan(self, rows):
        rows = rows or []

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
        def on_columns(sender, args):
            if self.silent:
                return

            self.vm.by_column = bool(sender.IsChecked)
            self._refresh()

        @guarded
        def on_size(sender, args):
            if self.silent:
                return

            self.vm.by_size = bool(sender.IsChecked)
            self._refresh()

        @guarded
        def on_holes(sender, args):
            if self.silent:
                return

            self.vm.fill_holes = bool(sender.IsChecked)
            self._refresh()

        @guarded
        def on_size_text(sender, args):
            if self.silent:
                return

            self.vm.size_text = sender.Text
            self._refresh()

        @guarded
        def on_run(sender, args):
            self._accept()

        @guarded
        def on_cancel(sender, args):
            self.window.Close()

        columns = find("ChkColumns")
        columns.Checked += on_columns
        columns.Unchecked += on_columns

        size = find("ChkSize")
        size.Checked += on_size
        size.Unchecked += on_size

        holes = find("ChkHoles")
        holes.Checked += on_holes
        holes.Unchecked += on_holes

        find("TxtSize").TextChanged += on_size_text

        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(
            self.window,
            on_accept=self._accept,
            on_cancel=self.window.Close
        )

    # -- пересчёт ----------------------------------------------------
    def _refresh(self):
        if self.vm.size_error() or (not self.vm.by_column and not self.vm.by_size):
            self._fill_plan([])
            self.vm.apply({})
            return

        data = self.replan(self.vm.options())

        self._fill_plan(data.get(u"rows"))
        self.vm.apply(data)

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


def ask_options(start, replan):
    u"""Показать окно. Возврат: словарь условий или None, если отменили."""
    return SlabFillWindow(start, replan).show()

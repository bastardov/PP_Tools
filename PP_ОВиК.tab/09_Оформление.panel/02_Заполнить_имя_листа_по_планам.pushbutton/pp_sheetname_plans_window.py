# -*- coding: utf-8 -*-
u"""Окно инструмента «Заполнить имя листа по планам».

Таблица листов: галочка, лист, старое имя, редактируемое новое и примечание.
Строки строятся кодом — готовой таблицы с правкой в строке в теме нет, а
DataGrid тянул бы за собой отдельный шаблон.
"""

import os

from System.Windows import (
    Application, GridLength, GridUnitType, TextWrapping, Thickness,
    VerticalAlignment
)
from System.Windows.Controls import (
    CheckBox, ColumnDefinition, Grid, ListBoxItem, TextBlock, TextBox
)

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF


# Ширины колонок повторяют шапку в разметке
COLUMNS = [28, 200, 0, 0, 150]


def style(key):
    try:
        return Application.Current.Resources[key]
    except Exception:
        return None


def brush(key):
    try:
        return Application.Current.Resources[key]
    except Exception:
        return None


def plural(count, forms):
    count = abs(int(count))

    if count % 10 == 1 and count % 100 != 11:
        return forms[0]

    if 2 <= count % 10 <= 4 and not (12 <= count % 100 <= 14):
        return forms[1]

    return forms[2]


# ======================================================================
#  ViewModel
# ======================================================================

class PlansVM(pp_wpf.Notifier):

    def __init__(self, rows):
        pp_wpf.Notifier.__init__(self)

        self.rows = list(rows or [])

        self._ready = 0

        self._status = u""
        self._status_error = False

    @property
    def HasNoRows(self):
        return len(self.rows) == 0

    @property
    def Status(self):
        return self._status

    @property
    def StatusBrush(self):
        return brush(u"Hot" if self._status_error else u"Muted")

    @property
    def IsValid(self):
        return self._ready > 0

    def set_ready(self, count):
        self._ready = count
        self.revalidate()

    def revalidate(self):
        total = len(self.rows)

        if total == 0:
            self.set_status(u"Нет листов, для которых можно предложить имя.", True)

        elif self._ready == 0:
            self.set_status(
                u"Отметьте хотя бы один лист и оставьте имя непустым.", True)

        else:
            self.set_status(
                u"Будет переименовано {} {} из {}.".format(
                    self._ready,
                    plural(self._ready, (u"лист", u"листа", u"листов")),
                    total
                ),
                False
            )

        self.notify(u"IsValid")

    def set_status(self, text, is_error=False):
        self._status = text
        self._status_error = bool(is_error)
        self.notify(u"Status", u"StatusBrush")


# ======================================================================
#  Окно
# ======================================================================

class PlansWindow(object):

    def __init__(self, script_dir, rows):
        self.window = pp_wpf.load_window_file(os.path.join(script_dir, u"ui.xaml"))

        self.vm = PlansVM(rows)
        self.window.DataContext = self.vm

        self.accepted = False

        # [(данные строки, флажок, поле имени), ...]
        self.controls = []

        self._build_rows()
        self._wire()

        pp_wpf.set_owner(self.window)

        self._refresh_ready()

    # ---------- построение -------------------------------------------------

    def _build_rows(self):
        box = self.window.FindName("LstRows")

        body = style(u"Body")
        caption = style(u"Caption")
        check_style = style(u"PP.CheckBox")
        field_style = style(u"TextBox.Field")

        for data in self.vm.rows:
            grid = Grid()

            for width in COLUMNS:
                column = ColumnDefinition()
                column.Width = (GridLength(1, GridUnitType.Star) if not width
                                else GridLength(width, GridUnitType.Pixel))
                grid.ColumnDefinitions.Add(column)

            check = CheckBox()
            check.IsChecked = bool(data.get("include"))
            check.VerticalAlignment = VerticalAlignment.Center

            if check_style is not None:
                check.Style = check_style

            sheet = TextBlock()
            sheet.Text = unicode(data.get("sheet_label") or u"")
            sheet.TextWrapping = TextWrapping.Wrap
            sheet.Margin = Thickness(0, 0, 8, 0)
            sheet.VerticalAlignment = VerticalAlignment.Center
            sheet.Style = body

            old = TextBlock()
            old.Text = unicode(data.get("old") or u"")
            old.TextWrapping = TextWrapping.Wrap
            old.Margin = Thickness(0, 0, 8, 0)
            old.VerticalAlignment = VerticalAlignment.Center
            old.Style = caption

            new = TextBox()
            new.Text = unicode(data.get("new") or u"")
            new.Margin = Thickness(0, 0, 8, 0)
            new.VerticalAlignment = VerticalAlignment.Center

            if field_style is not None:
                new.Style = field_style

            warn = TextBlock()
            warn.Text = unicode(data.get("warn") or u"")
            warn.TextWrapping = TextWrapping.Wrap
            warn.VerticalAlignment = VerticalAlignment.Center
            warn.Style = caption

            if data.get("warn"):
                warn.Foreground = brush(u"Hot")

            for index, control in enumerate((check, sheet, old, new, warn)):
                Grid.SetColumn(control, index)
                grid.Children.Add(control)

            item = ListBoxItem()
            item.Content = grid

            box.Items.Add(item)

            self.controls.append((data, check, new))

    # ---------- подписки ------------------------------------------------------

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        @guard
        def on_row_changed(sender, args):
            self._refresh_ready()

        for _data, check, new in self.controls:
            check.Checked += on_row_changed
            check.Unchecked += on_row_changed
            new.TextChanged += on_row_changed

        @guard
        def on_all(sender, args):
            self._set_all(True)

        @guard
        def on_none(sender, args):
            self._set_all(False)

        @guard
        def on_reset(sender, args):
            self._reset_names()

        find("BtnAll").Click += on_all
        find("BtnNone").Click += on_none
        find("BtnReset").Click += on_reset

        @guard
        def on_run(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    # ---------- действия --------------------------------------------------------

    def _set_all(self, value):
        for _data, check, _new in self.controls:
            check.IsChecked = value

    def _reset_names(self):
        for data, _check, new in self.controls:
            new.Text = unicode(data.get("new") or u"")

        self.vm.set_status(u"Имена возвращены к предложенным.")

    def selected(self):
        u"""Возврат: [(лист, новое имя), ...] по отмеченным непустым строкам."""
        result = []

        for data, check, new in self.controls:
            if not check.IsChecked:
                continue

            name = unicode(new.Text or u"").strip()

            if not name:
                continue

            result.append((data["sheet"], name))

        return result

    def _refresh_ready(self):
        self.vm.set_ready(len(self.selected()))

    def _accept(self):
        if not self.vm.IsValid:
            return

        self.accepted = True
        self.window.DialogResult = True

    def _fail(self, message):
        self.vm.set_status(message, True)

    def show(self):
        self.window.ShowDialog()

        if not self.accepted:
            return None

        return self.selected()


def ask(script_dir, rows):
    return PlansWindow(script_dir, rows).show()

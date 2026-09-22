# -*- coding: utf-8 -*-
u"""Окно «Аудит модели».

Немодальное: работа с моделью идёт только через ExternalEvent, который живёт
в script.py и передаётся сюда сервисом ``select``.

Каркас похож на отчёт проверок, но набор действий другой — нет подкраски и
настроек запуска, зато есть экспорт и обновление, — поэтому окно своё, а не
надстройка над pp_check_windows.
"""

import os

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

from System import TimeSpan
from System.Collections.ObjectModel import ObservableCollection
from System.Windows import (
    Application, GridLength, GridUnitType, TextWrapping, Thickness, VerticalAlignment
)
from System.Windows.Controls import ColumnDefinition, Grid, ListBoxItem, TextBlock
from System.Windows.Threading import DispatcherTimer

from Microsoft.Win32 import SaveFileDialog


# Колонки таблицы групп: ширины совпадают с шапкой в разметке
COLUMNS = [
    (u"desc", 0),
    (u"count", 70),
    (u"newest_first_seen", 130),
]

CSV_HEADER = [u"Предупреждение", u"Серьёзность", u"Количество",
              u"Впервые замечено", u"Элементы (id)"]


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


def csv_cell(text):
    u"""Точка с запятой и кавычки экранируются — формат тот же, что был."""
    value = unicode(text if text is not None else u"")
    value = value.replace(u"\r", u" ").replace(u"\n", u" ")

    if u";" in value or u'"' in value:
        value = u'"' + value.replace(u'"', u'""') + u'"'

    return value


# ======================================================================
#  ViewModel
# ======================================================================

class AuditVM(pp_wpf.Notifier):

    def __init__(self, groups):
        pp_wpf.Notifier.__init__(self)

        self.all_groups = list(groups or [])
        self.groups = list(self.all_groups)

        self._ids = ObservableCollection[object]()

        # Строки списка и сами id идут параллельно: у пояснительной строки
        # («нет привязанных элементов») числа нет, поэтому в этой позиции None.
        self.id_values = []

        self._search = u""

        self._status = u""
        self._status_error = False

        self.total = 0

        for group in self.all_groups:
            self.total += group["count"]

        self.set_status(self._counter_text())

    # ---------- шапка ---------------------------------------------------

    @property
    def Headline(self):
        if self.total == 0:
            return u"Предупреждений нет"

        return u"Всего предупреждений: {}".format(self.total)

    @property
    def Summary(self):
        count = len(self.all_groups)

        return u"{} {}. Сначала показаны недавно замеченные.".format(
            count, plural(count, (u"тип", u"типа", u"типов"))
        )

    # ---------- группы -----------------------------------------------------

    @property
    def HasNoGroups(self):
        return len(self.groups) == 0

    @property
    def EmptyGroupsText(self):
        if self._search:
            return u"Ничего не найдено. Измените запрос или очистите поиск."

        return u"Предупреждений в модели не найдено."

    def set_search(self, text):
        self._search = unicode(text or u"").strip().lower()

        if self._search:
            needle = self._search
            self.groups = [group for group in self.all_groups
                           if needle in unicode(group["desc"]).lower()]
        else:
            self.groups = list(self.all_groups)

        self.notify(u"HasNoGroups", u"EmptyGroupsText")
        self.set_status(self._counter_text())

    def _counter_text(self):
        if self._search:
            return u"Показано групп: {} из {}.".format(
                len(self.groups), len(self.all_groups))

        return u"Групп предупреждений: {}.".format(len(self.groups))

    # ---------- элементы группы ---------------------------------------------

    @property
    def Ids(self):
        return self._ids

    @property
    def HasNoIds(self):
        return self._ids.Count == 0

    def fill_ids(self, group):
        self._ids.Clear()
        self.id_values = []

        if group is not None:
            if not group["ids"]:
                self._ids.Add(u"У этого предупреждения нет привязанных элементов.")
                self.id_values.append(None)
            else:
                for int_id in group["ids"]:
                    self._ids.Add(u"id {}".format(int_id))
                    self.id_values.append(int_id)

        self.notify(u"HasNoIds")

    def id_at(self, index):
        if 0 <= index < len(self.id_values):
            return self.id_values[index]

        return None

    # ---------- статус --------------------------------------------------------

    @property
    def Status(self):
        return self._status

    @property
    def StatusBrush(self):
        return brush(u"Hot" if self._status_error else u"Muted")

    def set_status(self, text, is_error=False):
        self._status = text
        self._status_error = bool(is_error)
        self.notify(u"Status", u"StatusBrush")

    # ---------- выгрузка --------------------------------------------------------

    def csv_text(self):
        u"""Выгружается то, что видно на экране: с учётом поиска."""
        lines = [u";".join(CSV_HEADER)]

        for group in self.groups:
            ids_text = u" ".join(unicode(int_id) for int_id in group["ids"])

            lines.append(u";".join([
                csv_cell(group["desc"]),
                csv_cell(group["sev"]),
                csv_cell(group["count"]),
                csv_cell(group["newest_first_seen"]),
                csv_cell(ids_text),
            ]))

        return u"\r\n".join(lines)


# ======================================================================
#  Окно
# ======================================================================

class AuditWindow(object):

    def __init__(self, script_dir, groups, services):
        self.window = pp_wpf.load_window_file(os.path.join(script_dir, u"ui.xaml"))

        self.vm = AuditVM(groups)
        self.window.DataContext = self.vm

        # services: select(ids, report), refresh(), on_closed(window)
        self.services = services

        # Выделение поднимает окно Revit, поэтому событие откладываем на такт —
        # иначе фокус возвращается сюда раньше, чем Revit успевает отработать.
        self.timer = DispatcherTimer()
        self.timer.Interval = TimeSpan.FromMilliseconds(150)
        self.pending_ids = []

        self._wire()
        self._render_groups()

        pp_wpf.set_owner(self.window)

    # ---------- отрисовка ---------------------------------------------------

    def _render_groups(self):
        box = self.window.FindName("LstGroups")

        box.Items.Clear()

        body = style(u"Body")
        caption = style(u"Caption")

        for group in self.vm.groups:
            grid = Grid()

            for _key, width in COLUMNS:
                column = ColumnDefinition()
                column.Width = (GridLength(1, GridUnitType.Star) if not width
                                else GridLength(width, GridUnitType.Pixel))
                grid.ColumnDefinitions.Add(column)

            desc = TextBlock()
            desc.Text = unicode(group["desc"])
            desc.TextWrapping = TextWrapping.Wrap
            desc.Margin = Thickness(0, 0, 8, 0)
            desc.Style = body

            count = TextBlock()
            count.Text = unicode(group["count"])
            count.VerticalAlignment = VerticalAlignment.Top
            count.Margin = Thickness(0, 0, 8, 0)
            count.Style = body
            count.Foreground = brush(u"Hot")

            seen = TextBlock()
            seen.Text = unicode(group["newest_first_seen"])
            seen.VerticalAlignment = VerticalAlignment.Top
            seen.Style = caption

            for index, block in enumerate((desc, count, seen)):
                Grid.SetColumn(block, index)
                grid.Children.Add(block)

            item = ListBoxItem()
            item.Content = grid
            item.Tag = group

            box.Items.Add(item)

        if box.Items.Count > 0:
            box.SelectedIndex = 0
        else:
            self.vm.fill_ids(None)

    # ---------- подписки ------------------------------------------------------

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        @guard
        def on_group_changed(sender, args):
            self.vm.fill_ids(self.selected_group())

        find("LstGroups").SelectionChanged += on_group_changed

        @guard
        def on_search(sender, args):
            self.vm.set_search(sender.Text)
            self._render_groups()

        find("TxtSearch").TextChanged += on_search

        @guard
        def on_id_double_click(sender, args):
            self.select_current_id()

        find("LstIds").MouseDoubleClick += on_id_double_click

        @guard
        def on_select_one(sender, args):
            self.select_current_id()

        find("BtnSelectOne").Click += on_select_one

        @guard
        def on_select_group(sender, args):
            group = self.selected_group()

            if group is None:
                self.vm.set_status(u"Сначала выберите предупреждение слева.", True)
                return

            if not group["ids"]:
                self.vm.set_status(u"У этого предупреждения нет привязанных элементов.", True)
                return

            self.request_select(group["ids"])

        find("BtnSelectGroup").Click += on_select_group

        @guard
        def on_export(sender, args):
            self._export()

        find("BtnExport").Click += on_export

        @guard
        def on_refresh(sender, args):
            self.window.Close()
            self.services[u"refresh"]()

        find("BtnRefresh").Click += on_refresh

        @guard
        def on_close(sender, args):
            self.window.Close()

        find("BtnClose").Click += on_close

        @guard
        def on_tick(sender, args):
            self.timer.Stop()
            self.services[u"select"](self.pending_ids, self.vm.set_status)

        self.timer.Tick += on_tick

        def on_closed(sender, args):
            try:
                self.timer.Stop()
            except Exception:
                pass

            try:
                self.services[u"on_closed"](self)
            except Exception:
                pass

        self.window.Closed += on_closed

    # ---------- действия --------------------------------------------------------

    def selected_group(self):
        item = self.window.FindName("LstGroups").SelectedItem

        if item is None:
            return None

        return item.Tag

    def select_current_id(self):
        index = self.window.FindName("LstIds").SelectedIndex
        int_id = self.vm.id_at(index)

        if int_id is None:
            self.vm.set_status(u"Эта строка пояснительная — выделять нечего.", True)
            return

        self.request_select([int_id])

    def request_select(self, element_ids):
        self.pending_ids = list(element_ids or [])
        self.timer.Stop()
        self.timer.Start()

        self.vm.set_status(
            u"Выделяю в модели: {} {}.".format(
                len(self.pending_ids),
                plural(len(self.pending_ids), (u"элемент", u"элемента", u"элементов"))
            )
        )

    def _export(self):
        if not self.vm.groups:
            self.vm.set_status(u"Нечего выгружать: в таблице нет групп.", True)
            return

        dialog = SaveFileDialog()
        dialog.Title = u"Экспорт предупреждений в CSV"
        dialog.Filter = u"CSV-файл (*.csv)|*.csv"
        dialog.FileName = u"audit_preduprezhdeniya.csv"

        if dialog.ShowDialog() != True:
            return

        handle = open(dialog.FileName, "wb")

        try:
            # BOM: иначе Excel открывает кириллицу кракозябрами
            handle.write(self.vm.csv_text().encode("utf-8-sig"))
        finally:
            handle.close()

        self.vm.set_status(u"Выгружено групп: {} в {}".format(
            len(self.vm.groups), os.path.basename(dialog.FileName)))

    def _fail(self, message):
        self.vm.set_status(message, True)

    def show(self):
        self.window.Show()
        self.window.Activate()


def show(script_dir, groups, services):
    u"""Открыть немодальное окно. Возвращает окно — держать ссылку!"""
    window = AuditWindow(script_dir, groups, services)
    window.show()
    return window

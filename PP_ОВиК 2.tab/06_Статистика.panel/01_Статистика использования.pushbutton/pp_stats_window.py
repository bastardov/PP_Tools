# -*- coding: utf-8 -*-
u"""Окно «Статистика использования».

Таблица собирается кодом: набор колонок зависит от выбранной группировки,
поэтому фиксированной разметки под неё нет. Строки хранятся обычным списком
python, а в ListBox уезжают уже отфильтрованными и отсортированными.

Заголовок колонки — кортеж (подпись, ширина) или (подпись, ширина, вид).
Вид «spark» рисует ячейку моноширинным шрифтом: столбики гистограммы иначе
разъезжаются по ширине и картинка перестаёт читаться.
"""

import os

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

import clr

clr.AddReference("System")

from System.Diagnostics import Process
from System.Windows import (
    Application, GridLength, GridUnitType, TextWrapping, Thickness, VerticalAlignment
)
from System.Windows.Controls import (
    ColumnDefinition, Grid, ListBoxItem, TextBlock
)
from System.Windows.Input import Cursors
from System.Windows.Media import Brushes, FontFamily

from Microsoft.Win32 import SaveFileDialog


ARROW_UP = u" ↑"
ARROW_DOWN = u" ↓"

# Шрифт для колонки-гистограммы: столбики должны стоять ровной сеткой
SPARK_FONT = u"Consolas, Courier New"


def style(key):
    try:
        return Application.Current.Resources[key]
    except Exception:
        return None


def header_kind(header):
    u"""Необязательный третий элемент заголовка — вид колонки (напр. «spark»)."""
    if header is not None and len(header) > 2:
        return header[2]
    return None


def cell_key(value):
    u"""Колонка однородна, поэтому хватает числа или строки в нижнем регистре."""
    try:
        return (0, float(value), u"")
    except (TypeError, ValueError):
        return (1, 0.0, unicode(value).lower())


# ======================================================================
#  ViewModel
# ======================================================================

class StatsVM(pp_wpf.Notifier):

    def __init__(self, records, folder, aggregate, overall_summary,
                 models=None, filter_by_model=None):
        pp_wpf.Notifier.__init__(self)

        self.records = records
        self.folder = folder

        # Агрегация остаётся в script.py: это логика инструмента, не окна
        self.aggregate = aggregate
        self.overall_summary = overall_summary

        # Список моделей и отбор по модели — тоже логика инструмента
        self.models = models or []
        self.filter_by_model = filter_by_model

        self._model_index = 0
        self._active = records
        self._overall = overall_summary(records)
        self._note = u""

        self._group_by = 0
        self._search = u""

        self._sort_index = None
        self._sort_desc = True

        self.headers = []
        self.all_rows = []
        self.rows = []

        self._status = u""
        self._status_error = False

        self.rebuild()

    # ---------- шапка -------------------------------------------------

    @property
    def Overall(self):
        return self._overall

    @property
    def Note(self):
        u"""Пояснение к режиму: что именно показано в таблице."""
        return self._note

    @property
    def HasNote(self):
        return bool(self._note)

    @property
    def FolderText(self):
        return u"Папка логов: {}".format(self.folder or u"—")

    @property
    def HasNoRows(self):
        return len(self.rows) == 0

    @property
    def EmptyText(self):
        if self._search:
            return u"Ничего не найдено. Измените запрос или очистите поиск."

        return u"Записей пока нет."

    # ---------- статус ---------------------------------------------------

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

    def set_status(self, text, is_error=False):
        self._status = text
        self._status_error = bool(is_error)
        self.notify(u"Status", u"StatusBrush")

    # ---------- команды ---------------------------------------------------

    @property
    def group_by(self):
        return self._group_by

    def set_group(self, index):
        if index == self._group_by:
            return

        self._group_by = index

        # Колонки поменялись — прежняя сортировка к ним не относится
        self._sort_index = None
        self._sort_desc = True

        self.rebuild()

    @property
    def model_index(self):
        return self._model_index

    def set_model(self, index):
        u"""Отбор по модели: пересчитывается и таблица, и строка итогов."""
        if index == self._model_index:
            return
        if not (0 <= index < len(self.models)):
            return

        self._model_index = index
        key = self.models[index][1]

        if self.filter_by_model is None:
            self._active = self.records
        else:
            self._active = self.filter_by_model(self.records, key)

        self._overall = self.overall_summary(self._active)
        self.notify(u"Overall")

        self.rebuild()

    def set_search(self, text):
        self._search = unicode(text or u"").strip().lower()
        self.apply_view()

    def sort_by(self, index):
        if self._sort_index == index:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_index = index
            self._sort_desc = True

        self.apply_view()

    def sort_state(self):
        return self._sort_index, self._sort_desc

    # ---------- пересборка -------------------------------------------------

    def rebuild(self):
        u"""Полный пересчёт: сменилась группировка или выбранная модель."""
        self.headers, self.all_rows, self._note = self.aggregate(
            self._active, self._group_by)
        self.notify(u"Note", u"HasNote")
        self.apply_view()

    def apply_view(self):
        u"""Фильтр и сортировка поверх уже посчитанных строк."""
        rows = self.all_rows

        if self._search:
            needle = self._search
            rows = [row for row in rows
                    if any(needle in unicode(cell).lower() for cell in row)]

        if self._sort_index is not None:
            index = self._sort_index
            rows = sorted(rows,
                          key=lambda row: cell_key(row[index]),
                          reverse=self._sort_desc)

        self.rows = rows

        self.notify(u"HasNoRows", u"EmptyText")

        if self._search:
            self.set_status(u"Показано строк: {} из {}.".format(
                len(rows), len(self.all_rows)))
        else:
            self.set_status(u"Строк: {}.".format(len(rows)))

    def csv_text(self):
        u"""Выгружается то, что видно на экране: с учётом поиска и сортировки."""
        lines = [u";".join([header[0] for header in self.headers])]

        for row in self.rows:
            lines.append(u";".join([unicode(cell) for cell in row]))

        return u"\r\n".join(lines)


# ======================================================================
#  Окно
# ======================================================================

class StatsWindow(object):

    def __init__(self, script_dir, records, folder, aggregate, overall_summary,
                 models=None, filter_by_model=None):
        self.window = pp_wpf.load_window_file(os.path.join(script_dir, u"ui.xaml"))

        self.vm = StatsVM(records, folder, aggregate, overall_summary,
                          models, filter_by_model)
        self.window.DataContext = self.vm

        self.header_blocks = []

        self._fill_controls()
        self._wire()
        self._render()

        pp_wpf.set_owner(self.window)

    # ---------- начальное состояние ----------------------------------------

    def _fill_controls(self):
        find = self.window.FindName

        find("ChipTool").IsChecked = True
        find("TxtSearch").Text = u""

        combo = find("CmbModel")
        combo.Items.Clear()

        for label, _key in self.vm.models:
            combo.Items.Add(label)

        if combo.Items.Count:
            combo.SelectedIndex = self.vm.model_index

    # ---------- отрисовка таблицы -------------------------------------------

    def _columns(self):
        u"""Первая колонка тянется, остальные фиксированы — как в шапке."""
        widths = []

        for index, header in enumerate(self.vm.headers):
            if index == 0:
                widths.append(GridLength(1, GridUnitType.Star))
            else:
                widths.append(GridLength(header[1], GridUnitType.Pixel))

        return widths

    def _render(self):
        self._render_header()
        self._render_rows()

    def _render_header(self):
        grid = self.window.FindName("PanelHeader")

        grid.Children.Clear()
        grid.ColumnDefinitions.Clear()
        self.header_blocks = []

        for width in self._columns():
            column = ColumnDefinition()
            column.Width = width
            grid.ColumnDefinitions.Add(column)

        sort_index, sort_desc = self.vm.sort_state()
        label_style = style(u"FieldLabel")

        for index, header in enumerate(self.vm.headers):
            block = TextBlock()
            block.Text = header[0].upper()

            if index == sort_index:
                block.Text += ARROW_DOWN if sort_desc else ARROW_UP

            if label_style is not None:
                block.Style = label_style

            # Прозрачный фон нужен, иначе TextBlock не ловит клик
            block.Background = Brushes.Transparent
            block.Cursor = Cursors.Hand
            block.Margin = Thickness(0, 0, 8, 0)
            block.ToolTip = u"Сортировать по колонке «{}»".format(header[0])

            Grid.SetColumn(block, index)
            grid.Children.Add(block)

            self.header_blocks.append(block)

        self._wire_header()

    def _wire_header(self):
        guard = pp_wpf.guard(self._fail)

        # Фабрика обработчиков: общий цикл с lambda отдал бы всем заголовкам
        # последний индекс.
        def make_handler(index):
            @guard
            def handler(sender, args):
                self.vm.sort_by(index)
                self._render()

            return handler

        for index in range(len(self.header_blocks)):
            self.header_blocks[index].MouseLeftButtonUp += make_handler(index)

    def _render_rows(self):
        box = self.window.FindName("LstRows")

        box.Items.Clear()

        body = style(u"Body")
        caption = style(u"Caption")
        widths = self._columns()

        for row in self.vm.rows:
            grid = Grid()

            for width in widths:
                column = ColumnDefinition()
                column.Width = width
                grid.ColumnDefinitions.Add(column)

            for index, cell in enumerate(row):
                block = TextBlock()
                block.Text = unicode(cell)
                block.VerticalAlignment = VerticalAlignment.Center
                block.Margin = Thickness(0, 0, 8, 0)

                kind = None
                if index < len(self.vm.headers):
                    kind = header_kind(self.vm.headers[index])

                if kind == u"spark":
                    # Гистограмма: обычный цвет текста, но ровная сетка столбиков
                    block.Style = body
                    block.FontFamily = FontFamily(SPARK_FONT)
                elif index == 0:
                    block.TextWrapping = TextWrapping.Wrap
                    block.Style = body
                elif index == len(row) - 1 or index == len(row) - 2:
                    # Даты — служебная информация, приглушаем
                    block.Style = caption
                else:
                    block.Style = body

                Grid.SetColumn(block, index)
                grid.Children.Add(block)

            item = ListBoxItem()
            item.Content = grid

            box.Items.Add(item)

    # ---------- подписки -----------------------------------------------------

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        def make_group_handler(index):
            @guard
            def handler(sender, args):
                self.vm.set_group(index)
                self._render()

            return handler

        for index, name in enumerate(("ChipTool", "ChipUser", "ChipBoth",
                                      "ChipPhase")):
            find(name).Checked += make_group_handler(index)

        @guard
        def on_model(sender, args):
            self.vm.set_model(sender.SelectedIndex)
            self._render()

        find("CmbModel").SelectionChanged += on_model

        @guard
        def on_search(sender, args):
            self.vm.set_search(sender.Text)
            self._render_rows()

        find("TxtSearch").TextChanged += on_search

        @guard
        def on_export(sender, args):
            self._export()

        @guard
        def on_folder(sender, args):
            self._open_folder()

        @guard
        def on_close(sender, args):
            self.window.Close()

        find("BtnExport").Click += on_export
        find("BtnFolder").Click += on_folder
        find("BtnClose").Click += on_close

        pp_wpf.wire_keys(self.window, on_accept=self.window.Close)

    # ---------- действия ------------------------------------------------------

    def _export(self):
        if not self.vm.rows:
            self.vm.set_status(u"Нечего выгружать: в таблице нет строк.", True)
            return

        dialog = SaveFileDialog()
        dialog.Title = u"Экспорт статистики PP Tools"
        dialog.Filter = u"CSV-файл (*.csv)|*.csv"
        dialog.FileName = u"pp_usage_stats.csv"

        if dialog.ShowDialog() != True:
            return

        handle = open(dialog.FileName, "wb")

        try:
            # BOM: иначе Excel открывает кириллицу кракозябрами
            handle.write(self.vm.csv_text().encode("utf-8-sig"))
        finally:
            handle.close()

        self.vm.set_status(u"Выгружено строк: {} в {}".format(
            len(self.vm.rows), os.path.basename(dialog.FileName)))

    def _open_folder(self):
        if not self.vm.folder or not os.path.isdir(self.vm.folder):
            self.vm.set_status(u"Папка логов не найдена: {}".format(
                self.vm.folder or u"—"), True)
            return

        Process.Start(u"explorer.exe", u'"{}"'.format(self.vm.folder))

        self.vm.set_status(u"Папка открыта в проводнике.")

    def _fail(self, message):
        self.vm.set_status(message, True)

    def show(self):
        self.window.ShowDialog()


def show(script_dir, records, folder, aggregate, overall_summary,
         models=None, filter_by_model=None):
    StatsWindow(script_dir, records, folder, aggregate, overall_summary,
                models, filter_by_model).show()

# -*- coding: utf-8 -*-
u"""Общий каркас инструментов-проверок.

Три инструмента плагина устроены одинаково: окно со списком проверок и
настройками выбранной, потом немодальный отчёт, из которого можно выделить и
подкрасить проблемные элементы. Здесь этот каркас один на всех.

Окно настроек::

    setup = pp_check_windows.ask_setup({
        u"title":              u"Проверка Модели",
        u"subtitle":           u"Отметьте проверки и задайте параметры.",
        u"definitions":        get_check_definitions(),
        u"option_definitions": get_check_option_definitions(),
        u"config":             current_config,
        u"selected_keys":      saved_keys,
        u"last_selected_key":  saved_key,
        u"scope":              None,   # или u"model" / u"selection"
        u"selection_count":    0,
        u"run_label":          u"Проверить",
        u"param_provider":     provider,   # см. ниже, необязательно
    })

Возврат: словарь с ключами ``selected_keys``, ``config``, ``scope``,
``last_selected_key`` — или None, если пользователь отказался.

Опция с ``option_type=u"param"`` — это имя параметра: поле ввода плюс кнопка
«Выбрать…» (общее окно ``lib/pp_param_picker``). Список готовит инструмент::

    def provider(option_key, values):
        # values — текущие значения всех опций окна, в том числе категорий
        return pp_param_source.project_options(doc)

Без ключа ``param_provider`` кнопка не показывается и опция остаётся обычным
текстовым полем — старые вызовы работают как раньше.

Окно отчёта::

    pp_check_windows.show_results(results, {
        u"title":     u"Отчёт проверки модели",
        u"select":    do_select,     # (ids, report) -> None
        u"paint":     do_paint,      # (mode, ids, report) -> None
        u"restart":   run_tool_flow, # () -> None
        u"on_closed": on_closed,     # (window) -> None
    })

Работа с моделью — забота вызывающего скрипта: окно отчёта немодальное и
обращается к ``doc`` только через переданные обработчики ExternalEvent.
"""

import os

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

from System import TimeSpan
from System.Collections.ObjectModel import ObservableCollection
from System.Windows import (
    Application, GridLength, GridUnitType, TextWrapping, Thickness,
    VerticalAlignment, Visibility
)
from System.Windows.Controls import (
    Border, Button, CheckBox, ColumnDefinition, Grid, ListBoxItem,
    ScrollBarVisibility, StackPanel, TextBlock, TextBox
)
from System.Windows.Threading import DispatcherTimer

import pp_param_picker


SETUP_XAML = u"pp_check_setup.xaml"
REPORT_XAML = u"pp_check_report.xaml"

# Опции вида «Показывать то-то (да/нет)» рисуются галочкой, а не полем ввода.
BOOL_SUFFIX = u"(да/нет)"

YES = u"да"
NO = u"нет"

# Подписи окна выбора параметра (перебиваются ключом param_picker_subtitle)
DEFAULT_PARAM_SUBTITLE = (
    u"Параметры проекта, привязанные к категориям модели. Встроенные и "
    u"семейные параметры в список не попадают — их вписывают в поле вручную."
)
DEFAULT_PARAM_EMPTY = (
    u"Параметры не найдены. Измените поиск или закройте окно и впишите имя "
    u"параметра вручную."
)


def _lib_dir():
    return os.path.dirname(os.path.abspath(__file__))


def is_yes(raw_text):
    u"""Тот же набор значений, что понимают модули проверок."""
    value = unicode(raw_text or u"").strip().lower()
    return value in (YES, u"yes", u"1", u"true", u"истина", u"+")


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


def parse_category_value(text):
    u"""«walls, curtain» -> [walls, curtain]. Разделители: запятая, точка с запятой, слэш."""
    result = []
    seen = set()

    cleaned = unicode(text or u"").replace(u";", u",").replace(u"/", u",")

    for part in cleaned.split(u","):
        token = part.strip()

        if token and token not in seen:
            seen.add(token)
            result.append(token)

    return result


def plural(count, forms):
    count = abs(int(count))

    if count % 10 == 1 and count % 100 != 11:
        return forms[0]

    if 2 <= count % 10 <= 4 and not (12 <= count % 100 <= 14):
        return forms[1]

    return forms[2]


# ======================================================================
#  Окно настроек — ViewModel
# ======================================================================

class SetupVM(pp_wpf.Notifier):

    def __init__(self, config):
        pp_wpf.Notifier.__init__(self)

        self.config = config

        self.definitions = list(config.get(u"definitions") or [])
        self.option_definitions = list(config.get(u"option_definitions") or [])
        self.values = dict(config.get(u"config") or {})

        selected_keys = config.get(u"selected_keys") or []

        self._checked = [d.key in selected_keys for d in self.definitions]

        self._active = 0

        last_key = config.get(u"last_selected_key")

        if last_key:
            for index, definition in enumerate(self.definitions):
                if definition.key == last_key:
                    self._active = index
                    break

        self.selection_count = int(config.get(u"selection_count") or 0)

        scope = config.get(u"scope")
        self._has_scope = scope is not None
        self._scope = scope if (scope == u"selection" and self.selection_count) else u"model"

        self._status = u""
        self._status_error = False

        self.revalidate()

    # ---------- надписи -----------------------------------------------

    @property
    def Title(self):
        return self.config.get(u"title", u"Проверка")

    @property
    def Subtitle(self):
        return self.config.get(u"subtitle", u"")

    @property
    def RunLabel(self):
        return self.config.get(u"run_label", u"Проверить")

    @property
    def EmptyText(self):
        return self.config.get(
            u"empty_text",
            u"Проверок не найдено. Похоже, повреждён модуль с их описанием."
        )

    # ---------- список проверок ----------------------------------------

    @property
    def HasNoChecks(self):
        return len(self.definitions) == 0

    def active_definition(self):
        if 0 <= self._active < len(self.definitions):
            return self.definitions[self._active]

        return None

    @property
    def RuleTitle(self):
        definition = self.active_definition()

        if definition is None:
            return u"НАСТРОЙКИ ПРОВЕРКИ"

        return u"НАСТРОЙКИ: {}".format(definition.title.upper())

    @property
    def RuleDescription(self):
        definition = self.active_definition()

        if definition is None:
            return u"Выберите проверку слева."

        return definition.description

    def active_option_keys(self):
        definition = self.active_definition()

        if definition is None:
            return []

        return list(definition.option_keys)

    @property
    def HasOptions(self):
        return len(self.active_option_keys()) > 0

    @property
    def HasNoOptions(self):
        return not self.HasOptions

    # ---------- область -------------------------------------------------

    @property
    def HasScope(self):
        return self._has_scope

    @property
    def HasSelection(self):
        return self.selection_count > 0

    @property
    def SelectionLabel(self):
        if not self.selection_count:
            return u"Выделение"

        return u"Выделение — {} {}".format(
            self.selection_count,
            plural(self.selection_count, (u"элемент", u"элемента", u"элементов"))
        )

    @property
    def ScopeHint(self):
        if self._scope == u"selection":
            return u"Проверяются только выделенные элементы — быстро."

        return u"Считается геометрия каждого элемента модели. На крупном проекте это долго."

    @property
    def scope(self):
        return self._scope

    # ---------- статус ----------------------------------------------------

    @property
    def Status(self):
        return self._status

    @property
    def StatusBrush(self):
        return brush(u"Hot" if self._status_error else u"Muted")

    @property
    def IsValid(self):
        return any(self._checked)

    # ---------- команды ----------------------------------------------------

    def set_checked(self, index, value):
        if 0 <= index < len(self._checked):
            self._checked[index] = bool(value)

        self.revalidate()

    def check_all(self, value):
        for index in range(len(self._checked)):
            self._checked[index] = bool(value)

        self.revalidate()

    def is_checked(self, index):
        return self._checked[index]

    def select_rule(self, index):
        if index < 0 or index == self._active:
            return

        self._active = index

        self.notify(u"RuleTitle", u"RuleDescription", u"HasOptions", u"HasNoOptions")

    def set_scope(self, scope):
        self._scope = scope
        self.notify(u"ScopeHint")

    def selected_keys(self):
        return [self.definitions[i].key
                for i in range(len(self.definitions))
                if self._checked[i]]

    def revalidate(self):
        count = len(self.selected_keys())

        if count == 0:
            self._set_status(u"Отметьте хотя бы одну проверку.", True)
        else:
            self._set_status(
                u"К запуску: {} {}.".format(
                    count, plural(count, (u"проверка", u"проверки", u"проверок"))),
                False
            )

        self.notify(u"IsValid")

    def _set_status(self, text, is_error):
        self._status = text
        self._status_error = is_error
        self.notify(u"Status", u"StatusBrush")


# ======================================================================
#  Окно настроек
# ======================================================================

class SetupWindow(object):

    def __init__(self, config):
        self.window = pp_wpf.load_window_file(
            os.path.join(_lib_dir(), SETUP_XAML))

        self.vm = SetupVM(config)
        self.window.DataContext = self.vm
        self.window.Title = self.vm.Title

        self.accepted = False

        # Идёт групповое обновление флажков («Отметить все»): выбор не трогаем
        self._syncing = False

        # option_key -> {"kind":..., "container":..., "control":..., "order":[...]}
        self.options = {}

        # Кнопки «Выбрать…» у опций-параметров: [(option, поле, кнопка), ...]
        self.param_pickers = []

        self._build_checks()
        self._build_options()
        self._fill_controls()
        self._wire()
        self._refresh_options()

        pp_wpf.set_owner(self.window)

    # ---------- построение -------------------------------------------------

    def _build_checks(self):
        u"""Строка = флажок запуска плюс выбор строки для показа её настроек.

        Подпись держим отдельным TextBlock, а не внутри флажка: иначе клик по
        тексту попадал бы по CheckBox и переключал галочку вместо выбора строки.
        Теперь галочку меняет только сам квадратик, вся остальная строка — выбор.
        """
        box = self.window.FindName("LstChecks")
        check_style = style(u"PP.CheckBox")

        self.check_boxes = []

        for index, definition in enumerate(self.vm.definitions):
            check = CheckBox()
            check.IsChecked = self.vm.is_checked(index)
            check.VerticalAlignment = VerticalAlignment.Center
            check.ToolTip = u"Запускать эту проверку"

            # Фокус оставляем списку: стрелки должны ходить по строкам
            check.Focusable = False

            if check_style is not None:
                check.Style = check_style

            label = TextBlock()
            label.Text = definition.title
            label.TextWrapping = TextWrapping.Wrap
            label.VerticalAlignment = VerticalAlignment.Center
            label.Margin = Thickness(8, 0, 0, 0)

            row = Grid()

            col_box = ColumnDefinition()
            col_box.Width = GridLength.Auto
            col_label = ColumnDefinition()
            col_label.Width = GridLength(1, GridUnitType.Star)

            row.ColumnDefinitions.Add(col_box)
            row.ColumnDefinitions.Add(col_label)

            Grid.SetColumn(check, 0)
            Grid.SetColumn(label, 1)

            row.Children.Add(check)
            row.Children.Add(label)

            item = ListBoxItem()
            item.Content = row
            item.ToolTip = (u"Клик по строке — показать настройки проверки справа. "
                            u"Галочка слева — запускать ли её.")

            box.Items.Add(item)
            self.check_boxes.append(check)

    def _build_options(self):
        u"""Все поля создаются сразу, показываются только нужные выбранной проверке."""
        panel = self.window.FindName("PanelOptions")

        builders = self.vm.config.get(u"option_builders") or {}

        for option in self.vm.option_definitions:
            container = StackPanel()
            container.Margin = Thickness(0, 0, 0, 16)

            builder = builders.get(option.option_type)

            if builder is not None:
                # Свой тип опции: контрол строит вызывающий инструмент, чтобы
                # компонент не знал про его предметную область
                control = self._make_custom(container, option, builder)
                order = None
                kind = u"custom"

                panel.Children.Add(container)

                self.options[option.key] = {
                    u"kind": kind,
                    u"container": container,
                    u"control": control,
                    u"order": order,
                }
                continue

            raw_value = unicode(self.vm.values.get(option.key, u""))

            if option.option_type == u"category_multiselect":
                control, order = self._make_multiselect(container, option, raw_value)
                kind = u"multiselect"

            elif option.label.strip().endswith(BOOL_SUFFIX):
                control = self._make_bool(container, option, raw_value)
                order = None
                kind = u"bool"

            elif option.option_type == u"multiline":
                control = self._make_text(container, option, raw_value, multiline=True)
                order = None
                kind = u"multiline"

            elif option.option_type == u"param":
                # Имя параметра: поле плюс кнопка «Выбрать…», если инструмент
                # дал поставщика списка. Значение читается как у обычного текста
                control = self._make_text(
                    container, option, raw_value, multiline=False,
                    with_picker=self._has_param_provider())
                order = None
                kind = u"param"

            else:
                control = self._make_text(container, option, raw_value, multiline=False)
                order = None
                kind = u"text"

            panel.Children.Add(container)

            self.options[option.key] = {
                u"kind": kind,
                u"container": container,
                u"control": control,
                u"order": order,
            }

    def _add_label(self, container, text):
        label = TextBlock()
        label.Text = text
        label.Style = style(u"FieldLabel")
        container.Children.Add(label)
        return label

    def _make_custom(self, container, option, builder):
        u"""builder(raw_value, option) -> (элемент, функция чтения значения)."""
        self._add_label(container, option.label)

        element, getter = builder(self.vm.values.get(option.key), option)
        container.Children.Add(element)

        return getter

    def _make_bool(self, container, option, raw_value):
        check = CheckBox()
        check.Content = option.label.strip()[:-len(BOOL_SUFFIX)].strip()
        check.IsChecked = is_yes(raw_value) if raw_value else True

        check_style = style(u"PP.CheckBox")

        if check_style is not None:
            check.Style = check_style

        container.Children.Add(check)
        return check

    def _has_param_provider(self):
        return self.vm.config.get(u"param_provider") is not None

    def _make_text(self, container, option, raw_value, multiline,
                   with_picker=False):
        self._add_label(container, option.label)

        box = TextBox()
        box.Style = style(u"TextBox.Field")
        box.Text = raw_value

        if multiline:
            box.AcceptsReturn = True
            box.TextWrapping = TextWrapping.NoWrap
            box.Height = 120
            box.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
            box.HorizontalScrollBarVisibility = ScrollBarVisibility.Auto

        if not with_picker:
            container.Children.Add(box)
            return box

        # Поле и кнопка в одном ряду: кнопка дополняет ручной ввод, а не
        # заменяет его — встроенные и семейные параметры вводят руками
        row = Grid()

        field_column = ColumnDefinition()
        field_column.Width = GridLength(1, GridUnitType.Star)

        button_column = ColumnDefinition()
        button_column.Width = GridLength.Auto

        row.ColumnDefinitions.Add(field_column)
        row.ColumnDefinitions.Add(button_column)

        button = Button()
        button.Content = u"Выбрать…"
        button.MinWidth = 0
        button.Padding = Thickness(14, 7, 14, 7)
        button.Margin = Thickness(8, 0, 0, 0)
        button.ToolTip = u"Открыть список параметров проекта с поиском"

        flat = style(u"Button.Flat")

        if flat is not None:
            button.Style = flat

        Grid.SetColumn(box, 0)
        Grid.SetColumn(button, 1)

        row.Children.Add(box)
        row.Children.Add(button)

        container.Children.Add(row)

        self.param_pickers.append((option, box, button))

        return box

    def _make_multiselect(self, container, option, raw_value):
        self._add_label(container, option.label)

        selected = set(parse_category_value(raw_value))

        card = Border()
        card.Style = style(u"Card")
        card.Padding = Thickness(14, 12, 14, 6)

        inner = StackPanel()
        card.Child = inner

        check_style = style(u"PP.CheckBox")
        boxes = []
        order = []

        for choice_key, choice_label in option.choices:
            check = CheckBox()
            check.Content = choice_label
            check.IsChecked = choice_key in selected
            check.Margin = Thickness(0, 0, 0, 8)

            if check_style is not None:
                check.Style = check_style

            inner.Children.Add(check)
            boxes.append(check)
            order.append(choice_key)

        container.Children.Add(card)

        return boxes, order

    # ---------- начальное состояние ------------------------------------------

    def _fill_controls(self):
        find = self.window.FindName

        find("ChipModel").IsChecked = self.vm.scope == u"model"
        find("ChipSelection").IsChecked = self.vm.scope == u"selection"

        box = find("LstChecks")

        if box.Items.Count > 0:
            box.SelectedIndex = min(self.vm._active, box.Items.Count - 1)

    # ---------- подписки -------------------------------------------------------

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        # Фабрика обработчиков: общий цикл с lambda отдал бы всем флажкам
        # последний индекс.
        def make_check_handler(index):
            @guard
            def handler(sender, args):
                self.vm.set_checked(index, sender.IsChecked)

                if self._syncing:
                    return

                # Клик по флажку сам строку не выделяет — покажем её настройки
                self.window.FindName("LstChecks").SelectedIndex = index

            return handler

        for index in range(len(self.check_boxes)):
            handler = make_check_handler(index)
            self.check_boxes[index].Checked += handler
            self.check_boxes[index].Unchecked += handler

        def make_param_handler(option, box):
            @guard
            def handler(sender, args):
                provider = self.vm.config.get(u"param_provider")

                if provider is None:
                    return

                # Поставщику отдаём текущие значения всех опций: по ним он
                # может собрать список под уже отмеченные категории проверки
                options = provider(option.key, self.collect_config())

                name = pp_param_picker.ask(
                    options,
                    current=unicode(box.Text or u"").strip(),
                    title=option.label,
                    subtitle=self.vm.config.get(u"param_picker_subtitle")
                             or DEFAULT_PARAM_SUBTITLE,
                    owner=self.window,
                    empty_text=DEFAULT_PARAM_EMPTY
                )

                if name:
                    box.Text = name

            return handler

        for option, box, button in self.param_pickers:
            button.Click += make_param_handler(option, box)

        @guard
        def on_select(sender, args):
            self.vm.select_rule(sender.SelectedIndex)
            self._refresh_options()

        find("LstChecks").SelectionChanged += on_select

        @guard
        def on_check_all(sender, args):
            self.vm.check_all(True)
            self._sync_check_boxes()

        @guard
        def on_uncheck_all(sender, args):
            self.vm.check_all(False)
            self._sync_check_boxes()

        find("BtnCheckAll").Click += on_check_all
        find("BtnUncheckAll").Click += on_uncheck_all

        @guard
        def on_model(sender, args):
            self.vm.set_scope(u"model")

        @guard
        def on_selection(sender, args):
            self.vm.set_scope(u"selection")

        find("ChipModel").Checked += on_model
        find("ChipSelection").Checked += on_selection

        @guard
        def on_run(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    def _sync_check_boxes(self):
        u"""Кнопки «Отметить все» меняют состояние в VM — флажки нужно догнать."""
        self._syncing = True

        try:
            for index in range(len(self.check_boxes)):
                self.check_boxes[index].IsChecked = self.vm.is_checked(index)
        finally:
            self._syncing = False

    def _refresh_options(self):
        active = set(self.vm.active_option_keys())

        for option_key, entry in self.options.items():
            visible = option_key in active
            entry[u"container"].Visibility = (
                Visibility.Visible if visible else Visibility.Collapsed
            )

    # ---------- результат --------------------------------------------------------

    def collect_config(self):
        values = dict(self.vm.values)

        for option_key, entry in self.options.items():
            kind = entry[u"kind"]

            if kind == u"custom":
                # Значение отдаёт сам контрол: его формат знает только инструмент
                values[option_key] = entry[u"control"]()

            elif kind == u"multiselect":
                boxes = entry[u"control"]
                order = entry[u"order"]

                chosen = [order[i] for i in range(len(boxes))
                          if boxes[i].IsChecked]

                values[option_key] = u", ".join(chosen)

            elif kind == u"bool":
                values[option_key] = YES if entry[u"control"].IsChecked else NO

            elif kind == u"multiline":
                text = unicode(entry[u"control"].Text or u"")
                values[option_key] = text.replace(u"\r\n", u"\n").replace(u"\r", u"\n").strip()

            else:
                values[option_key] = unicode(entry[u"control"].Text or u"").strip()

        return values

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

        definition = self.vm.active_definition()

        return {
            "selected_keys": self.vm.selected_keys(),
            "config": self.collect_config(),
            "scope": self.vm.scope,
            "last_selected_key": definition.key if definition else u"",
        }


# ======================================================================
#  Окно отчёта — ViewModel
# ======================================================================

class ReportVM(pp_wpf.Notifier):

    def __init__(self, results, title):
        pp_wpf.Notifier.__init__(self)

        self.results = results or []
        self.title = title

        self._issues = ObservableCollection[object]()

        # Строки списка и объекты находок идут параллельно: у пояснительных
        # строк объекта нет, поэтому в этой позиции None.
        self.issue_objects = []

        self._status = u""
        self._status_error = False

        self.total_issues = 0

        for result in self.results:
            self.total_issues += len(result.issues)

    # ---------- шапка ------------------------------------------------------

    @property
    def Headline(self):
        if self.total_issues == 0:
            return u"Проблем не найдено"

        return u"Найдено проблем: {}".format(self.total_issues)

    @property
    def Summary(self):
        count = len(self.results)

        return u"Отработало {} {}. Слева — сводка, справа — находки выбранной проверки.".format(
            count, plural(count, (u"проверка", u"проверки", u"проверок"))
        )

    # ---------- находки -----------------------------------------------------

    @property
    def Issues(self):
        return self._issues

    @property
    def HasNoIssues(self):
        return self._issues.Count == 0

    def fill_issues(self, result):
        self._issues.Clear()
        self.issue_objects = []

        if result is not None:
            info = getattr(result, "info", u"")

            if info:
                self._issues.Add(unicode(info))
                self.issue_objects.append(None)

            if not result.issues:
                self._issues.Add(u"Проблем по этой проверке не найдено.")
                self.issue_objects.append(None)
            else:
                for issue in result.issues:
                    self._issues.Add(unicode(issue.message))
                    self.issue_objects.append(issue)

        self.notify(u"HasNoIssues")

    def issue_at(self, index):
        if 0 <= index < len(self.issue_objects):
            return self.issue_objects[index]

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


# ======================================================================
#  Окно отчёта
# ======================================================================

class ReportWindow(object):
    u"""Немодальное окно: модель правится только через ExternalEvent."""

    def __init__(self, results, services):
        self.window = pp_wpf.load_window_file(
            os.path.join(_lib_dir(), REPORT_XAML))

        title = services.get(u"title", u"Отчёт проверки")

        self.vm = ReportVM(results, title)
        self.window.DataContext = self.vm
        self.window.Title = title

        # services: select(ids, report), paint(mode, ids, report),
        #           restart(), on_closed(window)
        self.services = services

        # Выделение поднимает окно Revit, поэтому событие откладываем на такт —
        # иначе фокус возвращается сюда раньше, чем Revit успевает отработать.
        self.timer = DispatcherTimer()
        self.timer.Interval = TimeSpan.FromMilliseconds(150)
        self.pending_ids = []

        self._build_checks()
        self._wire()

        pp_wpf.set_owner(self.window)

        if self.vm.results:
            self.window.FindName("LstChecks").SelectedIndex = 0

    # ---------- построение ---------------------------------------------------

    def _build_checks(self):
        u"""Три колонки: название, число проблем, число проверенных элементов."""
        box = self.window.FindName("LstChecks")

        body = style(u"Body")
        caption = style(u"Caption")

        for result in self.vm.results:
            grid = Grid()

            # Название тянется, два счётчика фиксированы — как в шапке разметки
            widths = [
                GridLength(1, GridUnitType.Star),
                GridLength(70, GridUnitType.Pixel),
                GridLength(80, GridUnitType.Pixel),
            ]

            for width in widths:
                column = ColumnDefinition()
                column.Width = width
                grid.ColumnDefinitions.Add(column)

            title = TextBlock()
            title.Text = result.title
            title.TextWrapping = TextWrapping.Wrap
            title.Margin = Thickness(0, 0, 8, 0)

            if body is not None:
                title.Style = body

            count = TextBlock()
            count.Text = unicode(len(result.issues))

            if body is not None:
                count.Style = body

            if result.issues:
                count.Foreground = brush(u"Hot")

            checked = TextBlock()
            checked.Text = unicode(result.checked_count)

            if caption is not None:
                checked.Style = caption

            Grid.SetColumn(title, 0)
            Grid.SetColumn(count, 1)
            Grid.SetColumn(checked, 2)

            grid.Children.Add(title)
            grid.Children.Add(count)
            grid.Children.Add(checked)

            item = ListBoxItem()
            item.Content = grid
            item.Tag = result

            box.Items.Add(item)

    # ---------- подписки -------------------------------------------------------

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        @guard
        def on_check_changed(sender, args):
            self.vm.fill_issues(self.selected_result())

        find("LstChecks").SelectionChanged += on_check_changed

        @guard
        def on_issue_double_click(sender, args):
            self.select_current_issue()

        find("LstIssues").MouseDoubleClick += on_issue_double_click

        @guard
        def on_select_issue(sender, args):
            self.select_current_issue()

        find("BtnSelectIssue").Click += on_select_issue

        @guard
        def on_select_check(sender, args):
            ids = self.collect_error_ids()

            if ids is None:
                self.vm.set_status(u"Сначала выберите проверку слева.", True)
                return

            if not ids:
                self.vm.set_status(u"У выбранной проверки нет проблемных элементов.", True)
                return

            self.request_select(ids)

        find("BtnSelectCheck").Click += on_select_check

        @guard
        def on_paint(sender, args):
            ids = self.collect_error_ids()

            if ids is None:
                self.vm.set_status(u"Сначала выберите проверку слева.", True)
                return

            if not ids:
                self.vm.set_status(u"У выбранной проверки нет проблемных элементов.", True)
                return

            self.services[u"paint"](u"paint", ids, self.vm.set_status)
            self.vm.set_status(u"Подкрашиваю на активном виде…", False)

        find("BtnPaint").Click += on_paint

        @guard
        def on_reset_paint(sender, args):
            self.services[u"paint"](u"reset", [], self.vm.set_status)
            self.vm.set_status(u"Сбрасываю окраску активного вида…", False)

        find("BtnResetPaint").Click += on_reset_paint

        @guard
        def on_restart(sender, args):
            self.window.Close()
            self.services[u"restart"]()

        find("BtnRestart").Click += on_restart

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

    # ---------- действия ---------------------------------------------------------

    def selected_result(self):
        item = self.window.FindName("LstChecks").SelectedItem

        if item is None:
            return None

        return item.Tag

    def collect_error_ids(self):
        result = self.selected_result()

        if result is None:
            return None

        collected = []
        seen = set()

        for issue in result.issues:
            for int_id in issue.element_ids:
                if int_id in seen:
                    continue

                seen.add(int_id)
                collected.append(int_id)

        return collected

    def select_current_issue(self):
        index = self.window.FindName("LstIssues").SelectedIndex
        issue = self.vm.issue_at(index)

        if issue is None:
            self.vm.set_status(u"Эта строка пояснительная — выделять нечего.", True)
            return

        if not issue.element_ids:
            self.vm.set_status(u"У этой находки нет привязанных элементов.", True)
            return

        self.request_select(issue.element_ids)

    def request_select(self, element_ids):
        self.pending_ids = list(element_ids or [])
        self.timer.Stop()
        self.timer.Start()

        self.vm.set_status(
            u"Выделяю в модели: {} {}.".format(
                len(self.pending_ids),
                plural(len(self.pending_ids), (u"элемент", u"элемента", u"элементов"))
            ),
            False
        )

    def _fail(self, message):
        self.vm.set_status(message, True)

    def show(self):
        self.window.Show()
        self.window.Activate()


# ======================================================================
#  Точки входа
# ======================================================================

def ask_setup(config):
    u"""Показать окно настроек. Возврат: словарь или None при отказе."""
    return SetupWindow(config).show()


def show_results(results, services):
    u"""Открыть немодальное окно отчёта. Возвращает окно — держать ссылку!"""
    window = ReportWindow(results, services)
    window.show()
    return window

# -*- coding: utf-8 -*-
u"""Общее окно выбора имени параметра.

Родилось в кнопке «Передача параметров по MEP-соединениям»: рядом с полем
ручного ввода стоит кнопка «Выбрать…», она открывает список с поиском.
Теперь окно живёт здесь — любой инструмент, где имя параметра вводится
руками, подключает его одной строкой и не копирует разметку.

    import pp_param_picker

    name = pp_param_picker.ask(
        options,                      # см. ниже
        current=u"ADSK_Группирование",
        title=u"Параметр группировки",
        subtitle=u"Список собран с элементов активного вида.",
        owner=self.window)

    if name:
        box.Text = name            # поле ручного ввода инструмента

Возврат: имя параметра (unicode) или None, если пользователь отказался.

Элемент списка задаётся:

    u"ADSK_Группирование"                          — просто имя;
    {u"name": ..., u"display": ...}                — имя плюс строка показа.

`display` — это то, что видит пользователь: туда полезно писать откуда взят
параметр и насколько он покрывает выбранные категории. В поле ввода уходит
только `name`.

Окно ничего не знает про Revit: список готовит вызывающий инструмент.
"""

import os

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

from System.Collections.ObjectModel import ObservableCollection
from System.Windows import Application, TextWrapping, Visibility
from System.Windows.Controls import TextBlock


XAML_FILE = u"pp_param_picker.xaml"

DEFAULT_TITLE = u"Выбор параметра"
DEFAULT_SUBTITLE = u"Выберите параметр из списка или закройте окно и впишите имя вручную."
DEFAULT_EMPTY = u"Параметры не найдены. Измените поиск или впишите имя вручную."


def brush(key):
    try:
        return Application.Current.Resources[key]
    except Exception:
        return None


def normalize_options(options):
    u"""Приводит список к виду [{name, display}, ...] и отсеивает пустые."""
    result = []

    for option in options or []:
        if isinstance(option, dict):
            name = unicode(option.get(u"name") or u"").strip()
            display = unicode(option.get(u"display") or u"").strip() or name
        else:
            name = unicode(option or u"").strip()
            display = name

        if not name:
            continue

        result.append({u"name": name, u"display": display})

    return result


# ======================================================================
#  Окно
# ======================================================================

class ParameterPicker(object):
    u"""Список параметров с поиском и одиночным выбором."""

    def __init__(self, options, current=None, title=None, subtitle=None,
                 owner=None, empty_text=None):
        xaml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), XAML_FILE)

        self.window = pp_wpf.load_window_file(xaml_path)

        self.options = normalize_options(options)
        self.filtered = []
        self.items = ObservableCollection[object]()
        self.result = None

        find = self.window.FindName
        find("TxtPickerTitle").Text = title or DEFAULT_TITLE
        find("TxtPickerSubtitle").Text = subtitle or DEFAULT_SUBTITLE
        find("TxtEmpty").Text = empty_text or DEFAULT_EMPTY
        find("LstParameters").ItemsSource = self.items

        self.window.Title = title or DEFAULT_TITLE

        self._set_owner(owner)
        self._wire()
        self._refresh(u"", current)

    def _set_owner(self, owner):
        u"""Владелец — вызвавшее окно, если оно есть, иначе главное окно Revit."""
        if owner is not None:
            try:
                self.window.Owner = owner
                return
            except Exception:
                pass

        pp_wpf.set_owner(self.window)

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        @guard
        def on_search(sender, args):
            self._refresh(sender.Text, None)

        @guard
        def on_selection(sender, args):
            find("BtnPickerSelect").IsEnabled = sender.SelectedIndex >= 0

        @guard
        def on_select(sender, args):
            self._accept()

        @guard
        def on_double_click(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("TxtSearch").TextChanged += on_search
        find("LstParameters").SelectionChanged += on_selection
        find("LstParameters").MouseDoubleClick += on_double_click
        find("BtnPickerSelect").Click += on_select
        find("BtnPickerCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    def _refresh(self, query, preferred_name):
        query = unicode(query or u"").strip().lower()
        self.filtered = []

        for option in self.options:
            if query and query not in option[u"name"].lower():
                continue
            self.filtered.append(option)

        self.items.Clear()

        for option in self.filtered:
            row = TextBlock()
            row.Text = option[u"display"]
            row.TextWrapping = TextWrapping.Wrap
            self.items.Add(row)

        find = self.window.FindName
        has_items = bool(self.filtered)
        find("TxtEmpty").Visibility = Visibility.Collapsed if has_items else Visibility.Visible
        find("LstParameters").Visibility = Visibility.Visible if has_items else Visibility.Collapsed
        find("TxtPickerStatus").Text = u"Найдено параметров: {}.".format(len(self.filtered))
        find("TxtPickerStatus").Foreground = brush(u"Muted")
        find("BtnPickerSelect").IsEnabled = False

        if preferred_name:
            for index, option in enumerate(self.filtered):
                if option[u"name"].lower() == unicode(preferred_name).lower():
                    find("LstParameters").SelectedIndex = index
                    find("LstParameters").ScrollIntoView(self.items[index])
                    break

    def _accept(self):
        index = self.window.FindName("LstParameters").SelectedIndex

        if index < 0 or index >= len(self.filtered):
            return

        self.result = self.filtered[index][u"name"]
        self.window.DialogResult = True

    def _fail(self, message):
        status = self.window.FindName("TxtPickerStatus")
        status.Text = message
        status.Foreground = brush(u"Hot")

    def show(self):
        self.window.ShowDialog()
        return self.result


def ask(options, current=None, title=None, subtitle=None, owner=None,
        empty_text=None):
    u"""Показать окно выбора. Возврат: имя параметра или None при отказе."""
    return ParameterPicker(
        options, current, title, subtitle, owner, empty_text).show()

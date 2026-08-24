# -*- coding: utf-8 -*-
u"""Список с поиском и галочками.

Тот самый блок, который повторяется в инструментах оформления: строка поиска,
список с галочками, кнопки «Все / Снять все» и счётчик отмеченного. Галочки
живут отдельно от строк списка, поэтому фильтрация их не сбрасывает — снял
фильтр, и всё на месте.

Использование::

    lst = pp_check_list.CheckList(
        [(u"Имя", payload), ...],
        extra_button=(u"Текущий вид", on_current),
        search_hint=u"Поиск по номеру системы, например В1",
    )
    panel.Children.Add(lst.element)

    lst.set_checked([u"Имя"])
    ...
    for name, payload in lst.get_checked():
        ...
"""

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

from System.Windows import Application, GridLength, GridUnitType, Thickness
from System.Windows.Controls import (
    Border, Button, CheckBox, Grid, RowDefinition, ScrollBarVisibility,
    ScrollViewer, StackPanel, TextBlock, TextBox, WrapPanel
)


def style(key):
    try:
        return Application.Current.Resources[key]
    except Exception:
        return None


class CheckList(object):

    def __init__(self, items, extra_button=None, empty_text=None,
                 on_change=None, height=None, search_hint=None):
        # items: [(имя, значение), ...]
        self.items = list(items or [])
        self.checked_names = set()
        self.on_change = on_change

        # Видимая подсказка в пустой строке поиска. Без неё поле читается как
        # непонятное пустое окошко: ToolTip виден только при наведении.
        self.search_hint = search_hint or u"Поиск по списку — начните вводить"

        # Доп. фильтр строк поверх строки поиска: fn(имя, значение) -> bool.
        # Галочки живут отдельно, поэтому фильтр их не сбрасывает.
        self.filter_fn = None

        self.empty_text = empty_text or u"Ничего не найдено. Измените запрос."

        self.boxes = []          # [(имя, CheckBox), ...] — только видимые строки
        self.visible = []

        self.element = self._build(extra_button, height)

        self.refresh()

    # ---------- построение --------------------------------------------

    def _build(self, extra_button, height):
        root = Grid()

        for size in (GridLength(0, GridUnitType.Auto),
                     GridLength(1, GridUnitType.Star),
                     GridLength(0, GridUnitType.Auto),
                     GridLength(0, GridUnitType.Auto)):
            row = RowDefinition()
            row.Height = size
            root.RowDefinitions.Add(row)

        self.search = TextBox()
        self.search.Style = style(u"TextBox.Field")
        self.search.Margin = Thickness(0, 0, 0, 8)
        self.search.Tag = self.search_hint  # подсказка внутри пустого поля
        self.search.ToolTip = u"Показывать только строки, где встречается этот текст"
        self.search.TextChanged += self._on_search
        Grid.SetRow(self.search, 0)
        root.Children.Add(self.search)

        card = Border()
        card.Style = style(u"Card")

        if height:
            card.Height = height

        holder = Grid()

        scroller = ScrollViewer()
        scroller.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        scroller.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
        scroller.Padding = Thickness(14, 12, 10, 6)

        self.panel = StackPanel()
        scroller.Content = self.panel
        holder.Children.Add(scroller)

        self.empty_block = TextBlock()
        self.empty_block.Text = self.empty_text
        self.empty_block.Style = style(u"EmptyState")
        holder.Children.Add(self.empty_block)

        card.Child = holder
        Grid.SetRow(card, 1)
        root.Children.Add(card)

        actions = WrapPanel()
        actions.Margin = Thickness(0, 8, 0, 0)

        self.btn_all = self._action(u"Все", self._on_all)
        self.btn_none = self._action(u"Снять все", self._on_none)

        actions.Children.Add(self.btn_all)
        actions.Children.Add(self.btn_none)

        if extra_button:
            label, handler = extra_button
            self.btn_extra = self._action(label, handler)
            actions.Children.Add(self.btn_extra)

        Grid.SetRow(actions, 2)
        root.Children.Add(actions)

        self.counter = TextBlock()
        self.counter.Style = style(u"Caption")
        self.counter.Margin = Thickness(0, 6, 0, 0)
        Grid.SetRow(self.counter, 3)
        root.Children.Add(self.counter)

        return root

    def _action(self, label, handler):
        button = Button()
        button.Content = label
        button.MinWidth = 0
        button.Padding = Thickness(12, 5, 12, 5)
        button.Margin = Thickness(0, 0, 6, 6)

        flat = style(u"Button.Flat")

        if flat is not None:
            button.Style = flat

        button.Click += handler

        return button

    # ---------- отрисовка ------------------------------------------------

    def refresh(self):
        u"""Пересобираем строки под текущий фильтр. Отметки берём из множества."""
        from System.Windows import Visibility

        mask = unicode(self.search.Text or u"").strip().lower()

        self.panel.Children.Clear()
        self.boxes = []
        self.visible = []

        check_style = style(u"PP.CheckBox")

        for name, payload in self.items:
            if mask and mask not in unicode(name).lower():
                continue

            if self.filter_fn is not None:
                try:
                    if not self.filter_fn(name, payload):
                        continue
                except Exception:
                    pass

            box = CheckBox()
            box.Content = name
            box.IsChecked = name in self.checked_names
            box.Margin = Thickness(0, 0, 0, 8)

            if check_style is not None:
                box.Style = check_style

            box.Checked += self._on_box
            box.Unchecked += self._on_box

            self.panel.Children.Add(box)
            self.boxes.append((name, box))
            self.visible.append((name, payload))

        self.empty_block.Visibility = (
            Visibility.Collapsed if self.boxes else Visibility.Visible
        )

        self._update_counter()

    def _update_counter(self):
        total = len(self.items)
        shown = len(self.boxes)

        if shown == total:
            self.counter.Text = u"Отмечено {} из {}.".format(
                len(self.checked_names), total)
        else:
            self.counter.Text = u"Отмечено {} из {}. Показано {}.".format(
                len(self.checked_names), total, shown)

    # ---------- события ----------------------------------------------------

    def _sync(self):
        u"""Снимаем состояние с видимых строк, скрытые не трогаем."""
        for name, box in self.boxes:
            if box.IsChecked:
                self.checked_names.add(name)
            else:
                self.checked_names.discard(name)

    def _on_box(self, sender, args):
        self._sync()
        self._update_counter()

        if self.on_change is not None:
            self.on_change()

    def _on_search(self, sender, args):
        self._sync()
        self.refresh()

    def _on_all(self, sender, args):
        for _name, box in self.boxes:
            box.IsChecked = True

    def _on_none(self, sender, args):
        for _name, box in self.boxes:
            box.IsChecked = False

    # ---------- состояние ----------------------------------------------------

    def set_checked(self, names):
        self.checked_names = set(names or [])
        self.refresh()

    def set_filter(self, filter_fn):
        u"""Показывать только строки, для которых fn(имя, значение) истинно.

        None снимает фильтр. Отметки не трогаются: скрытые строки остаются
        отмеченными и попадают в get_checked().
        """
        self._sync()
        self.filter_fn = filter_fn
        self.refresh()

    def clear_search(self):
        self.search.Text = u""

    def get_checked(self):
        u"""Возврат: [(имя, значение), ...] в исходном порядке."""
        self._sync()

        return [(name, payload) for name, payload in self.items
                if name in self.checked_names]

    def count(self):
        self._sync()
        return len(self.checked_names)

# -*- coding: utf-8 -*-
u"""Контрол фильтра элементов по параметрам.

Тот самый составной фильтр, что раньше жил внутри «Проверки спецификации» и
«Передачи параметров по MEP»: галочка включения, режим совпадения внутри
группы и между группами, применение к источникам и приёмникам, и список
условий «параметр — оператор — значение — группа».

Использование::

    control = pp_mep_filter_control.MepFilterControl(saved_value)
    panel.Children.Add(control.element)
    ...
    value = control.get_value()

Формат значения совпадает с ``pp_mep_filter.normalize_filter_config``, поэтому
сохранённые настройки читаются без миграции.
"""

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

from System.Windows import (
    Application, GridLength, GridUnitType, HorizontalAlignment, Thickness,
    VerticalAlignment
)
from System.Windows.Controls import (
    Border, Button, CheckBox, ComboBox, ComboBoxItem, ColumnDefinition, Grid,
    Orientation, RadioButton, ScrollViewer, ScrollBarVisibility, StackPanel,
    TextBlock, TextBox, WrapPanel
)
from System.Windows.Shapes import Path
from System.Windows.Media import PathGeometry, Geometry

from pp_mep_filter import (
    FILTER_OPERATORS,
    FILTER_OP_KEYS,
    FILTER_GROUP_NUMBERS,
)


# Счётчик нужен, чтобы GroupName у чипсов не пересекался между экземплярами
_INSTANCE = [0]


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


def label(text, margin=None):
    block = TextBlock()
    block.Text = text
    block.Style = style(u"FieldLabel")

    if margin is not None:
        block.Margin = margin

    return block


def caption(text):
    block = TextBlock()
    block.Text = text
    block.Style = style(u"Caption")
    return block


def chip(text, group_name, checked):
    button = RadioButton()
    button.Content = text
    button.GroupName = group_name
    button.IsChecked = checked
    button.MinWidth = 0

    chip_style = style(u"RadioButton.Chip")

    if chip_style is not None:
        button.Style = chip_style

    return button


def check(text, checked):
    box = CheckBox()
    box.Content = text
    box.IsChecked = checked

    box_style = style(u"PP.CheckBox")

    if box_style is not None:
        box.Style = box_style

    return box


def combo(items, selected_index, width):
    box = ComboBox()
    box.Width = width

    box_style = style(u"PP.ComboBox")

    if box_style is not None:
        box.Style = box_style

    for text in items:
        item = ComboBoxItem()
        item.Content = text
        box.Items.Add(item)

    if 0 <= selected_index < box.Items.Count:
        box.SelectedIndex = selected_index
    elif box.Items.Count:
        box.SelectedIndex = 0

    return box


def cross_button():
    u"""Кнопка удаления строки: крестик вектором, растровых иконок в теме нет."""
    button = Button()
    button.Width = 28
    button.Height = 28
    button.MinWidth = 0
    button.ToolTip = u"Удалить условие"

    tool_style = style(u"Button.Tool")

    if tool_style is not None:
        button.Style = tool_style

    glyph = Path()
    glyph.Data = Geometry.Parse(u"M 0,0 L 9,9 M 9,0 L 0,9")
    glyph.Stroke = brush(u"Muted")
    glyph.StrokeThickness = 1.6
    glyph.HorizontalAlignment = HorizontalAlignment.Center
    glyph.VerticalAlignment = VerticalAlignment.Center

    button.Content = glyph

    return button


# ======================================================================
#  Контрол
# ======================================================================

class MepFilterControl(object):

    COLUMNS = [
        (u"ГРУППА", 62),
        (u"ПАРАМЕТР", 0),      # 0 = тянется
        (u"ОПЕРАТОР", 150),
        (u"ЗНАЧЕНИЕ", 130),
        (u"", 34),
    ]

    def __init__(self, value):
        if not isinstance(value, dict):
            value = {}

        _INSTANCE[0] += 1
        self.uid = u"MepFilter{}".format(_INSTANCE[0])

        self.rows = []

        self.element = self._build(value)

        for condition in value.get("conditions") or []:
            self.add_row(
                param_text=unicode(condition.get("param") or u""),
                op_key=condition.get("op") or "eq",
                value_text=unicode(condition.get("value") or u""),
                group=condition.get("group", 1),
            )

        self._update_enabled()

    # ---------- построение --------------------------------------------

    def _build(self, value):
        root = StackPanel()

        self.chk_enabled = check(u"Использовать фильтр элементов",
                                 bool(value.get("enabled")))
        self.chk_enabled.Checked += self._on_enabled
        self.chk_enabled.Unchecked += self._on_enabled
        root.Children.Add(self.chk_enabled)

        # Всё, кроме галочки включения, гаснет вместе с ней
        self.body = StackPanel()
        self.body.Margin = Thickness(23, 12, 0, 0)
        root.Children.Add(self.body)

        modes = Grid()
        modes.Margin = Thickness(0, 0, 0, 12)

        for width in (GridLength(1, GridUnitType.Star), GridLength(1, GridUnitType.Star)):
            column = ColumnDefinition()
            column.Width = width
            modes.ColumnDefinitions.Add(column)

        within = StackPanel()
        within.Children.Add(label(u"ВНУТРИ ГРУППЫ"))

        within_chips = WrapPanel()
        is_any = value.get("match") == "any"
        self.chip_all = chip(u"все условия", self.uid + u"Match", not is_any)
        self.chip_any = chip(u"любое", self.uid + u"Match", is_any)
        within_chips.Children.Add(self.chip_all)
        within_chips.Children.Add(self.chip_any)
        within.Children.Add(within_chips)

        Grid.SetColumn(within, 0)
        modes.Children.Add(within)

        between = StackPanel()
        between.Children.Add(label(u"МЕЖДУ ГРУППАМИ"))

        between_chips = WrapPanel()
        is_or = value.get("group_combine") == "or"
        self.chip_and = chip(u"И", self.uid + u"Combine", not is_or)
        self.chip_or = chip(u"ИЛИ", self.uid + u"Combine", is_or)
        between_chips.Children.Add(self.chip_and)
        between_chips.Children.Add(self.chip_or)
        between.Children.Add(between_chips)

        Grid.SetColumn(between, 1)
        modes.Children.Add(between)

        self.body.Children.Add(modes)

        self.body.Children.Add(label(u"ПРИМЕНЯТЬ К"))

        apply_row = StackPanel()
        apply_row.Orientation = Orientation.Horizontal
        apply_row.Margin = Thickness(0, 0, 0, 12)

        self.chk_source = check(u"источникам", bool(value.get("apply_source", True)))
        self.chk_receiver = check(u"приёмникам", bool(value.get("apply_receiver", False)))
        self.chk_receiver.Margin = Thickness(18, 0, 0, 0)

        apply_row.Children.Add(self.chk_source)
        apply_row.Children.Add(self.chk_receiver)
        self.body.Children.Add(apply_row)

        self.body.Children.Add(label(u"УСЛОВИЯ"))

        header = Grid()
        header.Margin = Thickness(13, 0, 25, 4)

        for _title, width in self.COLUMNS:
            column = ColumnDefinition()
            column.Width = (GridLength(1, GridUnitType.Star) if not width
                            else GridLength(width, GridUnitType.Pixel))
            header.ColumnDefinitions.Add(column)

        for index, (title, _width) in enumerate(self.COLUMNS):
            if not title:
                continue

            block = caption(title)
            Grid.SetColumn(block, index)
            header.Children.Add(block)

        self.body.Children.Add(header)

        card = Border()
        card.Style = style(u"Card")
        card.Height = 160

        scroller = ScrollViewer()
        scroller.VerticalScrollBarVisibility = ScrollBarVisibility.Visible
        scroller.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
        scroller.Padding = Thickness(9, 8, 9, 8)

        self.rows_panel = StackPanel()
        scroller.Content = self.rows_panel

        self.empty_block = TextBlock()
        self.empty_block.Text = u"Условий нет — фильтр пропустит все элементы."
        self.empty_block.Style = style(u"EmptyState")

        stack = Grid()
        stack.Children.Add(scroller)
        stack.Children.Add(self.empty_block)
        card.Child = stack

        self.body.Children.Add(card)

        self.btn_add = Button()
        self.btn_add.Content = u"Добавить условие"
        self.btn_add.MinWidth = 0
        self.btn_add.Margin = Thickness(0, 8, 0, 0)
        self.btn_add.HorizontalAlignment = HorizontalAlignment.Left

        flat = style(u"Button.Flat")

        if flat is not None:
            self.btn_add.Style = flat

        self.btn_add.Click += self._on_add
        self.body.Children.Add(self.btn_add)

        return root

    # ---------- строки условий -------------------------------------------

    def add_row(self, param_text=u"", op_key="eq", value_text=u"", group=1):
        row = Grid()
        row.Margin = Thickness(0, 0, 0, 6)

        for _title, width in self.COLUMNS:
            column = ColumnDefinition()
            column.Width = (GridLength(1, GridUnitType.Star) if not width
                            else GridLength(width, GridUnitType.Pixel))
            row.ColumnDefinitions.Add(column)

        try:
            group_index = FILTER_GROUP_NUMBERS.index(int(group))
        except (ValueError, TypeError):
            group_index = 0

        cb_group = combo([unicode(number) for number in FILTER_GROUP_NUMBERS],
                         group_index, 54)
        cb_group.Margin = Thickness(0, 0, 8, 0)

        tb_param = TextBox()
        tb_param.Style = style(u"TextBox.Field")
        tb_param.Text = param_text
        tb_param.Margin = Thickness(0, 0, 8, 0)

        op_index = FILTER_OP_KEYS.index(op_key) if op_key in FILTER_OP_KEYS else 0
        cb_op = combo([op_label for op_label, _key in FILTER_OPERATORS], op_index, 142)
        cb_op.Margin = Thickness(0, 0, 8, 0)

        tb_value = TextBox()
        tb_value.Style = style(u"TextBox.Field")
        tb_value.Text = value_text
        tb_value.Width = 122
        tb_value.Margin = Thickness(0, 0, 8, 0)

        btn_del = cross_button()

        for index, control in enumerate((cb_group, tb_param, cb_op, tb_value, btn_del)):
            Grid.SetColumn(control, index)
            row.Children.Add(control)

        entry = {
            "row": row,
            "group": cb_group,
            "param": tb_param,
            "op": cb_op,
            "value": tb_value,
        }

        def on_delete(sender, args):
            self.remove_row(entry)

        btn_del.Click += on_delete

        self.rows.append(entry)
        self.rows_panel.Children.Add(row)

        self._update_empty()

    def remove_row(self, entry):
        if entry not in self.rows:
            return

        self.rows.remove(entry)
        self.rows_panel.Children.Remove(entry["row"])

        self._update_empty()

    # ---------- реакция на ввод --------------------------------------------

    def _on_add(self, sender, args):
        self.add_row()

    def _on_enabled(self, sender, args):
        self._update_enabled()

    def _update_enabled(self):
        self.body.IsEnabled = bool(self.chk_enabled.IsChecked)

    def _update_empty(self):
        from System.Windows import Visibility

        self.empty_block.Visibility = (
            Visibility.Visible if not self.rows else Visibility.Collapsed
        )

    # ---------- значение ------------------------------------------------------

    def get_value(self):
        conditions = []

        for entry in self.rows:
            param_name = unicode(entry["param"].Text or u"").strip()

            if not param_name:
                continue

            op_index = entry["op"].SelectedIndex
            op_key = FILTER_OP_KEYS[op_index] if op_index >= 0 else "eq"

            group_index = entry["group"].SelectedIndex
            group = (FILTER_GROUP_NUMBERS[group_index]
                     if 0 <= group_index < len(FILTER_GROUP_NUMBERS) else 1)

            conditions.append({
                "param": param_name,
                "op": op_key,
                "value": unicode(entry["value"].Text or u"").strip(),
                "group": group,
            })

        return {
            "enabled": bool(self.chk_enabled.IsChecked),
            "match": "any" if self.chip_any.IsChecked else "all",
            "group_combine": "or" if self.chip_or.IsChecked else "and",
            "apply_source": bool(self.chk_source.IsChecked),
            "apply_receiver": bool(self.chk_receiver.IsChecked),
            "conditions": conditions,
        }


def build(value):
    u"""Совместимая обёртка: возвращает (элемент, функция чтения значения)."""
    control = MepFilterControl(value)
    return control.element, control.get_value

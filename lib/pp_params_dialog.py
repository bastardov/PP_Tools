# -*- coding: utf-8 -*-
u"""Общий диалог числовых параметров.

Многие инструменты спрашивают одно и то же: два-три числа с единицами
измерения и кнопку действия. Вместо самодельного окна в каждой кнопке —
одно окно, настраиваемое списком полей.

    values = pp_params_dialog.ask({
        u"title":    u"3D перекрытий",
        u"subtitle": u"Копия активного 3D вида на каждый уровень.",
        u"diagram":  DIAGRAM_XAML,          # необязательно
        u"fields": [
            {u"key": u"bottom", u"label": u"ОТСТУП СНИЗУ", u"short": u"снизу",
             u"unit": u"мм", u"value": 600, u"min": 0,
             u"hint": u"На сколько рез уходит ниже отметки уровня."},
            {u"key": u"top", ...},
        ],
        u"run_label": u"Создать виды",
    })

Необязательный ключ поля ``presets`` — список частых значений. Чипсы и поле
ввода встают в один ряд: пресеты, чипс «Своё» и само поле, активное только
при «Своё». Так же устроен выбор угла в «Опуске/Подъеме трассы»::

    {u"key": u"angle", u"label": u"УГОЛ", u"unit": u"°",
     u"value": 45, u"min": 1, u"max": 90, u"presets": [30, 45, 60, 90]}

Чипс «Своё» можно убрать ключом ``custom_chip: False`` — тогда поле остаётся
доступным всегда, а ручной ввод просто снимает выделение с пресетов.

Возврат: словарь {ключ: float} или None, если пользователь отказался.
"""

import os

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

from System.IO import StringReader
from System.Windows import Application, Thickness, VerticalAlignment
from System.Windows.Controls import (
    Orientation, RadioButton, StackPanel, TextBlock, TextBox, WrapPanel
)
from System.Windows.Markup import XamlReader
from System.Xml import XmlReader


# Счётчик нужен, чтобы GroupName у чипсов не пересекался между полями
_INSTANCE = [0]


XAML_FILE = u"pp_params_dialog.xaml"

DEFAULT_RUN_LABEL = u"Применить"


def parse_number(text):
    if text is None:
        return None

    text = unicode(text).strip().replace(u",", u".")

    if not text:
        return None

    try:
        return float(text)
    except ValueError:
        return None


def format_number(value):
    try:
        if float(value) == int(float(value)):
            return unicode(int(float(value)))
    except Exception:
        pass

    return unicode(value)


def style(key):
    try:
        return Application.Current.Resources[key]
    except Exception:
        return None


# ======================================================================
#  ViewModel
# ======================================================================

class ParamsDialogVM(pp_wpf.Notifier):

    def __init__(self, config):
        pp_wpf.Notifier.__init__(self)

        self.config = config
        self.fields = list(config.get(u"fields") or [])

        self._texts = {}

        for field in self.fields:
            self._texts[field[u"key"]] = format_number(field.get(u"value", 0))

        self._status = u""
        self._status_error = False

        self.revalidate()

    # ---------- надписи ---------------------------------------------

    @property
    def Title(self):
        return self.config.get(u"title", u"Параметры")

    @property
    def Subtitle(self):
        return self.config.get(u"subtitle", u"")

    @property
    def HasSubtitle(self):
        return bool(self.config.get(u"subtitle"))

    @property
    def HasDiagram(self):
        return bool(self.config.get(u"diagram"))

    @property
    def RunLabel(self):
        return self.config.get(u"run_label", DEFAULT_RUN_LABEL)

    # ---------- состояние -------------------------------------------

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

    @property
    def IsValid(self):
        return self.first_bad_field() is None

    # ---------- значения --------------------------------------------

    def text_of(self, key):
        return self._texts.get(key, u"")

    def value_of(self, field):
        value = parse_number(self._texts.get(field[u"key"]))

        if value is None:
            return None

        low = field.get(u"min")
        high = field.get(u"max")

        if low is not None and value < low:
            return None

        if high is not None and value > high:
            return None

        return value

    def first_bad_field(self):
        for field in self.fields:
            if self.value_of(field) is None:
                return field

        return None

    def values(self):
        result = {}

        for field in self.fields:
            result[field[u"key"]] = self.value_of(field)

        return result

    # ---------- команды ---------------------------------------------

    def set_text(self, key, text):
        self._texts[key] = text
        self.revalidate()

    def revalidate(self):
        bad = self.first_bad_field()

        if bad is not None:
            self._set_status(self._complaint(bad), True)
        else:
            self._set_status(self._ready_text(), False)

        self.notify(u"IsValid")

    def _complaint(self, field):
        name = field.get(u"short") or field.get(u"label", u"Значение")

        low = field.get(u"min")
        high = field.get(u"max")

        if low is not None and high is not None:
            return u"{}: введите число от {} до {}.".format(
                name.capitalize(), format_number(low), format_number(high))

        if low is not None:
            return u"{}: введите число не меньше {}.".format(
                name.capitalize(), format_number(low))

        return u"{}: введите число.".format(name.capitalize())

    def _ready_text(self):
        parts = []

        for field in self.fields:
            name = field.get(u"short") or field.get(u"key")
            parts.append(u"{} {} {}".format(
                name,
                format_number(self.value_of(field)),
                field.get(u"unit", u"")
            ).strip())

        return u"{}: {}.".format(self.RunLabel, u", ".join(parts))

    def _set_status(self, text, is_error):
        self._status = text
        self._status_error = is_error
        self.notify(u"Status", u"StatusBrush")


# ======================================================================
#  Окно
# ======================================================================

class ParamsDialog(object):

    def __init__(self, config):
        xaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), XAML_FILE)

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = ParamsDialogVM(config)
        self.window.DataContext = self.vm

        self.accepted = False
        self.boxes = {}

        self.window.Title = self.vm.Title

        self._build_diagram()
        self._build_fields()
        self._wire()

        pp_wpf.set_owner(self.window)

    def _build_diagram(self):
        u"""Схему рисует вызывающий инструмент: она про его геометрию, не про окно."""
        markup = self.vm.config.get(u"diagram")

        if not markup:
            return

        try:
            element = XamlReader.Load(XmlReader.Create(StringReader(markup)))
            self.window.FindName("DiagramHost").Content = element
        except Exception:
            # Схема — украшение: без неё окно должно открыться
            pass

    def _build_fields(self):
        panel = self.window.FindName("PanelFields")

        self.presets = {}
        self.custom_chips = {}

        for index, field in enumerate(self.vm.fields):
            block = StackPanel()

            if index:
                block.Margin = Thickness(0, 14, 0, 0)

            label = TextBlock()
            label.Text = field.get(u"label", field[u"key"])
            label.Style = style(u"FieldLabel")
            block.Children.Add(label)

            hint_text = field.get(u"hint")

            if hint_text:
                hint = TextBlock()
                hint.Text = hint_text
                hint.Style = style(u"Caption")
                hint.Margin = Thickness(0, -2, 0, 6)
                block.Children.Add(hint)

            box = TextBox()
            box.Style = style(u"TextBox.Field")
            box.Width = 120
            box.Text = self.vm.text_of(field[u"key"])

            unit = None
            unit_text = field.get(u"unit")

            if unit_text:
                unit = TextBlock()
                unit.Text = unit_text
                unit.Style = style(u"Caption")
                unit.Margin = Thickness(8, 0, 0, 0)
                unit.VerticalAlignment = VerticalAlignment.Center

            if field.get(u"presets"):
                # Чипсы и поле в одном ряду: поле — это вариант «Своё»
                self._build_presets_row(block, field, box, unit)
            else:
                row = StackPanel()
                row.Orientation = Orientation.Horizontal
                row.Children.Add(box)

                if unit is not None:
                    row.Children.Add(unit)

                block.Children.Add(row)

            panel.Children.Add(block)

            self.boxes[field[u"key"]] = box

    def _build_presets_row(self, block, field, box, unit):
        u"""Частые значения чипсами плюс «Своё» с полем ввода — как в «Опуске»."""
        _INSTANCE[0] += 1
        group_name = u"Preset{}".format(_INSTANCE[0])

        wrap = WrapPanel()

        chip_style = style(u"RadioButton.Chip")
        chips = []

        current = self.vm.text_of(field[u"key"]).strip()
        matched = False

        for value in field[u"presets"]:
            text = format_number(value)

            chip = RadioButton()
            chip.Content = u"{}{}".format(text, field.get(u"unit", u""))
            chip.GroupName = group_name
            chip.MinWidth = 0

            if chip_style is not None:
                chip.Style = chip_style

            if text == current:
                chip.IsChecked = True
                matched = True

            wrap.Children.Add(chip)
            chips.append((text, chip))

        self.presets[field[u"key"]] = chips

        if field.get(u"custom_chip", True):
            custom = RadioButton()
            custom.Content = u"Своё"
            custom.GroupName = group_name
            custom.MinWidth = 0
            custom.ToolTip = u"Ввести значение вручную"

            if chip_style is not None:
                custom.Style = chip_style

            custom.IsChecked = not matched

            wrap.Children.Add(custom)

            box.Margin = Thickness(2, 0, 0, 0)
            box.Width = 92
            box.IsEnabled = bool(custom.IsChecked)

            self.custom_chips[field[u"key"]] = custom

        wrap.Children.Add(box)

        if unit is not None:
            wrap.Children.Add(unit)

        block.Children.Add(wrap)

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        # Фабрика обработчиков: общий цикл с lambda отдал бы всем полям
        # последний ключ.
        def make_handler(key):
            @guard
            def handler(sender, args):
                self.vm.set_text(key, sender.Text)
                self._sync_presets(key)

            return handler

        for key, box in self.boxes.items():
            box.TextChanged += make_handler(key)

        def make_preset_handler(key, text):
            @guard
            def handler(sender, args):
                # Пресет ведёт поле, а не наоборот: правим текст, TextChanged
                # сам обновит VM
                self.boxes[key].IsEnabled = False
                self.boxes[key].Text = text

            return handler

        for key, chips in self.presets.items():
            for text, chip in chips:
                chip.Checked += make_preset_handler(key, text)

        def make_custom_handler(key):
            @guard
            def handler(sender, args):
                box = self.boxes[key]
                box.IsEnabled = True
                box.Focus()
                box.SelectAll()

            return handler

        for key, chip in self.custom_chips.items():
            chip.Checked += make_custom_handler(key)

        @guard
        def on_run(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    def _sync_presets(self, key):
        u"""Без чипса «Своё» ручной ввод снимает выделение с пресетов.

        С чипсом «Своё» ряд ведут чипсы, и трогать их из поля нельзя: иначе
        ввод «45» переключил бы окно с «Своё» на пресет прямо под руками.
        """
        if key in self.custom_chips:
            return

        chips = self.presets.get(key)

        if not chips:
            return

        current = self.vm.text_of(key).strip()

        for text, chip in chips:
            if chip.IsChecked != (text == current):
                chip.IsChecked = (text == current)

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

        return self.vm.values()


def ask(config):
    u"""Показать диалог. Возврат: словарь значений или None при отказе."""
    return ParamsDialog(config).show()

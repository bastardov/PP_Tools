# -*- coding: utf-8 -*-
u"""Окно параметров инструмента «Создать прокси-позицию»."""

import os

import pp_wpf

from System.Windows import Application


TEXT_DEFAULTS = {
    u"ADSK_Марка": u"",
    u"ADSK_Наименование": u"",
    u"ADSK_Единица измерения": u"шт.",
    u"ADSK_Количество": u"1",
    u"ADSK_Завод-изготовитель": u"",
    u"ADSK_Примечание": u"",
}


def parse_number(text):
    if text is None:
        return None

    value = unicode(text).strip().replace(u",", u".")

    if not value:
        return None

    try:
        return float(value)
    except ValueError:
        return None


class ProxyDataVM(pp_wpf.Notifier):

    def __init__(self, rule_codes, position_types):
        pp_wpf.Notifier.__init__(self)

        self._values = dict(TEXT_DEFAULTS)
        self.rule_codes = self._clean_choices(rule_codes, u"РУЧНАЯ_ПОЗИЦИЯ")
        self.position_types = self._clean_choices(position_types, u"Ручная позиция")
        self._rule = self.rule_codes[0]
        self._position_type = self.position_types[0]
        self._status = u""
        self._status_error = False

        self.revalidate()

    @staticmethod
    def _clean_choices(values, fallback):
        result = []

        for value in (values or []):
            text = unicode(value).strip()

            if text and text not in result:
                result.append(text)

        return result if result else [fallback]

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
        return (
            not self._status_error
            and self.quantity is not None
            and bool(self._rule)
            and bool(self._position_type)
        )

    @property
    def quantity(self):
        value = parse_number(self._values[u"ADSK_Количество"])

        if value is None or value <= 0:
            return None

        return value

    def set_text(self, param_name, text):
        if param_name not in self._values:
            self.set_error(u"Неизвестный параметр формы.")
            return

        self._values[param_name] = unicode(text) if text is not None else u""
        self.revalidate()

    def set_rule(self, value):
        self._rule = unicode(value).strip() if value is not None else u""
        self.revalidate()

    def set_position_type(self, value):
        self._position_type = unicode(value).strip() if value is not None else u""
        self.revalidate()

    def revalidate(self):
        quantity_text = self._values[u"ADSK_Количество"]
        quantity_value = parse_number(quantity_text)

        if quantity_value is None:
            self._set_status(u"Количество: введите число, например 1.", True)

        elif quantity_value <= 0:
            self._set_status(u"Количество должно быть больше нуля.", True)

        elif not self._rule:
            self._set_status(u"Выберите код правила.", True)

        elif not self._position_type:
            self._set_status(u"Выберите тип позиции.", True)

        else:
            mark = self._values[u"ADSK_Марка"].strip()
            name = self._values[u"ADSK_Наименование"].strip()

            if mark and name:
                message = u"Будет создана позиция «{} — {}», количество {}.".format(
                    mark,
                    name,
                    quantity_text
                )
            elif name:
                message = u"Будет создана позиция «{}», количество {}.".format(
                    name,
                    quantity_text
                )
            else:
                message = u"Параметры заполнены. Прокси-позиция готова к созданию."

            self._set_status(message, False)

        self.notify(u"IsValid")

    def set_error(self, message):
        self._set_status(message, True)
        self.notify(u"IsValid")

    def _set_status(self, message, is_error):
        self._status = message
        self._status_error = is_error
        self.notify(u"Status", u"StatusBrush")

    def result(self):
        data = dict(self._values)
        data[u"ADSK_Количество"] = data[u"ADSK_Количество"].replace(u",", u".")
        data[u"PP_Код правила"] = self._rule
        data[u"PP_Тип позиции"] = self._position_type
        return data


class ProxyDataWindow(object):

    def __init__(self, rule_codes, position_types):
        xaml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            u"ui.xaml"
        )

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = ProxyDataVM(rule_codes, position_types)
        self.window.DataContext = self.vm
        self.accepted = False

        self._fill_controls()
        self._wire()
        pp_wpf.set_owner(self.window)

    def _text_controls(self):
        find = self.window.FindName

        return [
            (u"ADSK_Марка", find("TxtMark")),
            (u"ADSK_Наименование", find("TxtName")),
            (u"ADSK_Единица измерения", find("TxtUnit")),
            (u"ADSK_Количество", find("TxtQuantity")),
            (u"ADSK_Завод-изготовитель", find("TxtManufacturer")),
            (u"ADSK_Примечание", find("TxtNote")),
        ]

    def _fill_controls(self):
        find = self.window.FindName

        for param_name, control in self._text_controls():
            control.Text = self.vm._values[param_name]

        rule_combo = find("CmbRule")
        type_combo = find("CmbType")

        for value in self.vm.rule_codes:
            rule_combo.Items.Add(value)

        for value in self.vm.position_types:
            type_combo.Items.Add(value)

        rule_combo.SelectedIndex = 0
        type_combo.SelectedIndex = 0

    def _wire(self):
        find = self.window.FindName
        guarded = pp_wpf.guard(self.vm.set_error)

        def make_text_handler(param_name):
            @guarded
            def handler(sender, args):
                self.vm.set_text(param_name, sender.Text)

            return handler

        for param_name, control in self._text_controls():
            control.TextChanged += make_text_handler(param_name)

        @guarded
        def on_rule(sender, args):
            self.vm.set_rule(sender.SelectedItem)

        @guarded
        def on_type(sender, args):
            self.vm.set_position_type(sender.SelectedItem)

        @guarded
        def on_run(sender, args):
            self._accept()

        @guarded
        def on_cancel(sender, args):
            self.window.Close()

        find("CmbRule").SelectionChanged += on_rule
        find("CmbType").SelectionChanged += on_type
        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(
            self.window,
            on_accept=self._accept,
            on_cancel=self.window.Close
        )

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


def ask_proxy_data(rule_codes, position_types):
    return ProxyDataWindow(rule_codes, position_types).show()

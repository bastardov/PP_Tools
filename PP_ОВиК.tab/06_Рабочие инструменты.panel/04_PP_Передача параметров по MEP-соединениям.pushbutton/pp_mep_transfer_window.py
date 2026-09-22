# -*- coding: utf-8 -*-
u"""Окно настроек инструмента «Передача параметров по MEP-соединениям».

Модальное: собирает категории, параметры, режим и фильтр, закрывается и
отдаёт словарь скрипту. Работы с моделью здесь нет.

Фильтр элементов — общий контрол из lib/pp_mep_filter_control: тот же самый
используется в «Проверке спецификации».

Окно выбора параметра по кнопке «Выбрать…» — общий компонент
lib/pp_param_picker: раньше он жил здесь, теперь им пользуются и другие
инструменты с ручным вводом имени параметра.
"""

import os

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

from System.Windows import Application, Thickness
from System.Windows.Controls import CheckBox

import pp_mep_filter_control
import pp_param_picker


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

class TransferVM(pp_wpf.Notifier):

    def __init__(self, config):
        pp_wpf.Notifier.__init__(self)

        self.config = config

        # options: [{key, name, category}, ...]
        self.options = list(config.get(u"options") or [])

        source_keys = set(config.get(u"source_keys") or [])
        receiver_keys = set(config.get(u"receiver_keys") or [])

        self._source = [option[u"key"] in source_keys for option in self.options]
        self._receiver = [option[u"key"] in receiver_keys for option in self.options]

        self._source_param = unicode(config.get(u"source_param") or u"")
        self._receiver_param = unicode(config.get(u"receiver_param") or u"")

        self._mode = config.get(u"mode") or config.get(u"modes")[0][0]
        self._chain = bool(config.get(u"chain_fill"))

        self._status = u""
        self._status_error = False

        self.revalidate()

    # ---------- категории ---------------------------------------------

    def checked(self, side):
        return self._source if side == u"source" else self._receiver

    def is_checked(self, side, index):
        return self.checked(side)[index]

    def set_checked(self, side, index, value):
        flags = self.checked(side)

        if 0 <= index < len(flags):
            flags[index] = bool(value)

        self.notify(u"SourceCounter", u"ReceiverCounter")
        self.revalidate()

    def set_all(self, side, value):
        flags = self.checked(side)

        for index in range(len(flags)):
            flags[index] = bool(value)

        self.notify(u"SourceCounter", u"ReceiverCounter")
        self.revalidate()

    def selected_options(self, side):
        flags = self.checked(side)

        return [self.options[i] for i in range(len(self.options)) if flags[i]]

    def _counter(self, side):
        return u"Отмечено {} из {}.".format(
            len(self.selected_options(side)), len(self.options))

    @property
    def SourceCounter(self):
        return self._counter(u"source")

    @property
    def ReceiverCounter(self):
        return self._counter(u"receiver")

    # ---------- режим ----------------------------------------------------

    @property
    def mode(self):
        return self._mode

    def set_mode(self, mode):
        self._mode = mode
        self.notify(u"ScopeHint")

    @property
    def ScopeHint(self):
        for key, _label, hint in self.config.get(u"modes") or []:
            if key == self._mode:
                return hint

        return u""

    # ---------- параметры -------------------------------------------------

    @property
    def source_param(self):
        return self._source_param.strip()

    @property
    def receiver_param(self):
        return self._receiver_param.strip()

    def set_source_param(self, text):
        self._source_param = text
        self.revalidate()

    def set_receiver_param(self, text):
        self._receiver_param = text
        self.revalidate()

    @property
    def chain_fill(self):
        return self._chain

    def set_chain(self, value):
        self._chain = bool(value)

    # ---------- статус ------------------------------------------------------

    @property
    def Status(self):
        return self._status

    @property
    def StatusBrush(self):
        return brush(u"Hot" if self._status_error else u"Muted")

    @property
    def IsValid(self):
        return (bool(self.selected_options(u"source"))
                and bool(self.selected_options(u"receiver"))
                and bool(self.source_param)
                and bool(self.receiver_param))

    def revalidate(self):
        u"""Единственное место, где решается, готово окно к запуску или нет."""
        if not self.selected_options(u"source"):
            self._set_status(u"Отметьте хотя бы одну категорию источника.", True)

        elif not self.selected_options(u"receiver"):
            self._set_status(u"Отметьте хотя бы одну категорию приёмника.", True)

        elif not self.source_param:
            self._set_status(u"Укажите параметр источника.", True)

        elif not self.receiver_param:
            self._set_status(u"Укажите параметр приёмника.", True)

        else:
            sources = len(self.selected_options(u"source"))
            receivers = len(self.selected_options(u"receiver"))

            self._set_status(
                u"Передача «{}» → «{}»: {} {} источника, {} {} приёмника.".format(
                    self.source_param, self.receiver_param,
                    sources, plural(sources, (u"категория", u"категории", u"категорий")),
                    receivers, plural(receivers, (u"категория", u"категории", u"категорий"))
                ),
                False
            )

        self.notify(u"IsValid")

    def _set_status(self, text, is_error):
        self._status = text
        self._status_error = is_error
        self.notify(u"Status", u"StatusBrush")


# ======================================================================
#  Окно
# ======================================================================

class TransferWindow(object):

    def __init__(self, script_dir, config):
        self.window = pp_wpf.load_window_file(os.path.join(script_dir, u"ui.xaml"))

        self.vm = TransferVM(config)
        self.window.DataContext = self.vm

        self.accepted = False

        self.boxes = {u"source": [], u"receiver": []}

        self._build_categories(u"source", "PanelSource")
        self._build_categories(u"receiver", "PanelReceiver")
        self._build_filter()
        self._fill_controls()
        self._wire()

        pp_wpf.set_owner(self.window)

    # ---------- построение -------------------------------------------------

    def _build_categories(self, side, panel_name):
        panel = self.window.FindName(panel_name)
        check_style = style(u"PP.CheckBox")

        for index, option in enumerate(self.vm.options):
            box = CheckBox()
            box.Content = option[u"name"]
            box.IsChecked = self.vm.is_checked(side, index)
            box.Margin = Thickness(0, 0, 0, 8)

            if check_style is not None:
                box.Style = check_style

            panel.Children.Add(box)
            self.boxes[side].append(box)

    def _build_filter(self):
        self.filter_control = pp_mep_filter_control.MepFilterControl(
            self.vm.config.get(u"filter"))

        self.window.FindName("FilterHost").Content = self.filter_control.element

    # ---------- начальное состояние ------------------------------------------

    def _fill_controls(self):
        find = self.window.FindName

        find("TxtSourceParam").Text = self.vm.source_param
        find("TxtReceiverParam").Text = self.vm.receiver_param
        find("ChkChain").IsChecked = self.vm.chain_fill

        for key, chip_name in self._mode_chips():
            find(chip_name).IsChecked = (key == self.vm.mode)

    def _mode_chips(self):
        u"""Порядок чипсов в разметке повторяет порядок режимов в конфигурации."""
        names = ("ChipWholeModel", "ChipActiveView",
                 "ChipManualSource", "ChipManualReceiver")

        modes = self.vm.config.get(u"modes") or []

        return [(modes[i][0], names[i]) for i in range(min(len(modes), len(names)))]

    def _parameter_options(self, side):
        selected_keys = set(
            option[u"key"] for option in self.vm.selected_options(side))
        selected_count = len(selected_keys)
        result = []

        for parameter in self.vm.config.get(u"parameters") or []:
            instance_keys = selected_keys.intersection(
                set(parameter.get(u"instance_keys") or []))
            type_keys = set()

            if side == u"source":
                type_keys = selected_keys.intersection(
                    set(parameter.get(u"type_keys") or []))

            matched_keys = instance_keys.union(type_keys)

            if not matched_keys:
                continue

            if instance_keys and type_keys:
                scope = u"экземпляр / тип"
            elif instance_keys:
                scope = u"экземпляр"
            else:
                scope = u"тип"

            name = parameter[u"name"]
            matched_count = len(matched_keys)
            display = u"{}    · {} · для {} из {} категорий".format(
                name, scope, matched_count, selected_count)

            result.append({
                u"name": name,
                u"display": display,
                u"coverage": matched_count,
            })

        return sorted(
            result,
            key=lambda item: (
                0 if item[u"coverage"] == selected_count else 1,
                item[u"name"].lower()
            )
        )

    def _pick_parameter(self, side):
        is_source = side == u"source"

        if is_source:
            title = u"Параметр источника"
            subtitle = (u"Показаны параметры проекта выбранных категорий. "
                        u"Для чтения доступны параметры экземпляра и типа.")
            current_name = self.vm.source_param
            text_box_name = "TxtSourceParam"
        else:
            title = u"Параметр приёмника"
            subtitle = (u"Показаны параметры проекта выбранных категорий. "
                        u"Для записи доступны только параметры экземпляра.")
            current_name = self.vm.receiver_param
            text_box_name = "TxtReceiverParam"

        selected = pp_param_picker.ask(
            self._parameter_options(side),
            current=current_name,
            title=title,
            subtitle=subtitle,
            owner=self.window,
            empty_text=u"Параметры не найдены. Измените поиск или категории "
                       u"в основном окне."
        )

        if selected:
            self.window.FindName(text_box_name).Text = selected

    # ---------- подписки -------------------------------------------------------

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        # Фабрика обработчиков: общий цикл с lambda отдал бы всем флажкам
        # последний индекс.
        def make_category_handler(side, index):
            @guard
            def handler(sender, args):
                self.vm.set_checked(side, index, sender.IsChecked)

            return handler

        for side in (u"source", u"receiver"):
            for index in range(len(self.boxes[side])):
                handler = make_category_handler(side, index)
                self.boxes[side][index].Checked += handler
                self.boxes[side][index].Unchecked += handler

        def make_all_handler(side, value):
            @guard
            def handler(sender, args):
                self.vm.set_all(side, value)
                self._sync(side)

            return handler

        find("BtnSourceAll").Click += make_all_handler(u"source", True)
        find("BtnSourceNone").Click += make_all_handler(u"source", False)
        find("BtnReceiverAll").Click += make_all_handler(u"receiver", True)
        find("BtnReceiverNone").Click += make_all_handler(u"receiver", False)

        @guard
        def on_source_param(sender, args):
            self.vm.set_source_param(sender.Text)

        @guard
        def on_receiver_param(sender, args):
            self.vm.set_receiver_param(sender.Text)

        find("TxtSourceParam").TextChanged += on_source_param
        find("TxtReceiverParam").TextChanged += on_receiver_param

        @guard
        def on_pick_source_param(sender, args):
            self._pick_parameter(u"source")

        @guard
        def on_pick_receiver_param(sender, args):
            self._pick_parameter(u"receiver")

        find("BtnSourceParam").Click += on_pick_source_param
        find("BtnReceiverParam").Click += on_pick_receiver_param

        def make_mode_handler(mode):
            @guard
            def handler(sender, args):
                self.vm.set_mode(mode)

            return handler

        for key, chip_name in self._mode_chips():
            find(chip_name).Checked += make_mode_handler(key)

        @guard
        def on_chain(sender, args):
            self.vm.set_chain(sender.IsChecked)

        chain = find("ChkChain")
        chain.Checked += on_chain
        chain.Unchecked += on_chain

        @guard
        def on_run(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    def _sync(self, side):
        u"""Кнопки «Все / Ничего» меняют состояние в VM — флажки нужно догнать."""
        for index in range(len(self.boxes[side])):
            self.boxes[side][index].IsChecked = self.vm.is_checked(side, index)

    # ---------- результат --------------------------------------------------------

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

        return {
            "source_options": self.vm.selected_options(u"source"),
            "receiver_options": self.vm.selected_options(u"receiver"),
            "source_param": self.vm.source_param,
            "receiver_param": self.vm.receiver_param,
            "mode": self.vm.mode,
            "filter": self.filter_control.get_value(),
            "chain_fill": self.vm.chain_fill,
        }


def ask(script_dir, config):
    u"""Показать окно настроек. Возврат: словарь или None при отказе."""
    return TransferWindow(script_dir, config).show()

# -*- coding: utf-8 -*-
u"""Общий диалог инструментов окрашивания.

Четыре кнопки панели «Теплопотери» спрашивали одно и то же: какие категории
брать, иногда числовой порог, и надо ли вместо окраски сбросить переопределения.
Раньше это были четыре копии одного WinForms-кода; теперь одно окно,
настраиваемое словарём.

    result = pp_paint_dialog.ask({
        u"title":      u"Окраска по помещениям",
        u"subtitle":   u"Элементы получают цвет по параметру ...",
        u"categories": [(u"Стены", BuiltInCategory.OST_Walls), ...],
        u"number":     {u"label": u"ПОРОГ НАЛОЖЕНИЯ", u"unit": u"%",
                        u"value": 90.0, u"min": 1.0, u"max": 100.0},
        u"run_label":  u"Окрасить",
    })

Необязательные ключи конфигурации:

    switches   — список дополнительных галочек:
                 [{u"key": ..., u"label": ..., u"hint": ..., u"value": bool}]
    choice     — выбор значения из списка с поиском:
                 {u"label": ..., u"hint": ..., u"items": [...], u"value": ...,
                  u"required": True, u"picker": {...}}
                 Список фильтруется по введённому тексту, но не ограничивает
                 его: применяется то, что осталось в поле.
                 Ключ picker добавляет рядом с полем кнопку «Выбрать…» —
                 отдельное окно со списком, поиском и пояснением к каждой
                 строке (общий компонент lib/pp_param_picker.py):
                 {u"title": ..., u"subtitle": ..., u"empty_text": ...,
                  u"options": [...]   либо
                  u"provider": callable(selected_categories) -> options}
                 provider вызывается в момент нажатия, поэтому список можно
                 собрать под уже отмеченные категории. selected_categories —
                 то же, что уходит в результат: [(подпись, значение), ...].
    actions_from — с какого числа категорий показывать «Отметить все»
                 и прокручивать список (по умолчанию 6).
    reset      — False убирает галочку «Сбросить»: инструменту, который
                 ничего не окрашивает, она не нужна (по умолчанию True).
    ready_label — текст строки состояния, когда всё заполнено верно
                 (по умолчанию «Окраска»). Инструменту, который ничего не
                 красит, обязательно задать свой: иначе внизу окна висит
                 неуместное «Окраска.». Парный ключ для режима сброса —
                 reset_ready_label.
    require_switch — True требует хотя бы одну отмеченную галочку switches,
                 иначе кнопка запуска неактивна. Текст подсказки —
                 require_switch_text.

Категория задаётся парой (подпись, значение) или тройкой с отметкой по
умолчанию: (подпись, значение, отмечена).

Возврат: словарь или None, если пользователь отказался.

    {"categories": [(label, payload), ...],
     "reset": bool,
     "number": float|None,
     "choice": unicode|None,
     "switches": {key: bool}}
"""

import os

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

import pp_param_picker

from System.Collections.ObjectModel import ObservableCollection
from System.Windows import Application, Thickness
from System.Windows.Controls import CheckBox, TextBlock


XAML_FILE = u"pp_paint_dialog.xaml"

DEFAULT_RESET_LABEL = u"Сбросить окрашивание вместо окраски"
DEFAULT_CATEGORIES_LABEL = u"КАТЕГОРИИ"
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


# ======================================================================
#  ViewModel
# ======================================================================

class PaintDialogVM(pp_wpf.Notifier):

    def __init__(self, config):
        pp_wpf.Notifier.__init__(self)

        self.config = config

        # Категория — (подпись, значение) или (подпись, значение, отмечена)
        self._categories = []
        self._checked = []

        for item in config.get(u"categories") or []:
            self._categories.append((item[0], item[1]))
            self._checked.append(bool(item[2]) if len(item) > 2 else True)

        self._number = config.get(u"number")

        if self._number:
            self._number_text = format_number(self._number.get(u"value", 0))
        else:
            self._number_text = u""

        self._reset = bool(config.get(u"reset_default", False))

        # Дополнительные галочки инструмента: [{key, label, hint, value}]
        self._switch_defs = list(config.get(u"switches") or [])
        self._switches = {}

        for switch in self._switch_defs:
            self._switches[switch[u"key"]] = bool(switch.get(u"value", False))

        # Выбор значения из списка с поиском
        self._choice = config.get(u"choice")

        self._choice_items = ObservableCollection[object]()
        self._choice_all = []
        self._choice_text = u""

        if self._choice:
            self._choice_all = [unicode(item) for item in (self._choice.get(u"items") or [])]
            self._choice_text = unicode(self._choice.get(u"value") or u"")
            self._refill_choice()

        self._status = u""
        self._status_error = False

        self.revalidate()

    # ---------- надписи ---------------------------------------------

    @property
    def Title(self):
        return self.config.get(u"title", u"Окраска")

    @property
    def Subtitle(self):
        return self.config.get(u"subtitle", u"")

    @property
    def HasSubtitle(self):
        return bool(self.config.get(u"subtitle"))

    @property
    def CategoriesLabel(self):
        return self.config.get(u"categories_label", DEFAULT_CATEGORIES_LABEL)

    @property
    def CategoriesHint(self):
        return self.config.get(u"categories_hint", u"")

    @property
    def HasCategoriesHint(self):
        return bool(self.config.get(u"categories_hint"))

    @property
    def HasCategories(self):
        return len(self._categories) > 0

    @property
    def HasCategoryActions(self):
        u"""Кнопки «Отметить все» нужны, когда категорий много."""
        return len(self._categories) >= int(self.config.get(u"actions_from", 6))

    @property
    def CategoriesCounter(self):
        return u"Отмечено {} из {}.".format(
            len(self.selected_categories()), len(self._categories))

    # ---------- выбор значения из списка ------------------------------

    @property
    def HasChoice(self):
        return self._choice is not None

    @property
    def ChoiceLabel(self):
        return (self._choice or {}).get(u"label", u"")

    @property
    def ChoiceHint(self):
        return (self._choice or {}).get(u"hint", u"")

    @property
    def HasChoiceHint(self):
        return bool((self._choice or {}).get(u"hint"))

    @property
    def ChoiceItems(self):
        return self._choice_items

    @property
    def HasChoicePicker(self):
        u"""Кнопка «Выбрать…» показывается, только если инструмент её заказал."""
        return bool((self._choice or {}).get(u"picker"))

    def choice_picker_config(self):
        return (self._choice or {}).get(u"picker") or {}

    def choice_picker_options(self):
        u"""Список для окна выбора: статический или собранный поставщиком.

        Поставщик вызывается в момент нажатия — он видит текущие отметки
        категорий, поэтому список подстраивается под выбор пользователя."""
        picker = self.choice_picker_config()
        provider = picker.get(u"provider")

        if provider is not None:
            return provider(self.selected_categories())

        options = picker.get(u"options")

        return options if options is not None else self._choice_all

    @property
    def HasNoChoiceItems(self):
        return self._choice_items.Count == 0

    @property
    def NumberLabel(self):
        return (self._number or {}).get(u"label", u"")

    @property
    def NumberHint(self):
        return (self._number or {}).get(u"hint", u"")

    @property
    def HasNumberHint(self):
        return bool((self._number or {}).get(u"hint"))

    @property
    def NumberUnit(self):
        return (self._number or {}).get(u"unit", u"")

    @property
    def HasNumber(self):
        return self._number is not None

    @property
    def ResetLabel(self):
        return self.config.get(u"reset_label", DEFAULT_RESET_LABEL)

    @property
    def ResetHint(self):
        return self.config.get(u"reset_hint", u"")

    @property
    def HasResetHint(self):
        return bool(self.config.get(u"reset_hint"))

    @property
    def HasReset(self):
        u"""Ключ reset=False убирает галочку «Сбросить»: инструменту,
        который ничего не окрашивает, она не нужна."""
        return bool(self.config.get(u"reset", True))

    @property
    def HasExtras(self):
        u"""Нижний блок нужен, только если в нём что-то есть."""
        return self.HasReset or bool(self._switch_defs)

    @property
    def RequireSwitch(self):
        u"""require_switch=True: нужна хотя бы одна отмеченная галочка.
        Для окна, где галочки — не дополнение, а сам предмет выбора."""
        return bool(self._switch_defs) and bool(self.config.get(u"require_switch", False))

    @property
    def RunLabel(self):
        if self._reset:
            return self.config.get(u"reset_run_label", u"Сбросить")

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
        if self.HasCategories and not self.selected_categories():
            return False

        if self.RequireSwitch and not any(self._switches.values()):
            return False

        if self.HasNumber and self.number_value() is None:
            return False

        if self.HasChoice and self._choice.get(u"required", True) and not self.choice_value():
            return False

        return True

    # ---------- значения --------------------------------------------

    def selected_categories(self):
        return [self._categories[i]
                for i in range(len(self._categories))
                if self._checked[i]]

    def number_value(self):
        if not self.HasNumber:
            return None

        value = parse_number(self._number_text)

        if value is None:
            return None

        low = self._number.get(u"min")
        high = self._number.get(u"max")

        if low is not None and value < low:
            return None

        if high is not None and value > high:
            return None

        return value

    def number_text(self):
        return self._number_text

    def choice_value(self):
        return self._choice_text.strip()

    def switch(self, key):
        return self._switches.get(key, False)

    # ---------- команды ---------------------------------------------

    def set_category(self, index, value):
        if 0 <= index < len(self._checked):
            self._checked[index] = bool(value)

        self.notify(u"CategoriesCounter")
        self.revalidate()

    def set_all_categories(self, value):
        for index in range(len(self._checked)):
            self._checked[index] = bool(value)

        self.notify(u"CategoriesCounter")
        self.revalidate()

    def is_category_checked(self, index):
        return self._checked[index]

    def set_switch(self, key, value):
        self._switches[key] = bool(value)
        self.revalidate()

    def set_choice(self, text):
        u"""Что в поле, то и применится: список — подсказка, а не ограничение."""
        self._choice_text = unicode(text or u"")
        self._refill_choice()
        self.revalidate()

    def _refill_choice(self):
        needle = self._choice_text.strip().lower()

        self._choice_items.Clear()

        for item in self._choice_all:
            if not needle or needle in item.lower():
                self._choice_items.Add(item)

        self.notify(u"HasNoChoiceItems")

    def choice_at(self, index):
        if 0 <= index < self._choice_items.Count:
            return unicode(self._choice_items[index])

        return None

    def set_number(self, text):
        self._number_text = text
        self.revalidate()

    def set_reset(self, value):
        self._reset = bool(value)
        self.notify(u"RunLabel")
        self.revalidate()

    def revalidate(self):
        if self.HasCategories and not self.selected_categories():
            self._set_status(u"Отметьте хотя бы одну категорию.", True)

        elif self.RequireSwitch and not any(self._switches.values()):
            self._set_status(
                self.config.get(u"require_switch_text",
                                u"Отметьте хотя бы одну галочку."),
                True
            )

        elif self.HasChoice and self._choice.get(u"required", True) and not self.choice_value():
            self._set_status(
                u"{}: выберите значение из списка или впишите своё.".format(
                    self.ChoiceLabel.capitalize()),
                True
            )

        elif self.HasNumber and self.number_value() is None:
            low = self._number.get(u"min")
            high = self._number.get(u"max")

            if low is not None and high is not None:
                self._set_status(
                    u"{}: введите число от {} до {}.".format(
                        self.NumberLabel.capitalize(),
                        format_number(low), format_number(high)
                    ),
                    True
                )
            else:
                self._set_status(u"{}: введите число.".format(self.NumberLabel.capitalize()), True)

        else:
            self._set_status(self._ready_text(), False)

        self.notify(u"IsValid")

    def _ready_text(self):
        u"""Строка состояния «всё готово». Инструменты, которые ничего не
        окрашивают, задают свой текст ключом ready_label."""
        if self._reset:
            action = self.config.get(u"reset_ready_label", u"Сброс переопределений")
        else:
            action = self.config.get(u"ready_label", u"Окраска")

        if self.HasCategories:
            names = u", ".join([unicode(item[0]) for item in self.selected_categories()])
            return u"{}: {}.".format(action, names)

        return u"{}.".format(action)

    def _set_status(self, text, is_error):
        self._status = text
        self._status_error = is_error
        self.notify(u"Status", u"StatusBrush")

    def result(self):
        return {
            u"categories": self.selected_categories(),
            u"reset": self._reset,
            u"number": self.number_value(),
            u"choice": self.choice_value() if self.HasChoice else None,
            u"switches": dict(self._switches),
        }


# ======================================================================
#  Окно
# ======================================================================

class PaintDialog(object):

    def __init__(self, config):
        xaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), XAML_FILE)

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = PaintDialogVM(config)
        self.window.DataContext = self.vm

        self.accepted = False

        self.window.Title = self.vm.Title

        self._build_categories()
        self._build_switches()
        self._fill_controls()
        self._wire()

        pp_wpf.set_owner(self.window)

    def _style(self, key):
        try:
            return Application.Current.Resources[key]
        except Exception:
            return None

    def _build_categories(self):
        u"""Флажки категорий строятся кодом: их состав задаёт вызывающий скрипт."""
        panel = self.window.FindName("PanelCategories")
        style = self._style(u"PP.CheckBox")

        self.boxes = []

        for index, (label, _payload) in enumerate(self.vm._categories):
            box = CheckBox()
            box.Content = unicode(label)
            box.IsChecked = self.vm.is_category_checked(index)
            box.Margin = Thickness(0, 0, 0, 8)

            if style is not None:
                box.Style = style

            panel.Children.Add(box)
            self.boxes.append(box)

        # Длинный список прокручивается, короткий тянется по содержимому
        if self.vm.HasCategoryActions:
            self.window.FindName("ScrollCategories").MaxHeight = 260

    def _build_switches(self):
        u"""Дополнительные галочки инструмента: у каждой свой ключ в результате."""
        panel = self.window.FindName("PanelSwitches")
        style = self._style(u"PP.CheckBox")

        self.switch_boxes = []

        for switch in self.vm._switch_defs:
            box = CheckBox()
            box.Content = switch[u"label"]
            box.IsChecked = self.vm.switch(switch[u"key"])
            box.Margin = Thickness(0, 0, 0, 10)

            if style is not None:
                box.Style = style

            panel.Children.Add(box)

            hint = switch.get(u"hint")

            if hint:
                block = TextBlock()
                block.Text = hint
                block.Style = self._style(u"Caption")
                block.Margin = Thickness(23, -6, 0, 10)
                panel.Children.Add(block)

            self.switch_boxes.append((switch[u"key"], box))

    def _fill_controls(self):
        find = self.window.FindName

        find("ChkReset").IsChecked = self.vm._reset

        if self.vm.HasNumber:
            find("TxtNumber").Text = self.vm.number_text()

        if self.vm.HasChoice:
            find("TxtChoice").Text = self.vm.choice_value()

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        # Фабрика обработчиков: общий цикл с lambda отдал бы всем флажкам
        # последний индекс.
        def make_category_handler(index):
            @guard
            def handler(sender, args):
                self.vm.set_category(index, sender.IsChecked)

            return handler

        for index in range(len(self.boxes)):
            handler = make_category_handler(index)
            self.boxes[index].Checked += handler
            self.boxes[index].Unchecked += handler

        if self.vm.HasCategoryActions:
            @guard
            def on_check_all(sender, args):
                self.vm.set_all_categories(True)
                self._sync_categories()

            @guard
            def on_uncheck_all(sender, args):
                self.vm.set_all_categories(False)
                self._sync_categories()

            find("BtnCheckAll").Click += on_check_all
            find("BtnUncheckAll").Click += on_uncheck_all

        def make_switch_handler(key):
            @guard
            def handler(sender, args):
                self.vm.set_switch(key, sender.IsChecked)

            return handler

        for key, box in self.switch_boxes:
            handler = make_switch_handler(key)
            box.Checked += handler
            box.Unchecked += handler

        if self.vm.HasChoice:
            @guard
            def on_choice_text(sender, args):
                self.vm.set_choice(sender.Text)

            @guard
            def on_choice_pick(sender, args):
                value = self.vm.choice_at(sender.SelectedIndex)

                if value is None:
                    return

                # Правим поле — обработчик TextChanged сам обновит VM
                find("TxtChoice").Text = value

            find("TxtChoice").TextChanged += on_choice_text
            find("LstChoice").SelectionChanged += on_choice_pick

            if self.vm.HasChoicePicker:
                @guard
                def on_choice_picker(sender, args):
                    picker = self.vm.choice_picker_config()

                    name = pp_param_picker.ask(
                        self.vm.choice_picker_options(),
                        current=self.vm.choice_value(),
                        title=picker.get(u"title"),
                        subtitle=picker.get(u"subtitle"),
                        owner=self.window,
                        empty_text=picker.get(u"empty_text")
                    )

                    if name:
                        # Правим поле — обработчик TextChanged сам обновит VM
                        find("TxtChoice").Text = name

                find("BtnChoicePick").Click += on_choice_picker

        @guard
        def on_reset(sender, args):
            self.vm.set_reset(sender.IsChecked)

        reset_box = find("ChkReset")
        reset_box.Checked += on_reset
        reset_box.Unchecked += on_reset

        if self.vm.HasNumber:
            @guard
            def on_number(sender, args):
                self.vm.set_number(sender.Text)

            find("TxtNumber").TextChanged += on_number

        @guard
        def on_run(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    def _sync_categories(self):
        u"""Кнопки «Отметить все» меняют состояние в VM — флажки нужно догнать."""
        for index in range(len(self.boxes)):
            self.boxes[index].IsChecked = self.vm.is_category_checked(index)

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

        return self.vm.result()


def ask(config):
    u"""Показать диалог. Возврат: словарь параметров или None при отказе."""
    return PaintDialog(config).show()

# -*- coding: utf-8 -*-
u"""Окно инструмента «Расход из пространства» (панель «Расчеты»).

Окно ничего не знает про Revit: скрипт передаёт ему сохранённые настройки,
число уже выделенных пространств и функцию, которая по запросу отдаёт список
параметров проекта для кнопки «Выбрать…».

    from pp_airflow_window import ask_options

    opts = ask_options(config, param_provider, selected_count)

    if opts is None:
        ...                      # пользователь закрыл окно
    else:
        opts[u"terminal_flow_param"]   # имена параметров, строки
        opts[u"exhaust_prefixes"]      # строка «В, ВЕ» — разбирает скрипт
        opts[u"write_counts"]          # bool

`param_provider(kind)` вызывается в момент нажатия кнопки «Выбрать…»,
kind — u"space" или u"terminal". Возврат — список для pp_param_picker.
"""

import os

import pp_wpf
import pp_param_picker

from System.Windows import Application


# key настройки -> (имя TextBox, имя кнопки, что показываем в окне выбора)
PARAM_FIELDS = [
    (u"exhaust_total_param", "TxtExhaustTotal", "BtnExhaustTotal", u"space",
     u"Общая вытяжка пространства"),
    (u"supply_total_param", "TxtSupplyTotal", "BtnSupplyTotal", u"space",
     u"Общий приток пространства"),
    (u"exhaust_count_param", "TxtExhaustCount", "BtnExhaustCount", u"space",
     u"Количество вытяжных решёток"),
    (u"supply_count_param", "TxtSupplyCount", "BtnSupplyCount", u"space",
     u"Количество приточных решёток"),
    (u"terminal_flow_param", "TxtTerminalFlow", "BtnTerminalFlow", u"terminal",
     u"Расход воздуха решётки"),
]

TEXT_FIELDS = [
    (u"exhaust_prefixes", "TxtExhaustPrefixes"),
    (u"supply_prefixes", "TxtSupplyPrefixes"),
    (u"exclude_words", "TxtExcludeWords"),
]

COUNT_KEYS = (u"exhaust_count_param", u"supply_count_param")


def split_list(text):
    u"""«В, ВЕ» -> [u"В", u"ВЕ"]. Пустые куски отбрасываются."""
    if not text:
        return []

    parts = []

    for chunk in unicode(text).replace(u";", u",").split(u","):
        chunk = chunk.strip()

        if chunk:
            parts.append(chunk)

    return parts


def plural_spaces(count):
    count = abs(int(count))

    if count % 10 == 1 and count % 100 != 11:
        return u"пространство"

    if count % 10 in (2, 3, 4) and not (11 <= count % 100 <= 14):
        return u"пространства"

    return u"пространств"


class AirflowVM(pp_wpf.Notifier):
    u"""Состояние окна и проверка ввода."""

    def __init__(self, config, selected_count):
        pp_wpf.Notifier.__init__(self)

        self.values = {}

        for key, _box, _btn, _kind, _title in PARAM_FIELDS:
            self.values[key] = unicode(config.get(key, u"") or u"")

        for key, _box in TEXT_FIELDS:
            self.values[key] = unicode(config.get(key, u"") or u"")

        self._write_counts = bool(config.get(u"write_counts", True))
        self.selected_count = int(selected_count or 0)

        self._status = u""
        self._status_error = False
        self._valid = False
        self._result_text = u""
        self._result_muted = True

        self.revalidate()

    # -- свойства для биндингов -------------------------------------
    @property
    def Status(self):
        return self._status

    @property
    def StatusBrush(self):
        return self._brush(u"Hot" if self._status_error else u"Muted")

    @property
    def ResultText(self):
        return self._result_text

    @property
    def ResultBrush(self):
        return self._brush(u"Muted" if self._result_muted else u"Ink")

    @property
    def RunLabel(self):
        return u"Рассчитать" if self.selected_count else u"Далее"

    @property
    def IsValid(self):
        return self._valid

    @property
    def WriteCounts(self):
        return self._write_counts

    def _brush(self, key):
        try:
            return Application.Current.Resources[key]
        except Exception:
            return None

    # -- изменения из окна ------------------------------------------
    def set_value(self, key, text):
        self.values[key] = unicode(text or u"").strip()
        self.revalidate()

    def set_write_counts(self, value):
        self._write_counts = bool(value)
        self.revalidate()

    def set_error(self, message):
        self._set_status(message, True)

    # -- проверка ввода ---------------------------------------------
    def exhaust_ready(self):
        return bool(self.values[u"exhaust_total_param"]
                    and split_list(self.values[u"exhaust_prefixes"]))

    def supply_ready(self):
        return bool(self.values[u"supply_total_param"]
                    and split_list(self.values[u"supply_prefixes"]))

    def revalidate(self):
        problem = self._find_problem()

        self._valid = problem is None

        if problem is not None:
            self._set_status(problem, True)
            self._set_result(
                u"Заполните то, о чём написано слева внизу, — тогда кнопка станет активной.",
                True
            )
        else:
            self._set_status(u"", False)
            self._set_result(self._describe(), False)

        self.notify(u"IsValid", u"RunLabel")

    def _find_problem(self):
        if not self.values[u"terminal_flow_param"]:
            return u"Укажите параметр решётки, в который записывается расход."

        if not self.exhaust_ready() and not self.supply_ready():
            return (u"Заполните параметр расхода и префиксы систем хотя бы для "
                    u"вытяжки или для притока.")

        if self._write_counts:
            if self.exhaust_ready() and not self.values[u"exhaust_count_param"]:
                return (u"Укажите параметр количества вытяжных решёток или снимите "
                        u"галочку записи количества.")

            if self.supply_ready() and not self.values[u"supply_count_param"]:
                return (u"Укажите параметр количества приточных решёток или снимите "
                        u"галочку записи количества.")

        return None

    def _describe(self):
        parts = []

        if self.exhaust_ready():
            parts.append(u"вытяжка систем {}".format(
                u", ".join(split_list(self.values[u"exhaust_prefixes"]))
            ))

        if self.supply_ready():
            parts.append(u"приток систем {}".format(
                u", ".join(split_list(self.values[u"supply_prefixes"]))
            ))

        what = u" и ".join(parts)

        if self.selected_count:
            head = u"Выделено {} {}.".format(
                self.selected_count, plural_spaces(self.selected_count)
            )
        else:
            head = u"Пространства выберете на виде — «Готово» завершит выбор."

        return u"{} Расход разложится по решёткам: {}.".format(head, what)

    def _set_status(self, message, is_error):
        self._status = message
        self._status_error = is_error
        self.notify(u"Status", u"StatusBrush")

    def _set_result(self, text, muted):
        self._result_text = text
        self._result_muted = muted
        self.notify(u"ResultText", u"ResultBrush")

    # -- результат ---------------------------------------------------
    def result(self):
        data = dict(self.values)
        data[u"write_counts"] = self._write_counts
        return data


class AirflowWindow(object):
    u"""Загрузка разметки, подписки, модальный показ."""

    def __init__(self, config, param_provider, selected_count):
        xaml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            u"ui.xaml"
        )

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = AirflowVM(config, selected_count)
        self.param_provider = param_provider
        self.window.DataContext = self.vm
        self.accepted = False

        self._fill_controls()
        self._wire()
        self._sync_counts()

        pp_wpf.set_owner(self.window)
        pp_wpf.fit_to_screen(self.window)

    def _fill_controls(self):
        for key, box_name, _btn, _kind, _title in PARAM_FIELDS:
            self.window.FindName(box_name).Text = self.vm.values[key]

        for key, box_name in TEXT_FIELDS:
            self.window.FindName(box_name).Text = self.vm.values[key]

        self.window.FindName("ChkWriteCounts").IsChecked = self.vm.WriteCounts

    def _sync_counts(self):
        u"""Поля количества гаснут, когда количество не записывается."""
        enabled = self.vm.WriteCounts

        for key, box_name, btn_name, _kind, _title in PARAM_FIELDS:
            if key in COUNT_KEYS:
                self.window.FindName(box_name).IsEnabled = enabled
                self.window.FindName(btn_name).IsEnabled = enabled

    def _wire(self):
        find = self.window.FindName
        guarded = pp_wpf.guard(self.vm.set_error)

        # Фабрика обработчиков: общий цикл с lambda отдал бы всем полям
        # последний ключ.
        def make_text_handler(key):
            @guarded
            def handler(sender, args):
                self.vm.set_value(key, sender.Text)

            return handler

        def make_pick_handler(key, box_name, kind, title):
            @guarded
            def handler(sender, args):
                self._pick(key, box_name, kind, title)

            return handler

        for key, box_name, btn_name, kind, title in PARAM_FIELDS:
            find(box_name).TextChanged += make_text_handler(key)
            find(btn_name).Click += make_pick_handler(key, box_name, kind, title)

        for key, box_name in TEXT_FIELDS:
            find(box_name).TextChanged += make_text_handler(key)

        @guarded
        def on_counts(sender, args):
            self.vm.set_write_counts(sender.IsChecked)
            self._sync_counts()

        chk = find("ChkWriteCounts")
        chk.Checked += on_counts
        chk.Unchecked += on_counts

        @guarded
        def on_run(sender, args):
            self._accept()

        @guarded
        def on_cancel(sender, args):
            self.window.Close()

        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(
            self.window,
            on_accept=self._accept,
            on_cancel=self.window.Close
        )

    def _pick(self, key, box_name, kind, title):
        options = []

        if self.param_provider is not None:
            options = self.param_provider(kind) or []

        if kind == u"space":
            subtitle = u"Параметры проекта, привязанные к пространствам."
            empty = (u"Параметров пространств не нашлось. Закройте окно и впишите "
                     u"имя вручную.")
        else:
            subtitle = u"Параметры экземпляра воздухораспределителей."
            empty = (u"Параметров решёток не нашлось. Закройте окно и впишите "
                     u"имя вручную.")

        selected = pp_param_picker.ask(
            options,
            current=self.vm.values.get(key, u""),
            title=title,
            subtitle=subtitle,
            owner=self.window,
            empty_text=empty
        )

        if selected:
            self.window.FindName(box_name).Text = selected

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


def ask_options(config, param_provider=None, selected_count=0):
    u"""Показать окно. Возврат: словарь настроек или None, если отменили."""
    return AirflowWindow(config, param_provider, selected_count).show()

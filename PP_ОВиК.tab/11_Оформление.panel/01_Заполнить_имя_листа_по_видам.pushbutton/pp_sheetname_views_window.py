# -*- coding: utf-8 -*-
u"""Окно инструмента «Заполнить имя листа по видам».

Сверху начало имени, слева найденные системы галочками, справа имена видов,
внизу живой предпросмотр итогового имени листа. Модальное.

Список систем — общий контрол из lib/pp_check_list.
"""

import os

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

from System.Collections.ObjectModel import ObservableCollection
from System.Windows import Application

import pp_check_list


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

class SheetNameVM(pp_wpf.Notifier):

    def __init__(self, view_names, prefixes, build_name):
        pp_wpf.Notifier.__init__(self)

        # build_name(префикс, [системы]) -> итоговое имя. Логика сборки живёт
        # в скрипте инструмента, окно только показывает результат.
        self.build_name = build_name

        self.prefixes = list(prefixes or [])

        self._views = ObservableCollection[object]()

        for name in (view_names or []):
            self._views.Add(unicode(name))

        self._prefix = self.prefixes[0] if self.prefixes else u""
        self._systems = []

        self._status = u""
        self._status_error = False

    # ---------- списки ---------------------------------------------------

    @property
    def ViewNames(self):
        return self._views

    @property
    def HasNoViews(self):
        return self._views.Count == 0

    @property
    def HasNoPrefixes(self):
        return len(self.prefixes) == 0

    # ---------- предпросмотр -----------------------------------------------

    @property
    def Preview(self):
        if not self.prefix:
            return u"Впишите начало имени листа."

        if not self._systems:
            return u"Отметьте хотя бы одну систему."

        return self.build_name(self.prefix, self._systems)

    @property
    def PreviewBrush(self):
        u"""Готовое имя — обычным текстом, подсказка «что сделать» — приглушённо."""
        return brush(u"Ink" if self.IsValid else u"Muted")

    @property
    def prefix(self):
        return unicode(self._prefix or u"").strip()

    def set_prefix(self, text):
        self._prefix = text if text is not None else u""
        self.revalidate()

    def set_systems(self, systems):
        self._systems = list(systems or [])
        self.revalidate()

    def result(self):
        return self.prefix, list(self._systems), self.Preview

    # ---------- статус --------------------------------------------------------

    @property
    def Status(self):
        return self._status

    @property
    def StatusBrush(self):
        return brush(u"Hot" if self._status_error else u"Muted")

    @property
    def IsValid(self):
        return bool(self.prefix) and bool(self._systems)

    def revalidate(self):
        u"""Единственное место, где решается, готово окно к записи или нет."""
        if not self.prefix:
            self.set_status(u"Укажите начало имени листа.", True)

        elif not self._systems:
            self.set_status(u"Отметьте хотя бы одну систему.", True)

        else:
            count = len(self._systems)

            self.set_status(
                u"В имя войдёт {} {}.".format(
                    count, plural(count, (u"система", u"системы", u"систем"))),
                False
            )

        self.notify(u"Preview", u"PreviewBrush", u"IsValid")

    def set_status(self, text, is_error=False):
        self._status = text
        self._status_error = bool(is_error)
        self.notify(u"Status", u"StatusBrush")


# ======================================================================
#  Окно
# ======================================================================

class SheetNameWindow(object):

    def __init__(self, script_dir, config):
        self.window = pp_wpf.load_window_file(os.path.join(script_dir, u"ui.xaml"))

        # config: view_names, prefixes, systems, system_label, build_name
        self.config = config

        self.vm = SheetNameVM(
            config.get(u"view_names"),
            config.get(u"prefixes"),
            config[u"build_name"]
        )

        self.window.DataContext = self.vm
        self.accepted = False

        self._build_systems()
        self._build_prefixes()
        self._fill_controls()
        self._wire()

        pp_wpf.set_owner(self.window)
        pp_wpf.fit_to_screen(self.window)

        self._on_systems_changed()

    # ---------- построение -------------------------------------------------

    def _build_systems(self):
        label = self.config.get(u"system_label") or unicode

        items = [(label(item), item) for item in (self.config.get(u"systems") or [])]

        self.systems = pp_check_list.CheckList(
            items,
            empty_text=u"Систем не найдено. Измените запрос.",
            on_change=self._on_systems_changed,
            search_hint=u"Поиск по номеру системы — например В1"
        )

        # По умолчанию в имя идут все найденные системы — как было раньше
        self.systems.set_checked([name for name, _payload in items])

        self.window.FindName("HostSystems").Content = self.systems.element

    def _build_prefixes(self):
        box = self.window.FindName("LstPrefixes")

        for value in self.vm.prefixes:
            box.Items.Add(value)

    def _fill_controls(self):
        self.window.FindName("TxtPrefix").Text = self.vm.prefix
        self._highlight_prefix(self.vm.prefix)

    def _highlight_prefix(self, text):
        u"""Подсвечиваем вариант, который сейчас в поле; правка руками — снимаем.

        Видно, что выбрано: список и поле не расходятся.
        """
        box = self.window.FindName("LstPrefixes")
        value = unicode(text or u"").strip()

        for index, prefix in enumerate(self.vm.prefixes):
            if prefix == value:
                if box.SelectedIndex != index:
                    box.SelectedIndex = index

                return

        box.SelectedIndex = -1

    # ---------- подписки ------------------------------------------------------

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        @guard
        def on_prefix_text(sender, args):
            self.vm.set_prefix(sender.Text)
            self._highlight_prefix(sender.Text)

        @guard
        def on_prefix_pick(sender, args):
            if sender.SelectedItem is None:
                return

            # Правим поле — обработчик TextChanged сам обновит VM
            find("TxtPrefix").Text = unicode(sender.SelectedItem)

        find("TxtPrefix").TextChanged += on_prefix_text
        find("LstPrefixes").SelectionChanged += on_prefix_pick

        @guard
        def on_run(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    def _on_systems_changed(self):
        self.vm.set_systems([payload for _name, payload in self.systems.get_checked()])

    # ---------- результат ------------------------------------------------------

    def _accept(self):
        if not self.vm.IsValid:
            return

        self.accepted = True
        self.window.DialogResult = True

    def _fail(self, message):
        self.vm.set_status(message, True)

    def show(self):
        u"""Возврат: (префикс, системы, имя) или None при отказе."""
        self.window.ShowDialog()

        if not self.accepted:
            return None

        return self.vm.result()


def ask(script_dir, config):
    return SheetNameWindow(script_dir, config).show()

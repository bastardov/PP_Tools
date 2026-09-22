# -*- coding: utf-8 -*-
u"""Окно инструмента «Полутона».

Слева шаблоны видов, справа категории. Модальное: собирает выбор и
закрывается. Оба списка — общий контрол из lib/pp_check_list.
"""

import os

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

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

class HalftoneVM(pp_wpf.Notifier):

    def __init__(self):
        pp_wpf.Notifier.__init__(self)

        self._templates = 0
        self._categories = 0

        self._status = u""
        self._status_error = False

    # ---------- состояние -----------------------------------------------

    @property
    def Status(self):
        return self._status

    @property
    def StatusBrush(self):
        return brush(u"Hot" if self._status_error else u"Muted")

    @property
    def IsValid(self):
        return self._templates > 0 and self._categories > 0

    def set_counts(self, templates, categories):
        self._templates = templates
        self._categories = categories
        self.revalidate()

    def revalidate(self):
        u"""Единственное место, где решается, готово окно к запуску или нет."""
        if self._templates == 0:
            self.set_status(
                u"Отметьте слева шаблоны видов, к которым применить полутон.", True)

        elif self._categories == 0:
            self.set_status(
                u"Отметьте справа категории, которым нужен полутон.", True)

        else:
            self.set_status(
                u"Полутон для {} {} в {} {}.".format(
                    self._categories,
                    plural(self._categories,
                           (u"категории", u"категорий", u"категорий")),
                    self._templates,
                    plural(self._templates,
                           (u"шаблоне", u"шаблонах", u"шаблонах"))
                ),
                False
            )

        self.notify(u"IsValid")

    def set_status(self, text, is_error=False):
        self._status = text
        self._status_error = bool(is_error)
        self.notify(u"Status", u"StatusBrush")


# ======================================================================
#  Окно
# ======================================================================

class HalftoneWindow(object):

    def __init__(self, script_dir, config):
        self.window = pp_wpf.load_window_file(os.path.join(script_dir, u"ui.xaml"))

        # config: templates, categories, saved_templates, saved_categories
        self.config = config

        self.vm = HalftoneVM()
        self.window.DataContext = self.vm

        self.accepted = False

        self._build_lists()
        self._fill_saved()
        self._wire()

        pp_wpf.set_owner(self.window)

        self._on_change()

    # ---------- построение -----------------------------------------------

    def _build_lists(self):
        self.tpl_list = pp_check_list.CheckList(
            self.config.get(u"templates") or [],
            empty_text=u"Шаблонов видов не найдено. Измените запрос.",
            on_change=self._on_change
        )

        self.cat_list = pp_check_list.CheckList(
            self.config.get(u"categories") or [],
            empty_text=u"Категорий не найдено. Измените запрос.",
            on_change=self._on_change
        )

        self.window.FindName("HostTemplates").Content = self.tpl_list.element
        self.window.FindName("HostCategories").Content = self.cat_list.element

    def _fill_saved(self):
        self.tpl_list.set_checked(self.config.get(u"saved_templates") or [])
        self.cat_list.set_checked(self.config.get(u"saved_categories") or [])

    # ---------- подписки ---------------------------------------------------

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        @guard
        def on_run(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    def _on_change(self):
        self.vm.set_counts(self.tpl_list.count(), self.cat_list.count())

    # ---------- результат ---------------------------------------------------

    def _accept(self):
        if not self.vm.IsValid:
            return

        self.accepted = True
        self.window.DialogResult = True

    def _fail(self, message):
        self.vm.set_status(message, True)

    def show(self):
        u"""Возврат: (шаблоны, категории) или (None, None) при отказе."""
        self.window.ShowDialog()

        if not self.accepted:
            return None, None

        templates = [payload for _name, payload in self.tpl_list.get_checked()]
        categories = [payload for _name, payload in self.cat_list.get_checked()]

        return templates, categories

    def checked_names(self):
        u"""Что запомнить для следующего запуска."""
        return (
            [name for name, _payload in self.tpl_list.get_checked()],
            [name for name, _payload in self.cat_list.get_checked()],
        )


def ask(script_dir, config):
    u"""Показать окно. Возврат: (окно, шаблоны, категории)."""
    window = HalftoneWindow(script_dir, config)
    templates, categories = window.show()

    return window, templates, categories

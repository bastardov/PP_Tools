# -*- coding: utf-8 -*-
u"""Окно инструмента «Связи в шаблоне».

Слева — к чему применяем (шаблоны видов или виды, переключаются чипсами),
справа — желаемое состояние связей. Модальное: собирает выбор и закрывается.

Списки с поиском — общий контрол из lib/pp_check_list.
"""

import os

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

from System.Windows import Application, Thickness, Visibility
from System.Windows.Controls import CheckBox

import pp_check_list


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

class LinksVM(pp_wpf.Notifier):

    def __init__(self, links_count):
        pp_wpf.Notifier.__init__(self)

        self._side = u"templates"

        self.links_count = links_count
        self.visible_count = 0

        self._targets = 0

        self._status = u""
        self._status_error = False

    # ---------- переключатель ------------------------------------------

    @property
    def IsTemplates(self):
        # «Шаблоны видов» и «Шаблоны видов ПЭ» — один и тот же список,
        # у второго просто включён фильтр по плановым видам.
        return self._side in (u"templates", u"plans")

    @property
    def IsViews(self):
        return self._side == u"views"

    def set_side(self, side):
        self._side = side
        self.notify(u"IsTemplates", u"IsViews")

    # ---------- счётчики -------------------------------------------------

    @property
    def LinksCounter(self):
        return u"Видимыми останутся {} из {}.".format(
            self.visible_count, self.links_count)

    def set_visible_count(self, count):
        self.visible_count = count
        self.notify(u"LinksCounter")

    def set_targets(self, count):
        self._targets = count
        self.revalidate()

    # ---------- статус ----------------------------------------------------

    @property
    def Status(self):
        return self._status

    @property
    def StatusBrush(self):
        return brush(u"Hot" if self._status_error else u"Muted")

    @property
    def IsValid(self):
        return self._targets > 0

    def revalidate(self):
        if self._targets == 0:
            self.set_status(
                u"Отметьте слева хотя бы один шаблон или вид.", True)
        else:
            self.set_status(
                u"Применить к {} {}: видимыми останутся {} из {}.".format(
                    self._targets,
                    plural(self._targets, (u"объекту", u"объектам", u"объектам")),
                    self.visible_count, self.links_count
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

class LinksWindow(object):

    def __init__(self, script_dir, config):
        self.window = pp_wpf.load_window_file(os.path.join(script_dir, u"ui.xaml"))

        # config: templates, views, links, saved_*, on_current_view, link_state
        self.config = config

        self.entries = list(config.get(u"links") or [])

        self.vm = LinksVM(len(self.entries))
        self.window.DataContext = self.vm

        self.accepted = False

        self._build_lists()
        self._build_links()
        self._fill_saved()
        self._wire()

        pp_wpf.set_owner(self.window)

        self._on_targets_changed()

    # ---------- построение ---------------------------------------------

    def _build_lists(self):
        self.tpl_list = pp_check_list.CheckList(
            self.config.get(u"templates") or [],
            empty_text=u"Шаблонов видов не найдено. Измените запрос.",
            on_change=self._on_targets_changed
        )

        self.view_list = pp_check_list.CheckList(
            self.config.get(u"views") or [],
            extra_button=(u"Текущий вид", self._on_current_view),
            empty_text=u"Видов не найдено. Измените запрос.",
            on_change=self._on_targets_changed
        )

        self.window.FindName("HostTemplates").Content = self.tpl_list.element
        self.window.FindName("HostViews").Content = self.view_list.element

        # Чипс «Шаблоны видов ПЭ» имеет смысл только если есть чем фильтровать
        if self.config.get(u"is_plan") is None:
            self.window.FindName("ChipPlanTemplates").Visibility = Visibility.Collapsed

    def _plan_filter(self, _name, payload):
        u"""Фильтр списка шаблонов: оставить только плановые виды."""
        checker = self.config.get(u"is_plan")

        if checker is None:
            return True

        try:
            return bool(checker(payload))
        except Exception:
            return False

    def _build_links(self):
        panel = self.window.FindName("PanelLinks")
        check_style = style(u"PP.CheckBox")

        self.link_boxes = []

        for label, _payload in self.entries:
            box = CheckBox()
            box.Content = label
            box.Margin = Thickness(0, 0, 0, 8)

            if check_style is not None:
                box.Style = check_style

            box.Checked += self._on_link_changed
            box.Unchecked += self._on_link_changed

            panel.Children.Add(box)
            self.link_boxes.append(box)

    # ---------- начальное состояние ----------------------------------------

    def _fill_saved(self):
        find = self.window.FindName

        find("ChipTemplates").IsChecked = True

        self.tpl_list.set_checked(self.config.get(u"saved_templates") or [])
        self.view_list.set_checked(self.config.get(u"saved_views") or [])

        saved_visible = self.config.get(u"saved_visible_links") or []

        if saved_visible:
            for index, (label, _payload) in enumerate(self.entries):
                self.link_boxes[index].IsChecked = label in saved_visible
        else:
            # Сохранённого выбора нет — берём фактическое состояние активного вида
            self.apply_state_from_view()

        self._update_visible_count()

    def apply_state_from_view(self):
        u"""Ставит галочки по фактическому состоянию связей в активном виде."""
        state = self.config.get(u"link_state")

        if state is None:
            return

        for index, (_label, payload) in enumerate(self.entries):
            self.link_boxes[index].IsChecked = bool(state(payload))

    # ---------- подписки ------------------------------------------------------

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        @guard
        def on_templates(sender, args):
            self.vm.set_side(u"templates")
            self.tpl_list.set_filter(None)

        @guard
        def on_plan_templates(sender, args):
            self.vm.set_side(u"plans")
            self.tpl_list.set_filter(self._plan_filter)

        @guard
        def on_views(sender, args):
            self.vm.set_side(u"views")

        find("ChipTemplates").Checked += on_templates
        find("ChipPlanTemplates").Checked += on_plan_templates
        find("ChipViews").Checked += on_views

        @guard
        def on_all(sender, args):
            self._set_all_links(True)

        @guard
        def on_none(sender, args):
            self._set_all_links(False)

        @guard
        def on_current(sender, args):
            self.apply_state_from_view()
            self._update_visible_count()
            self.vm.set_status(u"Состояние связей взято с активного вида.")

        find("BtnLinksAll").Click += on_all
        find("BtnLinksNone").Click += on_none
        find("BtnLinksCurrent").Click += on_current

        @guard
        def on_run(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    # ---------- реакция на ввод -------------------------------------------------

    def _set_all_links(self, value):
        for box in self.link_boxes:
            box.IsChecked = value

    def _on_link_changed(self, sender, args):
        self._update_visible_count()

    def _update_visible_count(self):
        count = len([box for box in self.link_boxes if box.IsChecked])
        self.vm.set_visible_count(count)
        self.vm.revalidate()

    def _on_targets_changed(self):
        self.vm.set_targets(self.tpl_list.count() + self.view_list.count())

    def _on_current_view(self, sender, args):
        u"""Кнопка «Текущий вид» под списком видов."""
        handler = self.config.get(u"on_current_view")

        if handler is None:
            return

        name, message = handler()

        if name is None:
            self.vm.set_status(message, True)
            return

        self.window.FindName("ChipViews").IsChecked = True
        self.view_list.clear_search()
        self.view_list.set_checked([name])

        self._on_targets_changed()
        self.vm.set_status(u"Выбран текущий вид: {}.".format(name))

    # ---------- результат ---------------------------------------------------------

    def _accept(self):
        if not self.vm.IsValid:
            return

        self.accepted = True
        self.window.DialogResult = True

    def _fail(self, message):
        self.vm.set_status(message, True)

    def show(self):
        u"""Возврат: (цели, флаги видимости связей) или (None, None) при отказе."""
        self.window.ShowDialog()

        if not self.accepted:
            return None, None

        targets = [payload for _name, payload in self.tpl_list.get_checked()]
        targets += [payload for _name, payload in self.view_list.get_checked()]

        visible = [bool(box.IsChecked) for box in self.link_boxes]

        return targets, visible

    def checked_names(self):
        u"""Что запомнить для следующего запуска."""
        return (
            [name for name, _payload in self.tpl_list.get_checked()],
            [name for name, _payload in self.view_list.get_checked()],
            [self.entries[i][0] for i in range(len(self.entries))
             if self.link_boxes[i].IsChecked],
        )


def ask(script_dir, config):
    u"""Показать окно. Возврат: (окно, цели, флаги) — окно нужно для сохранения выбора."""
    window = LinksWindow(script_dir, config)
    targets, visible = window.show()

    return window, targets, visible

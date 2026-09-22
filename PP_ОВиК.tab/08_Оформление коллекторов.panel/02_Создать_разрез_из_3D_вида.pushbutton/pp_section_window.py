# -*- coding: utf-8 -*-
u"""Окно выбора стороны и шаблона для создания разреза из 3D вида."""

import os

import pp_wpf

from System.Windows import Application


VALID_SIDES = ("front", "back", "left", "right")
NO_TEMPLATE_ID = -1

SIDE_LABELS = {
    "front": u"спереди",
    "back": u"сзади",
    "left": u"слева",
    "right": u"справа",
}

SIDE_TITLES = {
    "front": u"Выбрана передняя грань",
    "back": u"Выбрана задняя грань",
    "left": u"Выбрана левая грань",
    "right": u"Выбрана правая грань",
}

SIDE_HINTS = {
    "front": u"Разрез будет смотреть от передней грани внутрь 3D-коробки.",
    "back": u"Разрез будет смотреть от задней грани внутрь 3D-коробки.",
    "left": u"Разрез будет смотреть от левой грани внутрь 3D-коробки.",
    "right": u"Разрез будет смотреть от правой грани внутрь 3D-коробки.",
}


class SectionSideVM(pp_wpf.Notifier):

    def __init__(self, templates, side="front", template_id=NO_TEMPLATE_ID):
        pp_wpf.Notifier.__init__(self)

        self.templates = templates
        self._side = side if side in VALID_SIDES else "front"
        self._template_id = self._normalize_template_id(template_id)
        self._status = u""
        self._status_error = False
        self._update_status()

    @property
    def IsFront(self):
        return self._side == "front"

    @property
    def IsBack(self):
        return self._side == "back"

    @property
    def IsLeft(self):
        return self._side == "left"

    @property
    def IsRight(self):
        return self._side == "right"

    @property
    def IsValid(self):
        return not self._status_error and self._side in VALID_SIDES

    @property
    def SelectedSideTitle(self):
        return SIDE_TITLES[self._side]

    @property
    def SelectedSideHint(self):
        return SIDE_HINTS[self._side]

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
    def side(self):
        return self._side

    @property
    def template_id(self):
        return self._template_id

    def set_side(self, side):
        if side not in VALID_SIDES:
            self.set_error(u"Неизвестная сторона разреза.")
            return

        self._side = side
        self._status_error = False
        self._update_status()
        self.notify(
            u"IsFront",
            u"IsBack",
            u"IsLeft",
            u"IsRight",
            u"SelectedSideTitle",
            u"SelectedSideHint",
            u"IsValid"
        )

    def set_template_id(self, template_id):
        self._template_id = self._normalize_template_id(template_id)
        self._status_error = False
        self._update_status()
        self.notify(u"IsValid")

    def set_error(self, message):
        self._status = message
        self._status_error = True
        self.notify(u"Status", u"StatusBrush", u"IsValid")

    def _update_status(self):
        template_name = self._template_name()

        if template_name:
            template_text = u" Шаблон: «{}».".format(template_name)
        else:
            template_text = u" Шаблон вида не назначается."

        self._status = u"Разрез будет создан {} относительно границ 3D вида.{}".format(
            SIDE_LABELS[self._side],
            template_text
        )
        self.notify(u"Status", u"StatusBrush")

    def _normalize_template_id(self, template_id):
        for item in self.templates:
            if item["id"] == template_id:
                return template_id

        return NO_TEMPLATE_ID

    def _template_name(self):
        for item in self.templates:
            if item["id"] == self._template_id:
                return item["name"]

        return u""


class SectionSideWindow(object):

    def __init__(self, templates=None, side="front", template_id=NO_TEMPLATE_ID):
        xaml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            u"ui.xaml"
        )

        self.templates = [{"id": NO_TEMPLATE_ID, "name": u"Без шаблона вида"}]
        self.templates.extend(templates or [])

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = SectionSideVM(self.templates, side, template_id)
        self.window.DataContext = self.vm
        self.accepted = False

        self._fill_controls()
        self._wire()
        pp_wpf.set_owner(self.window)

    def _side_controls(self):
        find = self.window.FindName

        return [
            ("front", find("ChipFront")),
            ("back", find("ChipBack")),
            ("left", find("ChipLeft")),
            ("right", find("ChipRight")),
        ]

    def _fill_controls(self):
        for side, control in self._side_controls():
            control.IsChecked = (side == self.vm.side)

        combo = self.window.FindName("CmbTemplate")
        combo.ItemsSource = [item["name"] for item in self.templates]

        for index, item in enumerate(self.templates):
            if item["id"] == self.vm.template_id:
                combo.SelectedIndex = index
                break

    def _wire(self):
        find = self.window.FindName
        guarded = pp_wpf.guard(self.vm.set_error)

        def make_side_handler(side):
            @guarded
            def handler(sender, args):
                self.vm.set_side(side)

            return handler

        for side, control in self._side_controls():
            control.Checked += make_side_handler(side)

        @guarded
        def on_template_changed(sender, args):
            index = sender.SelectedIndex

            if index < 0 or index >= len(self.templates):
                self.vm.set_template_id(NO_TEMPLATE_ID)
                return

            self.vm.set_template_id(self.templates[index]["id"])

        find("CmbTemplate").SelectionChanged += on_template_changed

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

    def _accept(self):
        if not self.vm.IsValid:
            return

        self.accepted = True
        self.window.DialogResult = True

    def show(self):
        self.window.ShowDialog()

        if not self.accepted:
            return None

        return {
            "side": self.vm.side,
            "template_id": self.vm.template_id
        }


def ask_side(side="front"):
    result = SectionSideWindow(side=side).show()

    if result is None:
        return None

    return result["side"]


def ask_options(templates, template_id=NO_TEMPLATE_ID, side="front"):
    return SectionSideWindow(templates, side, template_id).show()

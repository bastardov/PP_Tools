# -*- coding: utf-8 -*-
u"""Окно кнопки «Разместить на листы»: планы + образец + раздел + нумерация.

Окно ничего не знает про Revit: списки планов и листов ему готовит
`script.py`, обратно уходит словарь настроек запуска (см. `ask`).
"""

import os
import re

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF
import pp_check_list

from System.Windows import Application, Thickness
from System.Windows.Controls import RadioButton


XAML_FILE = u"ui.xaml"

_NUMBER = re.compile(u"^\\s*(\\d+)\\s*$")


def brush(key):
    try:
        return Application.Current.Resources[key]
    except Exception:
        return None


def style(key):
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
#  Нумерация — чистые функции, ими же пользуется script.py
# ======================================================================

def suggest_start(taken, suffix):
    u"""Следующий свободный номер: max(N) + 1 среди номеров вида «N<суффикс>»."""
    suffix = unicode(suffix or u"")
    best = 0

    for number in taken:
        text = unicode(number or u"").strip()

        if suffix:
            if not text.endswith(suffix):
                continue
            text = text[:-len(suffix)]

        match = _NUMBER.match(text)
        if match:
            best = max(best, int(match.group(1)))

    return best + 1


def allocate_numbers(start, suffix, taken, count):
    u"""`count` свободных номеров начиная со `start`, занятые пропускаются."""
    taken = set(unicode(item or u"").strip() for item in (taken or []))
    suffix = unicode(suffix or u"")
    result = []
    number = max(1, int(start))

    while len(result) < count and number < 100000:
        candidate = u"{0}{1}".format(number, suffix)

        if candidate not in taken:
            result.append(candidate)
            taken.add(candidate)

        number += 1

    return result


# ======================================================================
#  Окно
# ======================================================================

class PlaceWindow(object):

    def __init__(self, folder, plans, sheets, taken_numbers, name_for,
                 defaults):
        u"""
        plans         — [(подпись, вид), ...]
        sheets        — [{sheet, number, name, section}, ...] (pp_sheet_picker)
        taken_numbers — множество занятых номеров листов
        name_for      — fn(вид) -> имя листа для предпросмотра
        defaults      — {sample_id, section, suffix, margins, portrait,
                         preselect}
        """
        self.window = pp_wpf.load_window_file(os.path.join(folder, XAML_FILE))
        self.plans = list(plans)
        self.sheets = list(sheets)
        self.taken = set(taken_numbers or [])
        self.name_for = name_for
        self.result = None
        self.chips = []
        self._start_auto = True
        self._syncing = False

        find = self.window.FindName

        self.list = pp_check_list.CheckList(
            self.plans,
            extra_button=(u"Текущий вид", self._on_current),
            on_change=self._refresh,
            search_hint=u"Поиск по имени плана, например «Этаж 2»",
            empty_text=u"Нет планов, которые ещё не размещены на листах."
        )
        find("ListHost").Content = self.list.element

        self.current_label = defaults.get(u"current")

        self._fill_samples(defaults.get(u"sample_id"))
        self._fill_sections()

        margins = defaults.get(u"margins") or (20.0, 5.0, 5.0, 60.0)
        find("TxtLeft").Text = self._fmt(margins[0])
        find("TxtRight").Text = self._fmt(margins[1])
        find("TxtTop").Text = self._fmt(margins[2])
        find("TxtBottom").Text = self._fmt(margins[3])
        find("ChkPortrait").IsChecked = bool(defaults.get(u"portrait", True))
        find("TxtSuffix").Text = unicode(defaults.get(u"suffix") or u"")

        self._wire()

        # Начальный выбор — после подписок (UI.md, п. 18)
        self._apply_sample()
        self._reset_start()

        if defaults.get(u"preselect"):
            self.list.set_checked(defaults[u"preselect"])

        pp_wpf.set_owner(self.window)
        pp_wpf.fit_to_screen(self.window)
        self._refresh()

    # ---------- построение -------------------------------------------------

    @staticmethod
    def _fmt(value):
        try:
            value = float(value)
        except Exception:
            return u""

        if abs(value - round(value)) < 1e-6:
            return unicode(int(round(value)))

        return unicode(value)

    def _fill_samples(self, sample_id):
        combo = self.window.FindName("CmbSample")
        selected = 0

        for index, row in enumerate(self.sheets):
            label = u"{0} — {1}".format(row[u"number"], row[u"name"]).strip(u" —")

            if row[u"section"]:
                label = u"{0}   ·   {1}".format(label, row[u"section"])

            combo.Items.Add(label)

            if sample_id is not None and row[u"sheet"].Id == sample_id:
                selected = index

        if self.sheets:
            combo.SelectedIndex = selected

    def _fill_sections(self):
        panel = self.window.FindName("PanelSections")
        chip_style = style(u"RadioButton.Chip")

        sections = []
        for row in self.sheets:
            if row[u"section"] and row[u"section"] not in sections:
                sections.append(row[u"section"])

        if not sections:
            self.window.FindName("TxtSectionHint").Text = (
                u"В проекте пока нет листов с заполненным разделом — "
                u"впишите раздел руками.")

        for section in sections:
            chip = RadioButton()
            chip.Content = section
            chip.GroupName = u"Sections"
            chip.MinWidth = 0
            chip.Margin = Thickness(0, 0, 6, 6)

            if chip_style is not None:
                chip.Style = chip_style

            chip.Checked += self._chip_handler(section)
            panel.Children.Add(chip)
            self.chips.append((section, chip))

    def _chip_handler(self, section):
        def handler(sender, args):
            if self._syncing:
                return
            self.window.FindName("TxtSection").Text = section
        return handler

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        @guard
        def on_sample(sender, args):
            self._apply_sample()
            self._refresh()

        @guard
        def on_section(sender, args):
            self._sync_chips()
            self._refresh()

        @guard
        def on_start(sender, args):
            if find("TxtStart").IsKeyboardFocused:
                self._start_auto = False
            self._refresh()

        @guard
        def on_suffix(sender, args):
            if self._start_auto:
                self._reset_start()
            self._refresh()

        @guard
        def on_any(sender, args):
            self._refresh()

        @guard
        def on_run(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("CmbSample").SelectionChanged += on_sample
        find("TxtSection").TextChanged += on_section
        find("TxtStart").TextChanged += on_start
        find("TxtSuffix").TextChanged += on_suffix

        for name in ("TxtLeft", "TxtRight", "TxtTop", "TxtBottom"):
            find(name).TextChanged += on_any

        find("ChkPortrait").Checked += on_any
        find("ChkPortrait").Unchecked += on_any
        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    # ---------- состояние --------------------------------------------------

    def _sample_row(self):
        index = self.window.FindName("CmbSample").SelectedIndex

        if 0 <= index < len(self.sheets):
            return self.sheets[index]

        return None

    def _apply_sample(self):
        u"""Раздел новых листов по умолчанию — раздел образца."""
        row = self._sample_row()

        if row is not None and row[u"section"]:
            self.window.FindName("TxtSection").Text = row[u"section"]

    def _sync_chips(self):
        text = unicode(self.window.FindName("TxtSection").Text or u"").strip()
        self._syncing = True
        try:
            for section, chip in self.chips:
                chip.IsChecked = (section == text)
        finally:
            self._syncing = False

    def _reset_start(self):
        suffix = unicode(self.window.FindName("TxtSuffix").Text or u"").strip()
        self.window.FindName("TxtStart").Text = unicode(
            suggest_start(self.taken, suffix))
        self._start_auto = True

    def _on_current(self, sender, args):
        if self.current_label:
            names = set(name for name, _view in self.list.get_checked())
            names.add(self.current_label)
            self.list.set_checked(names)
            self._refresh()
        else:
            self._fail(u"Активный вид — не план или уже лежит на листе.")

    def _read_margins(self):
        find = self.window.FindName
        values = []

        for name in ("TxtLeft", "TxtRight", "TxtTop", "TxtBottom"):
            text = unicode(find(name).Text or u"").strip().replace(u",", u".")
            value = float(text)
            if value < 0:
                raise ValueError(name)
            values.append(value)

        return tuple(values)

    def _collect(self):
        u"""Собрать настройки из окна. Возврат: (словарь, ошибка)."""
        find = self.window.FindName

        checked = self.list.get_checked()
        if not checked:
            return None, u"Отметьте хотя бы один план."

        sample = self._sample_row()
        if sample is None:
            return None, u"В проекте нет ни одного листа для образца."

        section = unicode(find("TxtSection").Text or u"").strip()

        match = _NUMBER.match(unicode(find("TxtStart").Text or u""))
        if not match:
            return None, u"Начальный номер — целое число."

        try:
            margins = self._read_margins()
        except Exception:
            return None, u"Поля рамки — неотрицательные числа в мм."

        suffix = unicode(find("TxtSuffix").Text or u"").strip()

        return {
            u"views": [view for _name, view in checked],
            u"labels": [name for name, _view in checked],
            u"sample": sample[u"sheet"],
            u"sample_label": u"{0} — {1}".format(
                sample[u"number"], sample[u"name"]).strip(u" —"),
            u"section": section,
            u"start": int(match.group(1)),
            u"suffix": suffix,
            u"margins": margins,
            u"portrait": bool(find("ChkPortrait").IsChecked)
        }, None

    def _refresh(self):
        find = self.window.FindName
        options, error = self._collect()

        status = find("TxtStatus")
        result = find("TxtResult")
        detail = find("TxtResultDetail")

        if error:
            find("BtnRun").IsEnabled = False
            status.Text = error
            status.Foreground = brush(u"Muted")
            result.Text = error
            result.Foreground = brush(u"Muted")
            detail.Text = u""
            return

        count = len(options[u"views"])
        numbers = allocate_numbers(
            options[u"start"], options[u"suffix"], self.taken, count)

        find("BtnRun").IsEnabled = True
        status.Text = u"Отмечено {0} {1}.".format(
            count, plural(count, (u"план", u"плана", u"планов")))
        status.Foreground = brush(u"Muted")

        result.Text = u"{0} {1}: {2}".format(
            count,
            plural(count, (u"лист", u"листа", u"листов")),
            self._range_text(numbers))
        result.Foreground = brush(u"Ink")

        first_name = u""
        try:
            first_name = self.name_for(options[u"views"][0]) or u""
        except Exception:
            first_name = u""

        lines = []
        if options[u"section"]:
            lines.append(u"Раздел: {0}.".format(options[u"section"]))
        else:
            lines.append(u"Раздел не заполняется.")
        lines.append(u"Рамка как на листе «{0}».".format(
            options[u"sample_label"]))
        if first_name:
            lines.append(u"Первый лист: «{0}».".format(first_name))

        detail.Text = u" ".join(lines)

    @staticmethod
    def _range_text(numbers):
        if not numbers:
            return u""
        if len(numbers) == 1:
            return numbers[0]
        if len(numbers) <= 4:
            return u", ".join(numbers)
        return u"{0} … {1}".format(numbers[0], numbers[-1])

    def _accept(self):
        options, error = self._collect()

        if error:
            self._fail(error)
            return

        self.result = options
        self.window.DialogResult = True

    def _fail(self, message):
        status = self.window.FindName("TxtStatus")
        status.Text = message
        status.Foreground = brush(u"Hot")

    def show(self):
        self.window.ShowDialog()
        return self.result


def ask(folder, plans, sheets, taken_numbers, name_for, defaults):
    u"""Показать окно. Возврат: словарь настроек или None."""
    return PlaceWindow(
        folder, plans, sheets, taken_numbers, name_for, defaults).show()

# -*- coding: utf-8 -*-
u"""Окно кнопки «Разместить на листы».

Два режима одним окном — чипсы сверху:

* «Планы» — каждый отмеченный план на свой лист;
* «Виды» — все отмеченные 3D-виды, разрезы, фасады на один лист рядами.

Правая колонка общая: образец, раздел, нумерация, поля рамки. Режиму «Виды»
дополнительно нужны начало имени листа и зазор между видами.

Окно ничего не знает про Revit: списки видов и листов ему готовит
`script.py`, обратно уходит словарь настроек запуска (см. `ask`).
"""

import os
import re

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF
import pp_check_list

from System.Windows import Application, Thickness, Visibility
from System.Windows.Controls import RadioButton


XAML_FILE = u"ui.xaml"

MODE_PLANS = u"plans"
MODE_VIEWS = u"views"

NO_PREFIX = u"(без начала — только системы)"

ORIENT_LANDSCAPE = u"landscape"
ORIENT_PORTRAIT = u"portrait"
ORIENT_ANY = u"any"

# Ориентация -> (имя чипса, пояснение под чипсами)
ORIENT_CHIPS = (
    (ORIENT_LANDSCAPE, "ChipLandscape",
     u"Только альбомные листы: галочка «Книжная ориентация» в рамке не ставится."),
    (ORIENT_PORTRAIT, "ChipPortrait",
     u"Только книжные листы: в рамке ставится «Книжная ориентация»."),
    (ORIENT_ANY, "ChipAnyOrientation",
     u"Для каждого формата сначала альбомная, потом книжная — берётся меньший подходящий лист."),
)

# Заголовки видов: группа чипсов -> [(значение, имя чипса)]. Значения совпадают
# с TITLE_* в script.py.
TITLE_CHIPS = {
    u"text": ((u"systems", "ChipTitleSystems"), (u"keep", "ChipTitleKeep")),
    u"valign": ((u"above", "ChipTitleAbove"), (u"below", "ChipTitleBelow")),
    u"halign": ((u"left", "ChipTitleLeft"), (u"center", "ChipTitleCenter"),
                (u"right", "ChipTitleRight")),
    u"line": ((u"text", "ChipLineText"), (u"view", "ChipLineView"),
              (u"keep", "ChipLineKeep")),
}

TITLE_FALLBACK = {u"text": u"systems", u"valign": u"above",
                  u"halign": u"center", u"line": u"text"}

TITLE_WORDS = {
    u"above": u"над видом", u"below": u"под видом",
    u"left": u"слева", u"center": u"по центру", u"right": u"справа",
}

SAMPLE_TYPE = u"Как на листе-образце"

_NUMBER = re.compile(u"^\\s*(\\d+)\\s*$")

MODE_TEXT = {
    MODE_PLANS: {
        u"subtitle": u"Каждый отмеченный план получает свой лист: рамка "
                     u"копируется с листа-образца, формат подбирается под план, "
                     u"план ставится по центру.",
        u"label": u"КАКИЕ ПЛАНЫ РАЗМЕСТИТЬ",
        u"search": u"Поиск по имени плана, например «Этаж 2»",
        u"empty": u"Нет планов, которые ещё не размещены на листах.",
        u"forms": (u"план", u"плана", u"планов"),
    },
    MODE_VIEWS: {
        u"subtitle": u"Отмеченные виды ложатся рядами на один лист в порядке "
                     u"списка: сначала приток, потом вытяжка. Не поместились "
                     u"даже на самый большой формат — остаток уйдёт на "
                     u"следующий лист.",
        u"label": u"КАКИЕ ВИДЫ ПОЛОЖИТЬ НА ЛИСТ",
        u"search": u"Поиск по имени вида, например «П1» или «Разрез»",
        u"empty": u"Нет 3D-видов, разрезов и фасадов, которые ещё не "
                  u"размещены на листах.",
        u"forms": (u"вид", u"вида", u"видов"),
    },
}


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

    def __init__(self, folder, lists, sheets, taken_numbers, name_for,
                 prefixes, defaults):
        u"""
        lists         — {режим: [(подпись, {view, template, warn}), ...]}
        sheets        — [{sheet, number, name, section}, ...] (pp_sheet_picker)
        taken_numbers — множество занятых номеров листов
        name_for      — fn(режим, [виды], начало имени) -> имя листа
        prefixes      — начала имени листа из настроек
        defaults      — {mode, sample_id, suffix, margins, orientation, gap,
                         prefix, current: {режим: подпись}}
        """
        self.window = pp_wpf.load_window_file(os.path.join(folder, XAML_FILE))
        self.items = dict(lists)
        self.sheets = list(sheets)
        self.taken = set(taken_numbers or [])
        self.name_for = name_for
        self.title_preview = defaults.get(u"title_preview") or (lambda view: u"")
        self.prefixes = list(prefixes or [])
        self.current = dict(defaults.get(u"current") or {})
        self.result = None
        self.chips = []
        self.mode = None
        self._start_auto = True
        self._syncing = False

        # Для каждого режима — свой список со своими отметками и фильтром
        self.lists = {}
        self.template_keys = {}
        self.template_index = {}

        for mode in (MODE_PLANS, MODE_VIEWS):
            text = MODE_TEXT[mode]
            self.lists[mode] = pp_check_list.CheckList(
                self.items.get(mode) or [],
                extra_button=(u"Текущий вид", self._on_current),
                on_change=self._refresh,
                search_hint=text[u"search"],
                empty_text=text[u"empty"]
            )
            self.template_keys[mode] = [None]
            self.template_index[mode] = 0

            if self.current.get(mode):
                self.lists[mode].set_checked([self.current[mode]])

        find = self.window.FindName

        self._fill_samples(defaults.get(u"sample_id"))
        self._fill_sections()
        self._fill_prefixes(defaults.get(u"prefix"))
        self._fill_title_types(defaults.get(u"viewport_types") or [],
                               (defaults.get(u"title") or {}).get(u"type_id"))

        margins = defaults.get(u"margins") or (20.0, 5.0, 5.0, 60.0)
        find("TxtLeft").Text = self._fmt(margins[0])
        find("TxtRight").Text = self._fmt(margins[1])
        find("TxtTop").Text = self._fmt(margins[2])
        find("TxtBottom").Text = self._fmt(margins[3])
        find("TxtGap").Text = self._fmt(defaults.get(u"gap", 10.0))
        find("TxtSuffix").Text = unicode(defaults.get(u"suffix") or u"")

        self._wire()

        # Начальное состояние — после подписок (UI.md, п. 18)
        self._apply_sample()
        self._reset_start()
        self._set_orientation(defaults.get(u"orientation") or ORIENT_LANDSCAPE)
        self._set_title(defaults.get(u"title") or {})

        mode = defaults.get(u"mode") or MODE_PLANS
        if not self.items.get(mode):
            other = MODE_VIEWS if mode == MODE_PLANS else MODE_PLANS
            if self.items.get(other):
                mode = other

        chip = find("ChipViews") if mode == MODE_VIEWS else find("ChipPlans")
        chip.IsChecked = True
        self._set_mode(mode)

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

    def _fill_prefixes(self, stored):
        combo = self.window.FindName("CmbPrefix")
        combo.Items.Add(NO_PREFIX)

        for prefix in self.prefixes:
            combo.Items.Add(prefix)

        # stored: None — ещё не запускали (берём первое начало из настроек),
        # u"" — в прошлый раз сознательно выбрали «без начала».
        if stored in self.prefixes:
            combo.SelectedIndex = self.prefixes.index(stored) + 1
        elif stored == u"":
            combo.SelectedIndex = 0
        else:
            combo.SelectedIndex = 1 if self.prefixes else 0

    def _fill_title_types(self, types, selected_id):
        u"""Типы видового экрана: (id, имя). Первая строка — как у образца."""
        combo = self.window.FindName("CmbTitleType")
        self.title_types = list(types)

        combo.Items.Add(SAMPLE_TYPE)
        index = 0

        for position, (type_id, name) in enumerate(self.title_types):
            combo.Items.Add(name)

            if selected_id and type_id == selected_id:
                index = position + 1

        combo.SelectedIndex = index

        if not self.title_types:
            self.window.FindName("TxtTitleTypeHint").Text = (
                u"В проекте ещё нет видовых экранов — тип возьмётся с листа-образца.")

    def _set_title(self, title):
        find = self.window.FindName

        for group, chips in TITLE_CHIPS.items():
            value = title.get(group) or TITLE_FALLBACK[group]
            names = dict(chips)
            find(names.get(value) or names[TITLE_FALLBACK[group]]).IsChecked = True

        find("TxtTitleGap").Text = self._fmt(title.get(u"gap", 3.0))
        find("ChkTitleRows").IsChecked = bool(title.get(u"rows", True))

    def _chip_value(self, group):
        find = self.window.FindName

        for value, name in TITLE_CHIPS[group]:
            if find(name).IsChecked:
                return value

        return TITLE_FALLBACK[group]

    def _fill_templates(self):
        u"""Фильтр по шаблону вида для текущего режима. Нет шаблонов — прячем."""
        find = self.window.FindName
        combo = find("CmbTemplate")
        items = self.items.get(self.mode) or []

        counts = {}
        for _label, item in items:
            template = item.get(u"template") or u""
            counts[template] = counts.get(template, 0) + 1

        keys = [None]

        self._syncing = True
        try:
            combo.Items.Clear()

            if not any(counts.keys()):
                find("PanelTemplate").Visibility = Visibility.Collapsed
                self.template_keys[self.mode] = keys
                return

            find("PanelTemplate").Visibility = Visibility.Visible
            combo.Items.Add(u"Все шаблоны ({0})".format(len(items)))

            names = sorted((name for name in counts if name),
                           key=lambda n: n.lower())
            if u"" in counts:
                names.append(u"")

            for name in names:
                combo.Items.Add(u"{0} ({1})".format(
                    name or u"(без шаблона)", counts[name]))
                keys.append(name)

            self.template_keys[self.mode] = keys

            index = self.template_index.get(self.mode, 0)
            combo.SelectedIndex = index if 0 <= index < len(keys) else 0
        finally:
            self._syncing = False

    def _chip_handler(self, section):
        def handler(sender, args):
            if self._syncing:
                return
            self.window.FindName("TxtSection").Text = section
        return handler

    def _mode_handler(self, mode):
        guard = pp_wpf.guard(self._fail)

        @guard
        def handler(sender, args):
            self._set_mode(mode)
            self._refresh()

        return handler

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        @guard
        def on_sample(sender, args):
            self._apply_sample()
            self._refresh()

        @guard
        def on_template(sender, args):
            if self._syncing:
                return
            self.template_index[self.mode] = find("CmbTemplate").SelectedIndex
            self._apply_template_filter()
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
        def on_orientation(sender, args):
            self._update_orientation_hint()
            self._refresh()

        @guard
        def on_run(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("ChipPlans").Checked += self._mode_handler(MODE_PLANS)
        find("ChipViews").Checked += self._mode_handler(MODE_VIEWS)
        find("CmbSample").SelectionChanged += on_sample
        find("CmbTemplate").SelectionChanged += on_template
        find("CmbPrefix").SelectionChanged += on_any
        find("TxtSection").TextChanged += on_section
        find("TxtStart").TextChanged += on_start
        find("TxtSuffix").TextChanged += on_suffix

        for name in ("TxtLeft", "TxtRight", "TxtTop", "TxtBottom", "TxtGap"):
            find(name).TextChanged += on_any

        for _value, chip_name, _hint in ORIENT_CHIPS:
            find(chip_name).Checked += on_orientation

        for chips in TITLE_CHIPS.values():
            for _value, chip_name in chips:
                find(chip_name).Checked += on_any

        find("CmbTitleType").SelectionChanged += on_any
        find("TxtTitleGap").TextChanged += on_any
        find("ChkTitleRows").Checked += on_any
        find("ChkTitleRows").Unchecked += on_any
        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    # ---------- режим ------------------------------------------------------

    def _set_mode(self, mode):
        if mode == self.mode:
            return

        find = self.window.FindName
        self.mode = mode
        text = MODE_TEXT[mode]

        find("TxtSubtitle").Text = text[u"subtitle"]
        find("TxtListLabel").Text = text[u"label"]
        find("ListHost").Content = self.lists[mode].element

        views_only = Visibility.Visible if mode == MODE_VIEWS \
            else Visibility.Collapsed
        find("PanelPrefix").Visibility = views_only
        find("PanelGap").Visibility = views_only
        find("PanelTitleText").Visibility = views_only
        find("ChkTitleRows").Visibility = views_only

        self._fill_templates()
        self._apply_template_filter()

    def _apply_template_filter(self):
        keys = self.template_keys.get(self.mode) or [None]
        index = self.template_index.get(self.mode, 0)
        lst = self.lists[self.mode]

        if index <= 0 or index >= len(keys):
            lst.set_filter(None)
        else:
            key = keys[index]
            lst.set_filter(
                lambda _name, item: (item.get(u"template") or u"") == key)

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
        label = self.current.get(self.mode)
        lst = self.lists[self.mode]

        if label:
            names = set(name for name, _item in lst.get_checked())
            names.add(label)
            lst.set_checked(names)
            self._refresh()
        elif self.mode == MODE_PLANS:
            self._fail(u"Активный вид — не план или уже лежит на листе.")
        else:
            self._fail(u"Активный вид — не 3D-вид, разрез или фасад, "
                       u"или уже лежит на листе.")

    def _set_orientation(self, value):
        find = self.window.FindName

        for chip_value, chip_name, _hint in ORIENT_CHIPS:
            if chip_value == value:
                find(chip_name).IsChecked = True
                break
        else:
            find("ChipLandscape").IsChecked = True

        self._update_orientation_hint()

    def _orientation(self):
        find = self.window.FindName

        for value, chip_name, _hint in ORIENT_CHIPS:
            if find(chip_name).IsChecked:
                return value

        return ORIENT_LANDSCAPE

    def _update_orientation_hint(self):
        current = self._orientation()

        for value, _chip_name, hint in ORIENT_CHIPS:
            if value == current:
                self.window.FindName("TxtOrientationHint").Text = hint

    def _prefix(self):
        index = self.window.FindName("CmbPrefix").SelectedIndex

        if 1 <= index <= len(self.prefixes):
            return self.prefixes[index - 1]

        return u""

    def _read_number(self, name, allow_zero=True):
        text = unicode(self.window.FindName(name).Text or u"")
        value = float(text.strip().replace(u",", u"."))

        if value < 0 or (not allow_zero and value == 0):
            raise ValueError(name)

        return value

    def _collect(self):
        u"""Собрать настройки из окна. Возврат: (словарь, ошибка)."""
        find = self.window.FindName
        text = MODE_TEXT[self.mode]

        checked = self.lists[self.mode].get_checked()
        if not checked:
            return None, u"Отметьте хотя бы один {0}.".format(text[u"forms"][0])

        sample = self._sample_row()
        if sample is None:
            return None, u"В проекте нет ни одного листа для образца."

        section = unicode(find("TxtSection").Text or u"").strip()

        match = _NUMBER.match(unicode(find("TxtStart").Text or u""))
        if not match:
            return None, u"Начальный номер — целое число."

        try:
            margins = tuple(self._read_number(name) for name in
                            ("TxtLeft", "TxtRight", "TxtTop", "TxtBottom"))
        except Exception:
            return None, u"Поля рамки — неотрицательные числа в мм."

        try:
            gap = self._read_number("TxtGap")
        except Exception:
            return None, u"Зазор между видами — неотрицательное число в мм."

        try:
            title_gap = self._read_number("TxtTitleGap")
        except Exception:
            return None, u"Отступ заголовка от вида — неотрицательное число в мм."

        type_index = find("CmbTitleType").SelectedIndex
        type_id = None
        if 1 <= type_index <= len(self.title_types):
            type_id = self.title_types[type_index - 1][0]

        title = {
            u"type_id": type_id,
            u"text": self._chip_value(u"text"),
            u"halign": self._chip_value(u"halign"),
            u"valign": self._chip_value(u"valign"),
            u"line": self._chip_value(u"line"),
            u"gap": title_gap,
            u"rows": bool(find("ChkTitleRows").IsChecked),
        }

        suffix = unicode(find("TxtSuffix").Text or u"").strip()

        return {
            u"title": title,
            u"mode": self.mode,
            u"views": [item[u"view"] for _name, item in checked],
            u"labels": [name for name, _item in checked],
            u"warned": len([1 for _name, item in checked if item.get(u"warn")]),
            u"sample": sample[u"sheet"],
            u"sample_label": u"{0} — {1}".format(
                sample[u"number"], sample[u"name"]).strip(u" —"),
            u"section": section,
            u"start": int(match.group(1)),
            u"suffix": suffix,
            u"margins": margins,
            u"gap": gap,
            u"prefix": self._prefix(),
            u"orientation": self._orientation()
        }, None

    def _refresh(self):
        if self.mode is None:
            return

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
        forms = MODE_TEXT[self.mode][u"forms"]

        find("BtnRun").IsEnabled = True
        status.Text = u"Отмечено {0} {1}.".format(count, plural(count, forms))
        status.Foreground = brush(u"Muted")

        if options[u"warned"]:
            status.Text = (
                u"{0} Без подрезки: {1} — на листе будут размером со всю "
                u"модель.".format(status.Text, options[u"warned"]))
            status.Foreground = brush(u"Hot")

        lines = []
        if options[u"section"]:
            lines.append(u"Раздел: {0}.".format(options[u"section"]))
        else:
            lines.append(u"Раздел не заполняется.")
        lines.append(u"Рамка как на листе «{0}».".format(
            options[u"sample_label"]))

        try:
            name = self.name_for(self.mode, options[u"views"],
                                 options[u"prefix"]) or u""
        except Exception:
            name = u""

        if self.mode == MODE_PLANS:
            numbers = allocate_numbers(
                options[u"start"], options[u"suffix"], self.taken, count)

            result.Text = u"{0} {1}: {2}".format(
                count, plural(count, (u"лист", u"листа", u"листов")),
                self._range_text(numbers))

            if name:
                lines.append(u"Первый лист: «{0}».".format(name))

            title = options[u"title"]
            lines.append(u"Заголовок {0}, {1}.".format(
                TITLE_WORDS[title[u"valign"]], TITLE_WORDS[title[u"halign"]]))
        else:
            number = allocate_numbers(
                options[u"start"], options[u"suffix"], self.taken, 1)[0]

            result.Text = u"Лист {0}: {1} {2}".format(
                number, count, plural(count, forms))

            if name:
                lines.append(u"Имя: «{0}».".format(name))

            title = options[u"title"]
            title_text = u""

            if title[u"text"] == u"systems":
                try:
                    title_text = self.title_preview(options[u"views"][0]) or u""
                except Exception:
                    title_text = u""

            where = u"{0}, {1}".format(TITLE_WORDS[title[u"valign"]],
                                        TITLE_WORDS[title[u"halign"]])

            if title_text:
                lines.append(u"Заголовок первого вида: «{0}», {1}.".format(
                    title_text, where))
            else:
                lines.append(u"Заголовки {0}.".format(where))

            lines.append(u"Если все виды не поместятся даже на самый большой "
                         u"формат, остаток уйдёт на следующие номера — "
                         u"отчёт об этом предупредит.")

        result.Foreground = brush(u"Ink")
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


def ask(folder, lists, sheets, taken_numbers, name_for, prefixes, defaults):
    u"""Показать окно. Возврат: словарь настроек или None."""
    return PlaceWindow(folder, lists, sheets, taken_numbers, name_for,
                       prefixes, defaults).show()

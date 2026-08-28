# -*- coding: utf-8 -*-
u"""Общее окно выбора листов с фильтром по разделу проекта.

Родилось в кнопке «Заполнить имя листа по планам»: стандартное окно pyRevit
`forms.select_sheets` умеет отбирать листы только по наборам (Sheet Sets), а
в работе листы делятся по штампу — ОВ, ОВ.С, ТМ, ЭЛ, ИЗМ. Здесь тот же список
с галочками и поиском, но сверху стоит выбор раздела: снял лишние разделы —
и в списке остались только свои листы.

    import pp_sheet_picker

    sheets = pp_sheet_picker.ask(
        doc,
        title=u"Заполнить имя листа по планам",
        subtitle=u"Отметьте листы, для которых нужно собрать имя.",
        button=u"Выбрать листы")

    if not sheets:
        return

Возврат: список ViewSheet в порядке списка или None, если пользователь закрыл
окно. Отметки живут отдельно от фильтра: переключение раздела и поиск их не
сбрасывают, поэтому можно набрать листы из нескольких разделов подряд.

Имя параметра раздела берётся из настроек (ключ `sheet_section_param`,
по умолчанию «ADSK_Штамп Раздел проекта»). Если в проекте такого параметра
нет — строка выбора раздела просто прячется, окно продолжает работать как
обычный список с поиском.
"""

import os
import re

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF
import pp_check_list

from pp_settings import load_settings, DEFAULT_SETTINGS

from System.Windows import Application, Visibility


XAML_FILE = u"pp_sheet_picker.xaml"

DEFAULT_TITLE = u"Выбор листов"
DEFAULT_SUBTITLE = u"Отберите листы по разделу проекта и отметьте нужные."
DEFAULT_BUTTON = u"Выбрать листы"

SECTION_PARAM_FALLBACK = u"ADSK_Штамп Раздел проекта"

ALL_SECTIONS = u"Все разделы"
NO_SECTION = u"(без раздела)"

_DIGITS = re.compile(u"(\\d+)")


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


def natural_key(text):
    u"""Ключ сортировки, где 2 идёт перед 10, а не после."""
    key = []

    for index, part in enumerate(_DIGITS.split(unicode(text or u""))):
        if index % 2:
            key.append((1, int(part), u""))
        else:
            key.append((0, 0, part.lower()))

    return key


# ======================================================================
#  Чтение листов
# ======================================================================

def get_section_param_name():
    u"""Имя параметра раздела: из настроек, иначе штамповое ADSK."""
    try:
        settings = load_settings()
    except Exception:
        settings = {}

    name = settings.get(
        "sheet_section_param",
        DEFAULT_SETTINGS.get("sheet_section_param", SECTION_PARAM_FALLBACK)
    )

    return unicode(name or u"").strip() or SECTION_PARAM_FALLBACK


def read_section(sheet, param_name):
    u"""Значение параметра раздела у листа. Пусто — если параметра нет."""
    if not param_name:
        return u""

    try:
        parameter = sheet.LookupParameter(param_name)
    except Exception:
        parameter = None

    if parameter is None:
        return u""

    value = None

    try:
        value = parameter.AsString()
    except Exception:
        value = None

    if not value:
        try:
            value = parameter.AsValueString()
        except Exception:
            value = None

    return unicode(value or u"").strip()


def collect_rows(doc, param_name=None):
    u"""Возврат: [{sheet, number, name, section}, ...] в порядке раздел + номер."""
    from Autodesk.Revit.DB import FilteredElementCollector, ViewSheet

    if param_name is None:
        param_name = get_section_param_name()

    rows = []

    for sheet in FilteredElementCollector(doc).OfClass(ViewSheet):
        if not isinstance(sheet, ViewSheet):
            continue

        try:
            number = unicode(sheet.SheetNumber or u"")
        except Exception:
            number = u""

        try:
            name = unicode(sheet.Name or u"")
        except Exception:
            name = u""

        rows.append({
            u"sheet": sheet,
            u"number": number,
            u"name": name,
            u"section": read_section(sheet, param_name)
        })

    rows.sort(key=lambda row: (
        row[u"section"] == u"",                 # безразделные — в конец
        natural_key(row[u"section"]),
        natural_key(row[u"number"]),
        natural_key(row[u"name"])
    ))

    return rows


def build_items(rows):
    u"""Строки списка: (подпись, строка данных).

    Подпись обязана быть уникальной — по ней pp_check_list хранит отметки.
    Номера листов в проекте повторяются (в каждом разделе своя нумерация),
    поэтому к дублям добавляем раздел, а если и этого мало — порядковый номер.
    """
    labels = {}

    for row in rows:
        base = u"{0} — {1}".format(row[u"number"], row[u"name"]).strip(u" —")
        labels.setdefault(base, []).append(row)

    items = []
    used = set()

    for row in rows:
        base = u"{0} — {1}".format(row[u"number"], row[u"name"]).strip(u" —")
        label = base

        if len(labels.get(base, [])) > 1:
            section = row[u"section"] or NO_SECTION
            label = u"{0}   ·   {1}".format(base, section)

        candidate = label
        index = 2

        while candidate in used:
            candidate = u"{0} ({1})".format(label, index)
            index += 1

        used.add(candidate)
        items.append((candidate, row))

    return items


def collect_sections(rows):
    u"""Разделы в порядке показа: заполненные по алфавиту, пустой — в конец."""
    values = []

    for row in rows:
        section = row[u"section"]

        if section and section not in values:
            values.append(section)

    values.sort(key=natural_key)

    if any(not row[u"section"] for row in rows):
        values.append(u"")

    return values


# ======================================================================
#  Окно
# ======================================================================

class SheetPicker(object):

    def __init__(self, doc, title=None, subtitle=None, button=None,
                 owner=None, preselect_section=None):
        xaml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), XAML_FILE)

        self.window = pp_wpf.load_window_file(xaml_path)

        self.param_name = get_section_param_name()
        self.rows = collect_rows(doc, self.param_name)
        self.sections = collect_sections(self.rows)
        self.result = None

        # Ключи фильтра по позициям ComboBox: None — все разделы.
        self.filter_keys = [None]

        find = self.window.FindName
        find("TxtPickerTitle").Text = title or DEFAULT_TITLE
        find("TxtPickerSubtitle").Text = subtitle or DEFAULT_SUBTITLE
        find("TxtSectionLabel").Text = self.param_name.upper()
        find("BtnPickerSelect").Content = button or DEFAULT_BUTTON

        self.window.Title = title or DEFAULT_TITLE

        self.list = pp_check_list.CheckList(
            build_items(self.rows),
            extra_button=(u"Сбросить отметки", self._on_reset),
            on_change=self._refresh_state,
            search_hint=u"Поиск по номеру и имени листа, например «План 2 этажа»",
            empty_text=u"В этом разделе нет листов под текущий поиск."
        )
        find("ListHost").Content = self.list.element

        self._fill_sections()
        self._wire()
        self._set_owner(owner)
        self._select_section(preselect_section)
        self._refresh_state()

        pp_wpf.fit_to_screen(self.window)

    # ---------- построение -------------------------------------------------

    def _fill_sections(self):
        u"""Заполняем ComboBox. Нет параметра в проекте — прячем строку выбора."""
        find = self.window.FindName
        combo = find("CmbSection")

        has_sections = any(row[u"section"] for row in self.rows)

        if not has_sections:
            find("PanelSection").Visibility = Visibility.Collapsed
            return

        combo.Items.Add(u"{0} ({1})".format(ALL_SECTIONS, len(self.rows)))

        for section in self.sections:
            count = len([row for row in self.rows
                         if row[u"section"] == section])

            combo.Items.Add(u"{0} ({1})".format(section or NO_SECTION, count))
            self.filter_keys.append(section)

        combo.SelectedIndex = 0

    def _set_owner(self, owner):
        if owner is not None:
            try:
                self.window.Owner = owner
                return
            except Exception:
                pass

        pp_wpf.set_owner(self.window)

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        @guard
        def on_section(sender, args):
            self._apply_filter()

        @guard
        def on_select(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("CmbSection").SelectionChanged += on_section
        find("BtnPickerSelect").Click += on_select
        find("BtnPickerCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    # ---------- фильтр -----------------------------------------------------

    def _select_section(self, section):
        u"""Ставим раздел активного листа, если он есть в списке."""
        if not section:
            return

        combo = self.window.FindName("CmbSection")

        for index, key in enumerate(self.filter_keys):
            if key is not None and key == section:
                combo.SelectedIndex = index
                return

    def _apply_filter(self):
        combo = self.window.FindName("CmbSection")
        index = combo.SelectedIndex

        if index <= 0 or index >= len(self.filter_keys):
            self.list.set_filter(None)
        else:
            key = self.filter_keys[index]
            self.list.set_filter(
                lambda _name, row: row[u"section"] == key)

        self._refresh_state()

    def _on_reset(self, sender, args):
        self.list.set_checked([])
        self._refresh_state()

    # ---------- состояние --------------------------------------------------

    def _refresh_state(self):
        count = self.list.count()

        find = self.window.FindName
        find("BtnPickerSelect").IsEnabled = count > 0

        status = find("TxtPickerStatus")

        if not self.rows:
            status.Text = u"В проекте нет листов."
            status.Foreground = brush(u"Hot")
            return

        if count:
            status.Text = u"Выбрано {0} {1}.".format(
                count, plural(count, (u"лист", u"листа", u"листов")))
            status.Foreground = brush(u"Muted")
        else:
            status.Text = u"Отметьте хотя бы один лист."
            status.Foreground = brush(u"Muted")

    def _accept(self):
        checked = self.list.get_checked()

        if not checked:
            self._fail(u"Не отмечено ни одного листа.")
            return

        self.result = [row[u"sheet"] for _name, row in checked]
        self.window.DialogResult = True

    def _fail(self, message):
        status = self.window.FindName("TxtPickerStatus")
        status.Text = message
        status.Foreground = brush(u"Hot")

    def show(self):
        self.window.ShowDialog()
        return self.result


def active_sheet_section(doc):
    u"""Раздел активного листа — чтобы окно открывалось сразу на нём."""
    from Autodesk.Revit.DB import ViewSheet

    try:
        view = doc.ActiveView
    except Exception:
        return None

    if not isinstance(view, ViewSheet):
        return None

    return read_section(view, get_section_param_name()) or None


def ask(doc, title=None, subtitle=None, button=None, owner=None,
        preselect_section=None):
    u"""Показать окно выбора листов. Возврат: [ViewSheet, ...] или None."""
    if preselect_section is None:
        try:
            preselect_section = active_sheet_section(doc)
        except Exception:
            preselect_section = None

    return SheetPicker(
        doc, title, subtitle, button, owner, preselect_section).show()

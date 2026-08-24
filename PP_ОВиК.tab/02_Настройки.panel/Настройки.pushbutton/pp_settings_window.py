# -*- coding: utf-8 -*-
u"""Окно «Настройки» PP Tools.

Разделы переключаются списком слева, значения правятся на месте, на диск
всё уходит одной кнопкой «Сохранить». Модель Revit не затрагивается.
"""

import os
from datetime import datetime

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

from System.Collections.ObjectModel import ObservableCollection
from System.Windows import Application

from Microsoft.Win32 import SaveFileDialog, OpenFileDialog

from pp_settings import (
    load_settings, save_settings, DEFAULT_SETTINGS,
    export_settings, import_settings, get_settings_path
)


# Раздел -> индекс в боковом меню
SECTIONS = [u"Общие", u"Прокси-контроллер", u"Оформление",
            u"Теплопотери", u"Проверка", u"Экспорт и импорт"]

# Чекбокс -> ключ настроек
FLAGS = [
    ("ChkSuccess", "show_success_report"),
    ("ChkWarning", "show_warning_report"),
    ("ChkOrientation", "detect_orientation"),
    ("ChkHeatReport", "heatloss_show_report"),
    ("ChkMepReport", "mep_transfer_show_pyrevit_report"),
]

# Суффикс имён контролов -> ключ настроек
LISTS = [
    ("RuleCodes", "proxy_rule_codes"),
    ("PosTypes", "proxy_position_types"),
    ("SheetPrefixes", "sheet_name_prefixes"),
    ("LocationMap", "sheet_plan_location_map"),
    ("SectionMap", "sheet_plan_section_map"),
]


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
#  Редактор одного списка
# ======================================================================

class ListEditor(pp_wpf.Notifier):
    u"""Список строк с полем ввода под ним.

    Своя ViewModel на каждый список: иначе на главную VM пришлось бы вешать
    по три свойства на каждый из пяти списков.
    """

    def __init__(self, title, values, report):
        pp_wpf.Notifier.__init__(self)

        self.title = title
        self.report = report          # куда писать строку статуса

        self._items = ObservableCollection[object]()

        for value in values or []:
            self._items.Add(unicode(value))

        self.list_box = None
        self.text_box = None

    # ---------- состояние, читаемое разметкой ----------------------

    @property
    def Items(self):
        return self._items

    @property
    def IsEmpty(self):
        return self._items.Count == 0

    # ---------- привязка к контролам --------------------------------

    def attach(self, box, list_box, text_box):
        box.DataContext = self
        self.list_box = list_box
        self.text_box = text_box

    # ---------- команды ---------------------------------------------

    def draft(self):
        if self.text_box is None:
            return u""

        return unicode(self.text_box.Text).strip()

    def selected_index(self):
        if self.list_box is None:
            return -1

        return self.list_box.SelectedIndex

    def pick(self):
        u"""Выбранная строка уезжает в поле ввода — оттуда её и правят."""
        index = self.selected_index()

        if index < 0 or self.text_box is None:
            return

        self.text_box.Text = unicode(self._items[index])

    def add(self):
        value = self.draft()

        if not value:
            self.report(u"{}: введите значение в поле под списком.".format(self.title), True)
            return

        if self._contains(value):
            self.report(u"{}: «{}» уже есть в списке.".format(self.title, value), True)
            return

        self._items.Add(value)
        self._changed()
        self.report(u"{}: добавлено «{}».".format(self.title, value), False)

    def replace(self):
        index = self.selected_index()

        if index < 0:
            self.report(u"{}: выберите строку, которую нужно заменить.".format(self.title), True)
            return

        value = self.draft()

        if not value:
            self.report(u"{}: введите новое значение в поле под списком.".format(self.title), True)
            return

        old = unicode(self._items[index])

        if old == value:
            return

        if self._contains(value):
            self.report(u"{}: «{}» уже есть в списке.".format(self.title, value), True)
            return

        self._items[index] = value
        self._changed()
        self.list_box.SelectedIndex = index
        self.report(u"{}: «{}» заменено на «{}».".format(self.title, old, value), False)

    def delete(self):
        index = self.selected_index()

        if index < 0:
            self.report(u"{}: выберите строку, которую нужно удалить.".format(self.title), True)
            return

        old = unicode(self._items[index])

        self._items.RemoveAt(index)
        self._changed()

        # Подтверждения нет намеренно: на диск ничего не уходит до «Сохранить»
        self.report(u"{}: удалено «{}». Отмена вернёт всё как было.".format(self.title, old), False)

    def values(self):
        return [unicode(self._items[i]) for i in range(self._items.Count)]

    # ---------- служебное --------------------------------------------

    def _contains(self, value):
        for i in range(self._items.Count):
            if unicode(self._items[i]) == value:
                return True

        return False

    def _changed(self):
        self.notify(u"IsEmpty")


# ======================================================================
#  ViewModel окна
# ======================================================================

class SettingsVM(pp_wpf.Notifier):

    def __init__(self):
        pp_wpf.Notifier.__init__(self)

        self.settings = load_settings()

        self._section = 0

        self._flags = {}

        for _, key in FLAGS:
            self._flags[key] = bool(self.settings.get(key, DEFAULT_SETTINGS.get(key, False)))

        self._distance_text = format_number(
            self.settings.get("pipe_pair_distance_mm", DEFAULT_SETTINGS["pipe_pair_distance_mm"])
        )

        self.editors = {}

        for suffix, key in LISTS:
            self.editors[suffix] = ListEditor(
                self._list_title(suffix),
                self.settings.get(key, DEFAULT_SETTINGS.get(key, [])),
                self.set_status
            )

        self._status = u"Настройки загружены."
        self._status_error = False

        self.imported = False

        self.revalidate()

    def _list_title(self, suffix):
        return {
            "RuleCodes": u"Коды правил",
            "PosTypes": u"Типы позиций",
            "SheetPrefixes": u"Начала имени листа",
            "LocationMap": u"Словарь локаций",
            "SectionMap": u"Словарь разделов",
        }[suffix]

    # ---------- разделы ----------------------------------------------

    @property
    def IsGeneral(self):
        return self._section == 0

    @property
    def IsProxy(self):
        return self._section == 1

    @property
    def IsSheets(self):
        return self._section == 2

    @property
    def IsHeatloss(self):
        return self._section == 3

    @property
    def IsCheck(self):
        return self._section == 4

    @property
    def IsBackup(self):
        return self._section == 5

    def select_section(self, index):
        if index < 0 or index == self._section:
            return

        self._section = index

        self.notify(u"IsGeneral", u"IsProxy", u"IsSheets",
                    u"IsHeatloss", u"IsCheck", u"IsBackup")

    # ---------- прочее состояние --------------------------------------

    @property
    def SettingsPath(self):
        try:
            return get_settings_path()
        except Exception:
            return u"—"

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
        return self.distance_mm is not None

    @property
    def distance_mm(self):
        value = parse_number(self._distance_text)

        if value is None or value <= 0:
            return None

        return value

    # ---------- команды -----------------------------------------------

    def set_flag(self, key, value):
        self._flags[key] = bool(value)

    def flag(self, key):
        return self._flags.get(key, False)

    def set_distance(self, text):
        self._distance_text = text
        self.revalidate()

    def distance_text(self):
        return self._distance_text

    def revalidate(self):
        if parse_number(self._distance_text) is None:
            self.set_status(u"Расстояние для пары труб: введите число, например 600.", True)
        elif self.distance_mm is None:
            self.set_status(u"Расстояние для пары труб должно быть больше нуля.", True)
        elif self._status_error:
            self.set_status(u"Готово к сохранению.", False)

        self.notify(u"IsValid")

    def set_status(self, text, is_error):
        self._status = text
        self._status_error = is_error
        self.notify(u"Status", u"StatusBrush")

    def save(self):
        if not self.IsValid:
            return False

        self.settings["pipe_pair_distance_mm"] = self.distance_mm

        for _, key in FLAGS:
            self.settings[key] = self._flags[key]

        for suffix, key in LISTS:
            self.settings[key] = self.editors[suffix].values()

        save_settings(self.settings)

        return True

    # ---------- экспорт и импорт ---------------------------------------

    def export_to_file(self):
        dialog = SaveFileDialog()
        dialog.Title = u"Экспорт настроек PP Tools"
        dialog.Filter = u"JSON-файл (*.json)|*.json"
        dialog.FileName = u"PP_Tools_settings_{}.json".format(
            datetime.now().strftime("%Y-%m-%d")
        )

        if dialog.ShowDialog() != True:
            return

        export_settings(dialog.FileName)

        self.set_status(u"Экспортировано в {}".format(os.path.basename(dialog.FileName)), False)

    def import_from_file(self):
        u"""Возвращает True, если импорт прошёл и окно пора закрывать."""
        dialog = OpenFileDialog()
        dialog.Title = u"Импорт настроек PP Tools"
        dialog.Filter = u"JSON-файл (*.json)|*.json"

        if dialog.ShowDialog() != True:
            return False

        import_settings(dialog.FileName)

        self.imported = True

        return True


# ======================================================================
#  Окно
# ======================================================================

class SettingsWindow(object):

    def __init__(self, script_dir):
        xaml_path = os.path.join(script_dir, u"ui.xaml")

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = SettingsVM()
        self.window.DataContext = self.vm

        self.saved = False

        self._fill_controls()
        self._wire()

        pp_wpf.set_owner(self.window)

    # ---------- начальное состояние до подписки на события ------------

    def _fill_controls(self):
        find = self.window.FindName

        find("LstSections").SelectedIndex = 0

        for name, key in FLAGS:
            find(name).IsChecked = self.vm.flag(key)

        find("TxtDistance").Text = self.vm.distance_text()

        for suffix, _ in LISTS:
            self.vm.editors[suffix].attach(
                find("Box" + suffix),
                find("Lst" + suffix),
                find("Txt" + suffix)
            )

    # ---------- подписки ------------------------------------------------

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        @guard
        def on_section(sender, args):
            self.vm.select_section(sender.SelectedIndex)

        find("LstSections").SelectionChanged += on_section

        # Фабрики обработчиков: общий цикл с lambda отдал бы всем контролам
        # последний ключ из списка.
        def make_flag_handler(key):
            @guard
            def handler(sender, args):
                self.vm.set_flag(key, sender.IsChecked)

            return handler

        for name, key in FLAGS:
            handler = make_flag_handler(key)
            control = find(name)
            control.Checked += handler
            control.Unchecked += handler

        @guard
        def on_distance(sender, args):
            self.vm.set_distance(sender.Text)

        find("TxtDistance").TextChanged += on_distance

        def make_list_handlers(suffix):
            editor = self.vm.editors[suffix]

            @guard
            def on_add(sender, args):
                editor.add()

            @guard
            def on_set(sender, args):
                editor.replace()

            @guard
            def on_del(sender, args):
                editor.delete()

            @guard
            def on_pick(sender, args):
                editor.pick()

            return on_add, on_set, on_del, on_pick

        for suffix, _ in LISTS:
            on_add, on_set, on_del, on_pick = make_list_handlers(suffix)

            find("BtnAdd" + suffix).Click += on_add
            find("BtnSet" + suffix).Click += on_set
            find("BtnDel" + suffix).Click += on_del
            find("Lst" + suffix).SelectionChanged += on_pick

        @guard
        def on_export(sender, args):
            self.vm.export_to_file()

        @guard
        def on_import(sender, args):
            if self.vm.import_from_file():
                # Закрываем без сохранения, иначе форма перезапишет
                # только что импортированный файл старыми значениями.
                self.window.DialogResult = False

        find("BtnExport").Click += on_export
        find("BtnImport").Click += on_import

        @guard
        def on_save(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("BtnSave").Click += on_save
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    # ---------- действия --------------------------------------------------

    def _accept(self):
        if not self.vm.IsValid:
            return

        if self.vm.save():
            self.saved = True
            self.window.DialogResult = True

    def _fail(self, message):
        self.vm.set_status(message, True)

    # ---------- запуск -----------------------------------------------------

    def show(self):
        u"""Возвращает 'saved', 'imported' или None."""
        self.window.ShowDialog()

        if self.saved:
            return u"saved"

        if self.vm.imported:
            return u"imported"

        return None


def show(script_dir):
    return SettingsWindow(script_dir).show()

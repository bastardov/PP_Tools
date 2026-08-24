# -*- coding: utf-8 -*-
u"""Окно «Инфо»: сведения о плагине и о среде, в которой он запущен.

Всё содержимое собирается в ViewModel при создании окна. Модель Revit не
трогается, только Application, поэтому ExternalEvent не нужен.
"""

import os
import sys
import time

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

import clr

clr.AddReference("System")

from System import Uri
from System.Diagnostics import Process
from System.Windows import Application, Clipboard
from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption


DEVELOPER = u"PP"

SKIP_DIRS = ("BACKUPS", "__pycache__", ".git", "_logs", "telemetry")


# ======================================================================
#  Сбор сведений
# ======================================================================

def plural(count, forms):
    u"""forms = (вкладка, вкладки, вкладок)."""
    count = abs(int(count))

    if count % 10 == 1 and count % 100 != 11:
        return forms[0]

    if 2 <= count % 10 <= 4 and not (12 <= count % 100 <= 14):
        return forms[1]

    return forms[2]


def uiapp():
    try:
        from pyrevit import HOST_APP
        return HOST_APP.uiapp
    except Exception:
        pass

    try:
        import __builtin__
        return __builtin__.__revit__
    except Exception:
        return None


def extension_root(start):
    u"""Поднимаемся вверх до папки *.extension."""
    path = os.path.abspath(start)

    for _ in range(12):
        if path.lower().endswith(".extension"):
            return path

        parent = os.path.dirname(path)

        if parent == path:
            break

        path = parent

    return os.path.abspath(start)


def scan_extension(root):
    u"""Считает вкладки, панели и кнопки, попутно ищет самый свежий файл."""
    tabs = 0
    panels = 0
    buttons = 0
    newest = 0.0

    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

        for name in dirs:
            lower = name.lower()

            if lower.endswith(".tab"):
                tabs += 1
            elif lower.endswith(".panel"):
                panels += 1
            elif lower.endswith(".pushbutton"):
                buttons += 1

        for name in files:
            if not (name.endswith(".py") or name.endswith(".xaml") or name.endswith(".yaml")):
                continue

            try:
                stamp = os.path.getmtime(os.path.join(current, name))

                if stamp > newest:
                    newest = stamp
            except Exception:
                pass

    return tabs, panels, buttons, newest


def revit_version():
    app = uiapp()

    if app is None:
        return u"—"

    try:
        application = app.Application

        return u"{} (сборка {})".format(
            application.VersionName,
            application.VersionBuild
        )
    except Exception:
        return u"—"


def revit_user():
    app = uiapp()

    if app is None:
        return u"—"

    try:
        return app.Application.Username
    except Exception:
        return u"—"


def pyrevit_version():
    try:
        from pyrevit import versionmgr
        return unicode(versionmgr.get_pyrevit_version().get_formatted())
    except Exception:
        pass

    try:
        from pyrevit import VERSION_STRING
        return unicode(VERSION_STRING)
    except Exception:
        return u"—"


def python_version():
    try:
        return u"IronPython {}".format(sys.version.split(u" ")[0])
    except Exception:
        return u"—"


# ======================================================================
#  ViewModel
# ======================================================================

class InfoVM(pp_wpf.Notifier):

    def __init__(self, script_dir):
        pp_wpf.Notifier.__init__(self)

        self.script_dir = script_dir
        self.root = extension_root(script_dir)

        tabs, panels, buttons, newest = scan_extension(self.root)

        self._composition = u"{} {} · {} {} · {} {}".format(
            tabs, plural(tabs, (u"вкладка", u"вкладки", u"вкладок")),
            panels, plural(panels, (u"панель", u"панели", u"панелей")),
            buttons, plural(buttons, (u"кнопка", u"кнопки", u"кнопок"))
        )

        if newest > 0:
            self._updated = u"Обновлено {}".format(
                time.strftime(u"%d.%m.%Y в %H:%M", time.localtime(newest))
            )
        else:
            self._updated = u"Дата обновления не определена"

        self._revit = revit_version()
        self._pyrevit = pyrevit_version()
        self._python = python_version()
        self._user = revit_user()

        self._status = u"Расширение загружено."
        self._status_error = False

    # ---------- состояние, читаемое разметкой ----------------------

    @property
    def Developer(self):
        return DEVELOPER

    @property
    def Composition(self):
        return self._composition

    @property
    def RevitVersion(self):
        return self._revit

    @property
    def PyRevitVersion(self):
        return self._pyrevit

    @property
    def PythonVersion(self):
        return self._python

    @property
    def UserName(self):
        return self._user

    @property
    def ExtensionPath(self):
        return self.root

    @property
    def UpdatedAt(self):
        return self._updated

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

    # ---------- команды ---------------------------------------------

    def summary_text(self):
        u"""То же, что видно в окне, но одним куском текста."""
        return u"\n".join([
            u"PP Tools — плагин автоматизации Revit (ОВиК и BIM)",
            u"Разработчик: {}".format(DEVELOPER),
            u"Состав: {}".format(self._composition),
            u"",
            u"Revit: {}".format(self._revit),
            u"pyRevit: {}".format(self._pyrevit),
            u"Интерпретатор: {}".format(self._python),
            u"Пользователь: {}".format(self._user),
            u"",
            u"Расположение: {}".format(self.root),
            self._updated,
        ])

    def copy_summary(self):
        try:
            Clipboard.SetDataObject(self.summary_text(), True)
        except Exception:
            # Буфер иногда занят другим процессом — вторая попытка обычно проходит
            Clipboard.SetText(self.summary_text())

        self._set_status(u"Сведения скопированы в буфер обмена.", False)

    def open_folder(self):
        if not os.path.isdir(self.root):
            self._set_status(u"Папка расширения не найдена: {}".format(self.root), True)
            return

        Process.Start(u"explorer.exe", u'"{}"'.format(self.root))

        self._set_status(u"Папка открыта в проводнике.", False)

    def _set_status(self, text, is_error):
        self._status = text
        self._status_error = is_error
        self.notify(u"Status", u"StatusBrush")


# ======================================================================
#  Окно
# ======================================================================

class InfoWindow(object):

    def __init__(self, script_dir):
        self.script_dir = script_dir

        xaml_path = os.path.join(script_dir, u"ui.xaml")

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = InfoVM(script_dir)
        self.window.DataContext = self.vm

        self._load_logo()
        self._wire()

        pp_wpf.set_owner(self.window)

    def _load_logo(self):
        u"""Растр допустим только для самого логотипа: вектора для него нет."""
        path = os.path.join(self.script_dir, u"logo.png")

        if not os.path.isfile(path):
            return

        try:
            image = BitmapImage()
            image.BeginInit()
            image.UriSource = Uri(path)
            # Иначе файл остаётся занятым до закрытия Revit
            image.CacheOption = BitmapCacheOption.OnLoad
            image.EndInit()

            self.window.FindName("ImgLogo").Source = image
        except Exception as ex:
            self.vm._set_status(u"Логотип не загружен: {}".format(unicode(ex)), True)

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        @guard
        def on_copy(sender, args):
            self.vm.copy_summary()

        @guard
        def on_folder(sender, args):
            self.vm.open_folder()

        @guard
        def on_close(sender, args):
            self.window.Close()

        find("BtnCopy").Click += on_copy
        find("BtnFolder").Click += on_folder
        find("BtnClose").Click += on_close

        pp_wpf.wire_keys(self.window, on_accept=self.window.Close)

    def _fail(self, message):
        self.vm._set_status(message, True)

    def show(self):
        self.window.ShowDialog()


def show(script_dir):
    InfoWindow(script_dir).show()

# -*- coding: utf-8 -*-
u"""PP_Tools — базовый слой WPF-окон.

Модуль решает четыре задачи, которые иначе дублировались бы в каждой кнопке:

    * Notifier          — INotifyPropertyChanged для IronPython
    * load_theme()      — подключение общей темы pp_theme.xaml
    * load_window()     — разбор XAML-строки в объект Window
    * set_owner()       — привязка окна к главному окну Revit

Логики Revit здесь нет и быть не должно.
"""

import os
import codecs

import clr

clr.AddReference("PresentationCore")
clr.AddReference("PresentationFramework")
clr.AddReference("WindowsBase")
clr.AddReference("System.Xml")

from System import Uri, UriKind
from System.IO import StringReader
from System.Xml import XmlReader
from System.Windows import (
    Application, ResourceDictionary, SizeToContent, SystemParameters, Window
)
from System.Windows.Input import Key
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs
from System.Globalization import CultureInfo
from System.Windows.Data import IValueConverter


THEME_FILE = u"pp_theme.xaml"


# ======================================================================
#  Notifier
# ======================================================================

class Notifier(INotifyPropertyChanged):
    u"""Базовый класс ViewModel.

    IronPython не умеет объявлять .NET-события напрямую, поэтому
    INotifyPropertyChanged реализуется парой add_/remove_ методов.
    """

    def __init__(self):
        self._pp_handlers = []

    # -- реализация интерфейса --------------------------------------
    def add_PropertyChanged(self, handler):
        self._pp_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        try:
            self._pp_handlers.remove(handler)
        except ValueError:
            pass

    # -- вызов из наследников ---------------------------------------
    def notify(self, *names):
        u"""Сообщить биндингам, что перечисленные свойства изменились."""
        for name in names:
            args = PropertyChangedEventArgs(name)
            for handler in list(self._pp_handlers):
                try:
                    handler(self, args)
                except Exception:
                    pass


# ======================================================================
#  Конвертеры общего назначения
# ======================================================================

class BoolToVisibilityConverter(IValueConverter):
    u"""True -> Visible, иначе Collapsed. Параметр "invert" переворачивает."""

    def Convert(self, value, target_type, parameter, culture):
        from System.Windows import Visibility

        flag = bool(value)

        if parameter is not None and unicode(parameter).lower() == u"invert":
            flag = not flag

        return Visibility.Visible if flag else Visibility.Collapsed

    def ConvertBack(self, value, target_type, parameter, culture):
        from System.Windows import Visibility
        return value == Visibility.Visible


class NullToVisibilityConverter(IValueConverter):
    u"""Непустое значение -> Visible, пустое -> Collapsed."""

    def Convert(self, value, target_type, parameter, culture):
        from System.Windows import Visibility

        empty = value is None or unicode(value).strip() == u""

        return Visibility.Collapsed if empty else Visibility.Visible

    def ConvertBack(self, value, target_type, parameter, culture):
        return None


# ======================================================================
#  Тема
# ======================================================================

def _lib_dir():
    return os.path.dirname(os.path.abspath(__file__))


def theme_path():
    return os.path.join(_lib_dir(), THEME_FILE)


def _read_text(path):
    text = codecs.open(path, "r", "utf-8").read()

    if text and text[0] == u"﻿":
        text = text[1:]

    return text


def _ensure_application():
    u"""Revit не является WPF-приложением, Application.Current обычно пуст.

    Собственный экземпляр без вызова Run() безопасен и нужен только как
    место хранения общих ресурсов: XamlReader ищет StaticResource именно
    в этой цепочке.
    """
    if Application.Current is None:
        app = Application()
        try:
            from System.Windows import ShutdownMode
            app.ShutdownMode = ShutdownMode.OnExplicitShutdown
        except Exception:
            pass

    return Application.Current


def _register_converters(resources):
    u"""Конвертеры кладутся до разбора XAML — иначе StaticResource не найдёт их."""
    pairs = [
        (u"BoolVis", BoolToVisibilityConverter),
        (u"NullVis", NullToVisibilityConverter),
    ]

    for key, cls in pairs:
        if not resources.Contains(key):
            resources[key] = cls()


_theme_loaded = [False]


def load_theme():
    u"""Подключить pp_theme.xaml к ресурсам приложения. Повторный вызов безвреден."""
    app = _ensure_application()

    if app is None:
        return False

    _register_converters(app.Resources)

    if _theme_loaded[0]:
        return True

    path = theme_path()

    if not os.path.isfile(path):
        raise IOError(u"Не найден файл темы: {}".format(path))

    rd = _parse_xaml(_read_text(path), path)

    app.Resources.MergedDictionaries.Add(rd)
    _theme_loaded[0] = True

    return True


def _parse_xaml(text, source_name):
    u"""XamlReader сообщает только номер строки. Показываем саму строку."""
    try:
        return XamlReader.Load(XmlReader.Create(StringReader(text)))
    except Exception as ex:
        line_no = 0

        try:
            line_no = int(ex.LineNumber)
        except Exception:
            pass

        snippet = u""

        if line_no > 0:
            lines = text.split(u"\n")
            lo = max(0, line_no - 2)
            hi = min(len(lines), line_no + 1)

            snippet = u"\n".join(
                u"{}{:>4}: {}".format(
                    u">> " if (i + 1) == line_no else u"   ",
                    i + 1,
                    lines[i].rstrip()
                )
                for i in range(lo, hi)
            )

        raise Exception(
            u"Ошибка разбора XAML.\n\nФайл: {}\nСтрока: {}\n\n{}\n\n{}".format(
                source_name, line_no, snippet, unicode(ex)
            )
        )


# ======================================================================
#  Окна
# ======================================================================

def load_window(xaml_text, source_name=u"<строка>"):
    u"""Разобрать XAML-строку в объект Window. Тема подключается автоматически."""
    load_theme()

    return _parse_xaml(xaml_text, source_name)


def load_window_file(path):
    u"""То же, но из файла .xaml рядом со скриптом."""
    return load_window(_read_text(path), path)


def main_window_handle():
    try:
        from pyrevit import HOST_APP
        return HOST_APP.uiapp.MainWindowHandle
    except Exception:
        pass

    try:
        import __builtin__
        return __builtin__.__revit__.MainWindowHandle
    except Exception:
        return None


def set_owner(window):
    u"""Иначе окно проваливается за главное окно Revit."""
    handle = main_window_handle()

    if handle is None:
        return False

    try:
        WindowInteropHelper(window).Owner = handle
        return True
    except Exception:
        return False


def fit_to_screen(window, margin=90):
    u"""Ужать окно до рабочей области экрана, если оно в неё не помещается.

    Размер из XAML — желаемый «удобный из коробки». На маленьком экране или
    при крупном масштабе Windows окно не должно уезжать за край: высота и
    ширина обрезаются по рабочей области минус поля, но не ниже MinHeight
    и MinWidth. Вызывать после set_owner, до показа окна.
    """
    try:
        area = SystemParameters.WorkArea
    except Exception:
        return False

    try:
        limit_h = area.Height - margin
        limit_w = area.Width - margin

        if window.Height > limit_h:
            window.Height = max(window.MinHeight, limit_h)

        if window.Width > limit_w:
            window.Width = max(window.MinWidth, limit_w)

        return True
    except Exception:
        return False


def wire_keys(window, on_accept=None, on_cancel=None):
    u"""Enter — основное действие, Esc — закрытие."""

    def handler(sender, args):
        try:
            if args.Key == Key.Escape:
                if on_cancel is not None:
                    on_cancel()
                else:
                    window.Close()
                args.Handled = True

            elif args.Key == Key.Enter:
                if on_accept is not None:
                    on_accept()
                    args.Handled = True
        except Exception:
            pass

    window.PreviewKeyDown += handler


def guard(status_setter):
    u"""Декоратор обработчика: исключение уходит в строку статуса, а не в Revit.

    Исключение внутри обработчика WPF роняет Revit молча, поэтому тело
    каждого обработчика оборачивается.
    """

    def decorator(func):
        def wrapper(sender, args):
            try:
                return func(sender, args)
            except Exception as ex:
                try:
                    status_setter(u"Ошибка: {}".format(unicode(ex)))
                except Exception:
                    pass

        return wrapper

    return decorator


# ======================================================================
#  Окно отчёта — замена forms.alert
# ======================================================================

_REPORT_XAML = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="PP_Tools"
        Width="480"
        MinWidth="380"
        MaxHeight="620"
        SizeToContent="Height"
        ResizeMode="CanResize"
        WindowStartupLocation="CenterOwner"
        ShowInTaskbar="False"
        FontFamily="Segoe UI"
        Background="{StaticResource Canvas}">
  <Window.Resources>
    <Style TargetType="ScrollBar" BasedOn="{StaticResource PP.ScrollBar}"/>
    <Style TargetType="ToolTip" BasedOn="{StaticResource PP.ToolTip}"/>
  </Window.Resources>
  <Grid Margin="20">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <StackPanel Grid.Row="0" Orientation="Horizontal">
      <Grid Width="26" Height="26" VerticalAlignment="Center" Margin="0,0,12,0">
        <Ellipse x:Name="IconRing" Stroke="{StaticResource Accent}" StrokeThickness="1.8" Fill="Transparent"/>
        <Path x:Name="IconOk" Data="M 7,13.5 L 11,17.5 L 19,8.5"
              Stroke="{StaticResource Accent}" StrokeThickness="1.8"
              StrokeStartLineCap="Round" StrokeEndLineCap="Round" StrokeLineJoin="Round"/>
        <Path x:Name="IconBad" Visibility="Collapsed"
              Data="M 13,7 L 13,14.5 M 13,18 L 13,19.5"
              Stroke="{StaticResource Hot}" StrokeThickness="1.8"
              StrokeStartLineCap="Round" StrokeEndLineCap="Round"/>
      </Grid>
      <TextBlock x:Name="TxtTitle" Style="{StaticResource H1}" VerticalAlignment="Center" Text="Готово"/>
    </StackPanel>

    <TextBlock x:Name="TxtSubtitle" Grid.Row="1" Style="{StaticResource Subtitle}"
               Margin="0,6,0,0" Visibility="Collapsed"/>

    <Border Grid.Row="2" Style="{StaticResource Card}" Margin="0,14,0,0">
      <ScrollViewer x:Name="BodyScroll" VerticalScrollBarVisibility="Auto"
                    HorizontalScrollBarVisibility="Disabled" Padding="14,12,10,12">
        <TextBlock x:Name="TxtBody" Style="{StaticResource Body}"/>
      </ScrollViewer>
    </Border>

    <Grid Grid.Row="3" Margin="0,14,0,0">
      <Button x:Name="BtnClose" Content="Закрыть"
              Style="{StaticResource Button.Primary}"
              HorizontalAlignment="Right"/>
    </Grid>
  </Grid>
</Window>
"""


def show_report(body, title=u"Готово", subtitle=None, is_error=False,
                monospace=False, width=None):
    u"""Модальное окно отчёта в стиле плагина. Возврат: None.

    monospace — для отчётов-таблиц, где важно выравнивание колонок: текст
    выводится Consolas без переноса строк, появляется горизонтальная прокрутка.
    width — ширина окна, если стандартной мало.
    """
    from System.Windows import Visibility, TextWrapping
    from System.Windows.Controls import ScrollBarVisibility
    from System.Windows.Media import FontFamily

    window = load_window(_REPORT_XAML, u"pp_wpf._REPORT_XAML")

    window.Title = title
    window.FindName("TxtTitle").Text = title
    window.FindName("TxtBody").Text = body

    if width:
        window.Width = width

    if monospace:
        text_block = window.FindName("TxtBody")
        text_block.FontFamily = FontFamily(u"Consolas")
        text_block.FontSize = 12
        text_block.TextWrapping = TextWrapping.NoWrap

        scroll = window.FindName("BodyScroll")
        scroll.HorizontalScrollBarVisibility = ScrollBarVisibility.Auto

        # Таблице нужно место: иначе окно подгонит высоту под первый экран
        window.SizeToContent = SizeToContent.Manual
        window.Height = 620

    if subtitle:
        sub = window.FindName("TxtSubtitle")
        sub.Text = subtitle
        sub.Visibility = Visibility.Visible

    if is_error:
        app_res = Application.Current.Resources
        hot = app_res["Hot"]

        window.FindName("IconOk").Visibility = Visibility.Collapsed
        window.FindName("IconBad").Visibility = Visibility.Visible
        window.FindName("IconRing").Stroke = hot

    btn = window.FindName("BtnClose")
    btn.Click += lambda s, e: window.Close()

    wire_keys(window, on_accept=lambda: window.Close())
    set_owner(window)

    window.ShowDialog()

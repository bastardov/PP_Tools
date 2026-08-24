# -*- coding: utf-8 -*-
"""PP_Tools — логотип на заголовке вкладки ленты Revit.

Revit API не умеет ставить картинку на вкладку. Это делается через
недокументированный AdWindows.dll: находим в визуальном дереве ленты
RibbonTabHeader нужной вкладки и вставляем Image рядом с текстом.

Скрипт полностью защищён try/except: если что-то пойдёт не так,
загрузка pyRevit не пострадает, а причина попадёт в _logs/tab_logo.log.
"""

import os
import sys
import datetime

# ---------------------------------------------------------------- настройки
# заголовки вкладок, которым ставим логотип (сравнение точное, без регистра).
# Старые имена оставлены на случай, если title из bundle.yaml не подхватится.
TAB_TITLES = [u'ОВиК', u'Расчеты', u'PP_ОВиК', u'PP_ОВиК 2']
LOGO_FILE = u'pp_tab_logo.png'
LOGO_SIZE = 14             # высота картинки в пикселях
GAP = 4                    # отступ между картинкой и текстом
# порядок вкладок слева направо; вкладки не из списка не трогаем
TAB_ORDER = [u'ОВиК', u'Расчеты']

TICK_MS = 1500             # период опроса ленты, мс (таймер не выключается)
MAX_TRIES = 20             # тиков без единого успеха, после которых сдаёмся

try:
    _EXT_DIR = os.path.dirname(os.path.abspath(__file__))
except Exception:
    _EXT_DIR = None

_LOG_PATH = None
if _EXT_DIR:
    _LOG_PATH = os.path.join(_EXT_DIR, '_logs', 'tab_logo.log')


def _log(msg):
    """Пишем в лог. Никогда не падаем."""
    try:
        if not _LOG_PATH:
            return
        d = os.path.dirname(_LOG_PATH)
        if not os.path.isdir(d):
            os.makedirs(d)
        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = u'[%s] %s\n' % (stamp, msg)
        import codecs
        f = codecs.open(_LOG_PATH, 'a', encoding='utf-8')
        try:
            f.write(line)
        finally:
            f.close()
    except Exception:
        pass


# ------------------------------------------------------------------ импорты
_READY = False
try:
    import clr
    clr.AddReference('AdWindows')
    clr.AddReference('PresentationCore')
    clr.AddReference('PresentationFramework')
    clr.AddReference('WindowsBase')

    from Autodesk.Windows import ComponentManager
    from System import Uri, UriKind, TimeSpan
    from System.Windows.Threading import (Dispatcher, DispatcherTimer,
                                          DispatcherPriority)
    from System.Windows import Thickness, VerticalAlignment, HorizontalAlignment
    from System.Windows.Controls import (Image, Panel, Grid, TextBlock,
                                         StackPanel, DockPanel)
    from System.Windows.Media import (VisualTreeHelper, RenderOptions,
                                      BitmapScalingMode, Stretch)
    from System.Windows.Media.Imaging import (BitmapImage, BitmapCacheOption,
                                              BitmapCreateOptions)
    _READY = True
except Exception as exc:
    _log(u'Не удалось загрузить сборки WPF/AdWindows: %s' % exc)


# ------------------------------------------------------------- вспомогалки
def _walk(elem, depth=0):
    """Обход визуального дерева вглубь."""
    if depth > 60:
        return
    try:
        count = VisualTreeHelper.GetChildrenCount(elem)
    except Exception:
        return
    for i in range(count):
        try:
            child = VisualTreeHelper.GetChild(elem, i)
        except Exception:
            continue
        yield child
        for sub in _walk(child, depth + 1):
            yield sub


_BITMAP_CACHE = {}
_DIAG = {}


def _log_once(key, msg):
    """Лог без спама: таймер тикает вечно, одно и то же писать не надо."""
    if _DIAG.get('logged_' + key):
        return
    _DIAG['logged_' + key] = True
    _log(msg)


def _bitmap(path):
    if path in _BITMAP_CACHE:
        return _BITMAP_CACHE[path]
    bi = BitmapImage()
    bi.BeginInit()
    bi.UriSource = Uri(path, UriKind.Absolute)
    bi.CacheOption = BitmapCacheOption.OnLoad
    bi.CreateOptions = BitmapCreateOptions.IgnoreImageCache
    bi.EndInit()
    bi.Freeze()
    _BITMAP_CACHE[path] = bi
    return bi


def _make_image(path):
    src = _bitmap(path)
    img = Image()
    img.Source = src
    # ширину не задаём — считаем от пропорций картинки,
    # иначе широкая монограмма сплющится в квадрат
    img.Height = LOGO_SIZE
    img.Stretch = Stretch.Uniform
    img.VerticalAlignment = VerticalAlignment.Center
    img.SnapsToDevicePixels = True
    img.Tag = 'PP_LOGO'
    RenderOptions.SetBitmapScalingMode(img, BitmapScalingMode.HighQuality)
    return img


def _norm(title):
    """Нормализуем заголовок: регистр, пробелы, ё→е."""
    if not title:
        return u''
    try:
        return title.strip().upper().replace(u'Ё', u'Е')
    except Exception:
        return u''


_WANTED = set(_norm(t) for t in TAB_TITLES)


def _wanted_title(title):
    return _norm(title) in _WANTED


def _is_tab_header(node):
    """Похоже ли это на визуал заголовка вкладки."""
    name = node.GetType().Name
    return ('RibbonTabHeader' in name) or ('RibbonTabButton' in name)


def _title_of(header):
    """Заголовок вкладки, к которой относится этот визуал."""
    for attr in ('DataContext', 'Tab', 'RibbonTab', 'Content'):
        try:
            ctx = getattr(header, attr, None)
        except Exception:
            continue
        if ctx is None:
            continue
        try:
            title = getattr(ctx, 'Title', None)
        except Exception:
            title = None
        if title:
            return title
    return None


def _dump_header(header):
    """Один раз выгружаем структуру заголовка — чтобы было по чему чинить."""
    if _DIAG.get('header_dumped'):
        return
    _DIAG['header_dumped'] = True
    try:
        ctx = getattr(header, 'DataContext', None)
        _log(u'Заголовок: %s, DataContext: %s'
             % (header.GetType().Name,
                ctx.GetType().Name if ctx is not None else u'None'))
    except Exception:
        pass
    lines = []

    def rec(node, depth):
        if depth > 12 or len(lines) > 60:
            return
        try:
            cnt = VisualTreeHelper.GetChildrenCount(node)
        except Exception:
            return
        for i in range(cnt):
            try:
                ch = VisualTreeHelper.GetChild(node, i)
            except Exception:
                continue
            extra = u''
            try:
                if hasattr(ch, 'Text') and ch.Text:
                    extra = u' text=«%s»' % ch.Text
            except Exception:
                pass
            lines.append(u'%s%s%s' % (u'  ' * depth, ch.GetType().Name, extra))
            rec(ch, depth + 1)

    rec(header, 1)
    _log(u'Дерево заголовка:\n%s' % u'\n'.join(lines))


def _find_text_element(header):
    """Элемент, показывающий название вкладки."""
    for child in _walk(header):
        name = child.GetType().Name
        if 'TextBlock' in name or isinstance(child, TextBlock):
            try:
                if getattr(child, 'Text', None):
                    return child
            except Exception:
                return child
    return None


def _panel_anchor(header, node):
    """Поднимаемся от node вверх до ближайшей Panel.

    Возвращает (panel, ребёнок_панели_на_пути) или (None, None).
    """
    child = node
    for _ in range(12):
        try:
            parent = VisualTreeHelper.GetParent(child)
        except Exception:
            return (None, None)
        if parent is None:
            return (None, None)
        if isinstance(parent, Panel):
            return (parent, child)
        if parent is header:
            return (None, None)
        child = parent
    return (None, None)


_PANELS = []   # панели заголовков, куда уже вставлена картинка


def _is_attached(node, root):
    """Узел всё ещё висит в дереве ленты? Подъём по родителям — дёшево."""
    cur = node
    for _ in range(40):
        if cur is root:
            return True
        try:
            cur = VisualTreeHelper.GetParent(cur)
        except Exception:
            return False
        if cur is None:
            return False
    return False


def _fast_check(ribbon, expected):
    """Быстрый путь: панели живы и картинки на месте — обход не нужен.

    Стоит ~20 обращений вместо ~1500 при полном обходе дерева.
    """
    if len(_PANELS) < expected:
        return False
    for panel in _PANELS:
        if not _is_attached(panel, ribbon):
            return False
        if not _panel_has_logo(panel):
            return False
    return True


def _panel_has_logo(panel):
    """Наша картинка уже лежит в этой панели?

    Проверяем сам визуал, а не флаг на заголовке: Revit переиспользует
    RibbonTabButton между вкладками, и флаг соврал бы.
    """
    try:
        for child in panel.Children:
            try:
                if child.Tag == 'PP_LOGO':
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _inject(header, logo_path):
    """Вставить картинку в заголовок вкладки. True — картинка на месте."""
    _dump_header(header)

    text_el = _find_text_element(header)
    if text_el is None:
        _log_once('no_text', u'В заголовке не найден элемент с текстом')
        return False

    panel, anchor = _panel_anchor(header, text_el)
    if panel is None:
        _log_once('no_panel', u'Не нашёл Panel над текстом (%s)'
                  % text_el.GetType().Name)
        return False

    if _panel_has_logo(panel):
        if panel not in _PANELS:
            _PANELS.append(panel)
        return True

    img = _make_image(logo_path)

    if isinstance(panel, Grid):
        # в Grid «слева» не вставить — кладём в ту же ячейку,
        # текст сдвигаем вправо
        try:
            Grid.SetColumn(img, Grid.GetColumn(anchor))
            Grid.SetRow(img, Grid.GetRow(anchor))
            try:
                Grid.SetColumnSpan(img, Grid.GetColumnSpan(anchor))
            except Exception:
                pass
        except Exception:
            pass
        img.HorizontalAlignment = HorizontalAlignment.Left
        try:
            shift = LOGO_SIZE * (float(img.Source.PixelWidth) /
                                 float(img.Source.PixelHeight))
        except Exception:
            shift = LOGO_SIZE
        m = anchor.Margin
        anchor.Margin = Thickness(m.Left + shift + GAP, m.Top,
                                  m.Right, m.Bottom)
        img.Margin = Thickness(m.Left, 0, 0, 0)
        panel.Children.Add(img)
    else:
        # StackPanel / DockPanel / прочее — вставляем перед текстом
        img.Margin = Thickness(0, 0, GAP, 0)
        idx = panel.Children.IndexOf(anchor)
        if idx < 0:
            idx = 0
        panel.Children.Insert(idx, img)

    if panel not in _PANELS:
        _PANELS.append(panel)

    _log_once('inject_%s' % text_el.Text,
              u'Логотип поставлен на вкладку «%s» (вставка в %s)'
              % (text_el.Text, panel.GetType().Name))

    try:
        panel.InvalidateMeasure()
        header.InvalidateMeasure()
    except Exception:
        pass
    return True


def _reorder_tabs(ribbon):
    """Расставить наши вкладки в порядке TAB_ORDER. Один раз за сессию."""
    if _DIAG.get('reordered'):
        return
    try:
        tabs = list(ribbon.Tabs)
        by_title = {}
        for tab in tabs:
            by_title[_norm(tab.Title)] = tab

        ours = []
        for name in TAB_ORDER:
            tab = by_title.get(_norm(name))
            if tab is not None:
                ours.append(tab)
        if len(ours) < 2:
            return  # нечего переставлять, ждём следующий тик

        current = [tab for tab in tabs if tab in ours]
        if current == ours:
            _DIAG['reordered'] = True
            _log(u'Порядок вкладок уже верный')
            return

        # вставляем на позицию самой левой из наших
        pos = min([ribbon.Tabs.IndexOf(tab) for tab in ours])
        for tab in ours:
            ribbon.Tabs.Remove(tab)
        for i, tab in enumerate(ours):
            ribbon.Tabs.Insert(pos + i, tab)

        _DIAG['reordered'] = True
        _log(u'Вкладки переставлены: %s'
             % u' → '.join([tab.Title for tab in ours]))
    except Exception as exc:
        _DIAG['reordered'] = True   # второй раз не лезем
        _log(u'Не смог переставить вкладки: %s' % exc)


def _apply():
    """Ставим логотипы и порядок. True — сейчас всё на месте."""
    ribbon = ComponentManager.Ribbon
    if ribbon is None:
        return False

    # сколько вкладок вообще должно получить логотип
    wanted = []
    try:
        for tab in ribbon.Tabs:
            t = tab.Title or u''
            if _wanted_title(t):
                wanted.append(t)
    except Exception as exc:
        _log_once('tabs_error', u'Не смог перебрать вкладки: %s' % exc)
        return False

    if not wanted:
        _log_once('no_tabs',
                  u'Не нашёл вкладок с заголовками: %s' % u', '.join(TAB_TITLES))
        return False

    # быстрый путь: всё на месте — полный обход дерева не запускаем
    if _DIAG.get('reordered') and _fast_check(ribbon, len(wanted)):
        return True

    logo_path = _DIAG.get('logo_path')
    if logo_path is None:
        logo_path = os.path.join(_EXT_DIR, LOGO_FILE)
        if not os.path.isfile(logo_path):
            _log_once('no_file', u'Файл логотипа не найден: %s' % logo_path)
            return True  # смысла повторять нет
        _DIAG['logo_path'] = logo_path

    # порядок правим до вставки картинок: перестановка пересоздаёт заголовки
    _reorder_tabs(ribbon)

    del _PANELS[:]   # заголовки могли пересоздаться, кеш неактуален
    done = 0
    seen = 0
    for header in _walk(ribbon):
        if not _is_tab_header(header):
            continue
        title = _title_of(header)
        if not _wanted_title(title):
            continue
        seen += 1
        if _inject(header, logo_path):
            done += 1

    if seen == 0 and not _DIAG.get('dumped'):
        _DIAG['dumped'] = True
        _log(u'Нужные заголовки не опознаны. Вкладки ленты: %s'
             % u', '.join(wanted))
        found = []
        for node in _walk(ribbon):
            if _is_tab_header(node):
                found.append(u'%s → title=%s'
                             % (node.GetType().Name, _title_of(node)))
        _log(u'Кандидаты в заголовки (%d): %s'
             % (len(found), u' | '.join(found[:30])))

    return done >= len(wanted) and seen >= len(wanted)


# ------------------------------------------------------------------ запуск
def _bootstrap():
    """Тикаем таймером на UI-потоке, пока лента не построится."""
    if not _READY or not _EXT_DIR:
        _log(u'Стартовать нечем: _READY=%s, _EXT_DIR=%s' % (_READY, _EXT_DIR))
        return

    timer = DispatcherTimer(DispatcherPriority.Background,
                            Dispatcher.CurrentDispatcher)
    timer.Interval = TimeSpan.FromMilliseconds(TICK_MS)

    state = {'tries': 0, 'ever_ok': False}

    def on_tick(sender, args):
        state['tries'] += 1
        ok = False
        try:
            ok = _apply()
        except Exception as exc:
            _log_once('apply_error', u'Ошибка при установке логотипа: %s' % exc)
        if ok:
            state['ever_ok'] = True
        # таймер не выключаем: Revit пересоздаёт заголовки вкладок при
        # переключении, картинку приходится ставить заново
        if not state['ever_ok'] and state['tries'] >= MAX_TRIES:
            try:
                timer.Stop()
            except Exception:
                pass
            _log(u'Сдался после %d попыток' % state['tries'])

    timer.Tick += on_tick
    timer.Start()
    # держим ссылку, иначе таймер соберёт GC
    globals()['_PP_TAB_LOGO_TIMER'] = timer
    _log(u'Таймер запущен, интервал %d мс' % TICK_MS)


try:
    _bootstrap()
except Exception as exc:
    _log(u'Фатальная ошибка startup: %s' % exc)

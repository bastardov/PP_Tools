# -*- coding: utf-8 -*-
u"""Окно инструмента «Инструменты меток» — редактор шаблона правил маркировки.

Два режима на одном окне:

  «По семейству и типу» — марка привязана к семейству или типу элемента.
  «По размеру»          — марка подбирается по длине надписи (воздуховоды,
                          трубы, изоляция), строки собраны в именованные
                          пресеты.

Окно не обращается к Revit: данные проекта приходят двумя функциями, которые
передаёт script.py. Работа с таблицами правил идёт через pp_tagrules — это
модель правил, а не API Revit.

    from pp_tagtools_window import ask_tag_rules

    result = ask_tag_rules(rules, categories, load_marks, load_types,
                           size_presets, size_active)

    rules         — рабочая копия списка правил семейств/типов (окно мутирует)
    categories    — список enum-имён категорий в порядке показа
    load_marks(cat_enum) -> [(tag_family, tag_type), ...]
    load_types(cat_enum) -> {family: [type_name, ...]}
    size_presets  — рабочая копия списка пресетов размерных правил
    size_active   — имя активного пресета

    Возврат при «Сохранить»:
        {"rules": [...], "presets": [...], "active": u"имя"}
    при отмене — None.
"""

import os
import json

from datetime import datetime

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF
import pp_tagrules as tr

from System.Windows import Application, TextWrapping, TextTrimming, Visibility
from System.Windows.Controls import TextBlock, TreeViewItem
from System.Windows.Documents import Run
from System.Windows.Input import Key, Keyboard

from Microsoft.Win32 import SaveFileDialog, OpenFileDialog


def style(key):
    try:
        return Application.Current.Resources[key]
    except Exception:
        return None


def brush(key):
    return style(key)


# ======================================================================
#  Подписи размерных правил
# ======================================================================

def _number(count):
    u"""Круглое число из заданного числа знаков: 3 -> «100»."""
    return u"1" + u"0" * (count - 1) if count > 0 else u""


# Ходовые сечения — чтобы пример выглядел как настоящая марка, а не как
# «10000х1000». Ключ — длина строки в знаках.
_RECT_SIZES = {
    5: u"50х50",
    6: u"100х50",
    7: u"100х100",
    8: u"1000х500",
    9: u"1000х1000",
}

_ROUND_SIZES = {
    3: u"ø90",
    4: u"ø400",
    5: u"ø1000",
}

# Имена систем ровно нужной длины, тоже из реальной практики.
_SYSTEM_NAMES = {
    2: u"В1",
    3: u"ДВ1",
    4: u"ДВ10",
    5: u"ДВ 10",
    6: u"ДВ 100",
    7: u"ПРИТОК1",
    8: u"ПРИТОК 1",
    9: u"ПРИТОК 10",
    10: u"ПРИТОК 100",
    11: u"ВЫТЯЖКА 100",
    12: u"ДЫМОУДАЛЕНИЕ",
    13: u"ДЫМОУДАЛЕНИЕ1",
    14: u"ДЫМОУДАЛЕНИЕ 1",
    15: u"ДЫМОУДАЛЕНИЕ 10",
    16: u"ДЫМОУДАЛЕНИЕ 100",
}


def _rect_size(length):
    u"""«100х100» ровно такой длины (знак «х» тоже считается)."""
    if length < 3:
        return None

    if length in _RECT_SIZES:
        return _RECT_SIZES[length]

    digits = length - 1
    left = digits // 2

    return u"{}х{}".format(_number(digits - left), _number(left))


def _round_size(length):
    u"""«ø400» ровно такой длины."""
    if length < 2:
        return None

    if length in _ROUND_SIZES:
        return _ROUND_SIZES[length]

    return u"ø" + _number(length - 1)


def _system_name(length):
    u"""Имя системы ровно такой длины: «В1», «ДВ 10», «ДЫМОУДАЛЕНИЕ 100»."""
    if length < 2:
        return None

    if length in _SYSTEM_NAMES:
        return _SYSTEM_NAMES[length]

    return u"ДЫМОУДАЛЕНИЕ " + _number(length - 13)


# Правдоподобные длины размерной части в примере «имя системы, размер»:
# сначала ходовые прямоугольные сечения, в конце круглое.
_SIZE_SAMPLE_ORDER = (8, 9, 7, 4, 6, 5)


def length_example(length):
    u"""Правдоподобная надпись ровно такой длины.

    До 9 знаков марка показывает только размер («ø400», «100х100»,
    «1000х1000»), дальше в неё уже не влезает один размер — значит, там имя
    системы вместе с размером («ДВ 10, 1000х1000»). Пример строится под этот
    расклад, чтобы диапазон было с чем сопоставить глазами.
    """
    if not length or length < 3:
        return None

    if length <= 4:
        return _round_size(length)

    if length <= 9:
        return _rect_size(length)

    for size_length in _SIZE_SAMPLE_ORDER:
        name_length = length - 2 - size_length  # два знака на «, »

        if name_length < 2:
            continue

        size = (_round_size(size_length) if size_length == 4
                else _rect_size(size_length))
        name = _system_name(name_length)

        if size and name:
            return u"{}, {}".format(name, size)

    return _rect_size(length)


def signs(count):
    u"""Слово «знак» в нужной форме: 1 знак, 2 знака, 5 знаков."""
    number = abs(int(count or 0))

    if number % 100 in (11, 12, 13, 14):
        return u"знаков"

    tail = number % 10

    if tail == 1:
        return u"знак"

    if tail in (2, 3, 4):
        return u"знака"

    return u"знаков"


def range_text(min_len, max_len):
    u"""Диапазон длины словами: «7–8 знаков», «от 9 знаков», «ровно 4 знака»."""
    low = min_len or 0

    if max_len is None:
        if low <= 1:
            return u"любая длина"

        return u"от {} {}".format(low, signs(low))

    if low >= max_len:
        return u"ровно {} {}".format(max_len, signs(max_len))

    return u"{}–{} {}".format(low, max_len, signs(max_len))


ALL_DUCTS = u"Все воздуховоды"


def scope_label(family, type_name):
    u"""Область действия строки словами."""
    if not family:
        return ALL_DUCTS

    if type_name:
        return u"{} : {}".format(family, type_name)

    return u"{} — всё семейство".format(family)


def size_rule_label(rule):
    u"""Строка правила для списка: область действия, диапазон, марка."""
    family, type_name = tr.size_rule_scope(rule)

    return u"{}   ·   {}   →   [{} : {}]".format(
        scope_label(family, type_name),
        range_text(rule.get("min_len"), rule.get("max_len")),
        rule.get("tag_family"),
        rule.get("tag_type")
    )


SIZE_RULE_KEYS = ("cat", "family", "type", "min_len", "max_len",
                  "tag_family", "tag_type")


def same_size_rule(one, other):
    u"""Две строки правил совпадают по всем значимым полям."""
    if one is None or other is None:
        return False

    for key in SIZE_RULE_KEYS:
        if one.get(key) != other.get(key):
            return False

    return True


# ======================================================================
#  ViewModel
# ======================================================================

class TagToolsVM(pp_wpf.Notifier):
    u"""Состояние окна: режим, что выделено, сколько правил, строка статуса."""

    def __init__(self, rules):
        pp_wpf.Notifier.__init__(self)

        self.rules = rules

        self._size_mode = False

        self._has_selection = False
        self._has_mark = False

        self._has_size_row = False
        self._size_valid = False
        self._size_dirty = False
        self._size_preview = u"Задайте категорию, длину надписи и марку."
        self._size_preview_ready = False
        self._coverage = u""
        self._coverage_gaps = False

        self._status = u"Выберите категорию, затем семейство или тип в дереве."
        self._status_error = False

    # -- режим --------------------------------------------------------
    @property
    def SizeMode(self):
        return self._size_mode

    @property
    def FamilyMode(self):
        return not self._size_mode

    @property
    def ModeHint(self):
        if self._size_mode:
            return (
                u"Марка подбирается по длине надписи: «100х100» — 7 знаков, "
                u"«1000х1000» — 9. Строку можно сузить до семейства или типа "
                u"воздуховода — тогда у круглых и прямоугольных будут свои "
                u"марки даже при одинаковой длине."
            )

        return (
            u"Шаблон задаёт, какой маркой маркировать семейство или отдельный "
            u"тип. Правило типа важнее правила семейства."
        )

    def set_size_mode(self, is_size):
        self._size_mode = bool(is_size)
        self.notify(u"SizeMode", u"FamilyMode", u"ModeHint")

    # -- свойства для биндингов ---------------------------------------
    @property
    def Status(self):
        return self._status

    @property
    def StatusBrush(self):
        key = u"Hot" if self._status_error else u"Muted"
        return brush(key)

    @property
    def HasSelection(self):
        return self._has_selection

    @property
    def CanAssign(self):
        return self._has_selection and self._has_mark

    @property
    def RulesInfo(self):
        count = len(self.rules)

        if not count:
            return u"Правил пока нет."

        return u"Правил в шаблоне: {}".format(count)

    # -- режим «по размеру» -------------------------------------------
    @property
    def HasSizeRow(self):
        return self._has_size_row

    @property
    def CanEditSize(self):
        return self._size_valid

    @property
    def CanChangeSizeRow(self):
        return self._has_size_row and self._size_valid

    @property
    def SizePreview(self):
        return self._size_preview

    @property
    def SizePreviewBrush(self):
        key = u"Ink" if self._size_preview_ready else u"Muted"
        return brush(key)

    @property
    def SizePreviewTitle(self):
        u"""Заголовок блока итога прямо говорит, что сделает кнопка."""
        if self._size_dirty:
            return u"ВЫДЕЛЕННАЯ СТРОКА СТАНЕТ ТАКОЙ"

        if self._has_size_row:
            return u"ВЫДЕЛЕННАЯ СТРОКА"

        return u"БУДЕТ ДОБАВЛЕНА СТРОКА"

    @property
    def SizeDirty(self):
        return self._size_dirty

    @property
    def Coverage(self):
        return self._coverage

    @property
    def CoverageBrush(self):
        key = u"Hot" if self._coverage_gaps else u"Muted"
        return brush(key)

    def set_coverage(self, text, has_gaps):
        self._coverage = text
        self._coverage_gaps = bool(has_gaps)
        self.notify(u"Coverage", u"CoverageBrush")

    def set_size_row(self, has_row):
        self._has_size_row = bool(has_row)
        self.notify(u"HasSizeRow", u"CanChangeSizeRow", u"SizePreviewTitle")

    def set_size_preview(self, text, is_ready, is_dirty=False):
        self._size_valid = bool(is_ready)
        self._size_preview = text
        self._size_preview_ready = bool(is_ready)
        self._size_dirty = bool(is_dirty)
        self.notify(u"SizePreview", u"SizePreviewBrush", u"SizePreviewTitle",
                    u"SizeDirty", u"CanEditSize", u"CanChangeSizeRow")

    # -- изменения из окна --------------------------------------------
    def set_selection(self, has_selection):
        self._has_selection = bool(has_selection)
        self.notify(u"HasSelection", u"CanAssign")

    def set_mark(self, has_mark):
        self._has_mark = bool(has_mark)
        self.notify(u"CanAssign")

    def rules_changed(self):
        self.notify(u"RulesInfo")

    def say(self, message):
        self._set_status(message, False)

    def set_error(self, message):
        self._set_status(message, True)

    def _set_status(self, message, is_error):
        self._status = message
        self._status_error = is_error
        self.notify(u"Status", u"StatusBrush")


# ======================================================================
#  Окно
# ======================================================================

class TagToolsWindow(object):

    def __init__(self, rules, categories, load_marks, load_types,
                 size_presets=None, size_active=None, state=None):
        xaml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            u"ui.xaml"
        )

        self.window = pp_wpf.load_window_file(xaml_path)
        self.vm = TagToolsVM(rules)
        self.window.DataContext = self.vm

        self.rules = rules
        self.categories = list(categories or [])
        self.load_marks = load_marks
        self.load_types = load_types

        self.cat = None
        self.marks = []          # [(tag_family, tag_type), ...] — по индексу CmbMark
        self.types = {}          # {family: [type_name, ...]}
        self.family_nodes = {}   # {family: TreeViewItem} — родитель по имени,
                                 # а не через .Parent: у элементов, добавленных
                                 # в Items напрямую, логический родитель ненадёжен
        self.accepted = False

        # Окно закрывается не только «Сохранить»/«Отмена»: кнопки «С плана»
        # закрывают его, чтобы дать выбрать марку в модели, и скрипт открывает
        # окно обратно с этим же состоянием.
        self.state = dict(state or {})
        self.pick_request = None

        # ── режим «по размеру» ──
        self.presets = tr.normalize_presets(size_presets)
        self.active_name = (tr.active_preset(self.presets, size_active)
                            or {}).get("name")
        # Размерные правила живут только у воздуховодов, категория одна.
        self.size_cat = tr.SIZE_ENUM_NAMES[0]
        self.size_scopes = []    # [(family|None, type|None), ...] — по индексу CmbSizeScope
        self.size_marks = []     # [(tag_family, tag_type), ...] — по индексу CmbSizeMark
        self.size_rows = []      # [правило, ...] — по индексу LstSizeRules
        self._marks_cache = {}   # {cat_enum: [(tag_family, tag_type), ...]}
        self._moved_note = None  # что подвинулось при последней записи строки

        self._filling = False

        self.lst_cat = self.window.FindName("LstCategories")
        self.tree = self.window.FindName("Tree")
        self.tree_empty = self.window.FindName("TxtTreeEmpty")
        self.search = self.window.FindName("TxtSearch")
        self.cmb_mark = self.window.FindName("CmbMark")

        self.lst_presets = self.window.FindName("LstPresets")
        self.txt_preset_name = self.window.FindName("TxtPresetName")
        self.lst_size_rules = self.window.FindName("LstSizeRules")
        self.size_empty = self.window.FindName("TxtSizeEmpty")
        self.cmb_size_scope = self.window.FindName("CmbSizeScope")
        self.txt_len_min = self.window.FindName("TxtLenMin")
        self.txt_len_max = self.window.FindName("TxtLenMax")
        self.cmb_size_mark = self.window.FindName("CmbSizeMark")
        self.btn_size_add = self.window.FindName("BtnSizeAdd")
        self.btn_size_apply = self.window.FindName("BtnSizeApply")

        self._fill_controls()
        self._wire()
        self._start()

        pp_wpf.set_owner(self.window)
        pp_wpf.fit_to_screen(self.window)

    # ---------- начальное заполнение -----------------------------------

    def _fill_controls(self):
        self._filling = True

        for enum_name in self.categories:
            self.lst_cat.Items.Add(tr.display_name(enum_name))

        self.window.FindName("RbReplace").IsChecked = True

        size_mode = self.state.get("mode") == u"size"

        self.window.FindName(
            "RbModeSize" if size_mode else "RbModeFamily").IsChecked = True

        self._filling = False

        # Подписок ещё нет, поэтому режим переносим в ViewModel руками.
        self.vm.set_size_mode(size_mode)

        # Список семейств и типов воздуховодов и список марок нужны до первого
        # предпросмотра: события их тоже не подтянут.
        self._fill_size_scopes((None, None))
        self._fill_size_marks(None)

        self._fill_presets(self.active_name)

    def _start(self):
        u"""Первый показ: выбрать категорию, довести дерево до нужного узла.

        Выделение категории ставится ПОСЛЕ подписок — иначе SelectionChanged
        уходит в пустоту и дерево остаётся пустым до первого щелчка."""
        if self.categories:
            cat = self.state.get("cat")

            index = (self.categories.index(cat)
                     if cat in self.categories else 0)

            if self.lst_cat.SelectedIndex == index:
                self._on_category()
            else:
                self.lst_cat.SelectedIndex = index

        focus = self.state.get("focus")

        if focus:
            self._focus_node(focus)

        message = self.state.get("message")

        if message:
            self.vm.say(message)

    def _focus_node(self, focus):
        u"""Показать в дереве узел, который только что записали с плана."""
        try:
            cat_enum, family, type_name = focus
        except:
            return

        if cat_enum != self.cat:
            return

        node = self.family_nodes.get(family)

        if node is None:
            return

        node.IsExpanded = True
        target = node

        if type_name:
            for child in node.Items:
                if child.Tag[2] == type_name:
                    target = child
                    break

        target.IsSelected = True

        try:
            target.BringIntoView()
        except:
            pass

    # ---------- подписки ------------------------------------------------

    def _wire(self):
        find = self.window.FindName
        guarded = pp_wpf.guard(self.vm.set_error)

        @guarded
        def on_category(sender, args):
            if self._filling:
                return
            self._on_category()

        @guarded
        def on_search(sender, args):
            self._build_tree()

        @guarded
        def on_node(sender, args):
            self._on_node()

        @guarded
        def on_mark(sender, args):
            self.vm.set_mark(self.cmb_mark.SelectedIndex >= 0)

        @guarded
        def on_assign(sender, args):
            self._assign()

        @guarded
        def on_clear(sender, args):
            self._clear()

        @guarded
        def on_export(sender, args):
            self._export()

        @guarded
        def on_import(sender, args):
            self._import()

        @guarded
        def on_save(sender, args):
            self._accept()

        @guarded
        def on_cancel(sender, args):
            self.window.Close()

        @guarded
        def on_pick_type(sender, args):
            self._pick(False)

        @guarded
        def on_pick_family(sender, args):
            self._pick(True)

        @guarded
        def on_mode(sender, args):
            if self._filling:
                return
            self._on_mode()

        @guarded
        def on_preset(sender, args):
            if self._filling:
                return
            self._on_preset()

        @guarded
        def on_preset_add(sender, args):
            self._preset_add()

        @guarded
        def on_preset_rename(sender, args):
            self._preset_rename()

        @guarded
        def on_preset_delete(sender, args):
            self._preset_delete()

        @guarded
        def on_size_scope(sender, args):
            if self._filling:
                return
            self._refresh_size_preview()

        @guarded
        def on_size_edit(sender, args):
            if self._filling:
                return
            self._refresh_size_preview()

        @guarded
        def on_size_row(sender, args):
            if self._filling:
                return
            self._on_size_row()

        @guarded
        def on_size_add(sender, args):
            self._size_add()

        @guarded
        def on_size_apply(sender, args):
            self._size_apply()

        @guarded
        def on_size_delete(sender, args):
            self._size_delete()

        self.lst_cat.SelectionChanged += on_category
        self.search.TextChanged += on_search
        self.tree.SelectedItemChanged += on_node
        self.cmb_mark.SelectionChanged += on_mark

        find("BtnAssign").Click += on_assign
        find("BtnClear").Click += on_clear
        find("BtnExport").Click += on_export
        find("BtnImport").Click += on_import
        find("BtnSave").Click += on_save
        find("BtnCancel").Click += on_cancel

        find("BtnPickType").Click += on_pick_type
        find("BtnPickFamily").Click += on_pick_family

        find("RbModeFamily").Checked += on_mode
        find("RbModeSize").Checked += on_mode

        self.lst_presets.SelectionChanged += on_preset
        find("BtnPresetAdd").Click += on_preset_add
        find("BtnPresetRename").Click += on_preset_rename
        find("BtnPresetDelete").Click += on_preset_delete

        self.cmb_size_scope.SelectionChanged += on_size_scope
        self.txt_len_min.TextChanged += on_size_edit
        self.txt_len_max.TextChanged += on_size_edit
        self.cmb_size_mark.SelectionChanged += on_size_edit
        self.lst_size_rules.SelectionChanged += on_size_row

        find("BtnSizeAdd").Click += on_size_add
        find("BtnSizeApply").Click += on_size_apply
        find("BtnSizeDelete").Click += on_size_delete

        # Свой PreviewKeyDown ДО pp_wpf.wire_keys: обработчики окна вызываются
        # в порядке подписки, и Handled=True останавливает следующий. Иначе
        # Enter в полях редактора закрывал бы окно вместо записи строки.
        self.window.PreviewKeyDown += self._on_key

        pp_wpf.wire_keys(
            self.window,
            on_accept=self._accept,
            on_cancel=self.window.Close
        )

    def _on_key(self, sender, args):
        u"""Enter внутри редактора размерных правил = записать строку."""
        try:
            if args.Key != Key.Enter:
                return

            focused = Keyboard.FocusedElement

            if focused is self.txt_preset_name:
                args.Handled = True
                self.vm.say(
                    u"Нажмите «Создать» — новый пресет, или «Переименовать» — "
                    u"дать это имя выделенному."
                )
                return

            editors = (self.txt_len_min, self.txt_len_max, self.cmb_size_mark,
                       self.cmb_size_scope, self.lst_size_rules)

            # Сравнение через is: у контролов .NET нет своего ==, а `in`
            # опирается именно на него.
            if any(focused is control for control in editors):
                args.Handled = True

                if self._selected_size_rule() is not None:
                    self._size_apply()
                else:
                    self._size_add()

        except Exception as ex:
            self.vm.set_error(u"Ошибка: {}".format(unicode(ex)))

    # ---------- категория ------------------------------------------------

    def _marks_for(self, cat_enum):
        u"""Марки категории с запоминанием: список нужен обоим режимам."""
        if cat_enum not in self._marks_cache:
            self._marks_cache[cat_enum] = list(self.load_marks(cat_enum) or [])

        return self._marks_cache[cat_enum]

    def _on_category(self):
        index = self.lst_cat.SelectedIndex

        if index < 0 or index >= len(self.categories):
            return

        self.cat = self.categories[index]
        self.marks = list(self._marks_for(self.cat))
        self.types = dict(self.load_types(self.cat) or {})

        self._filling = True
        self.cmb_mark.Items.Clear()

        for tag_family, tag_type in self.marks:
            self.cmb_mark.Items.Add(u"{} : {}".format(tag_family, tag_type))

        self.cmb_mark.SelectedIndex = -1
        self._filling = False

        self.vm.set_mark(False)
        self._build_tree()

        if not self.marks:
            self.vm.set_error(
                u"В проект не загружено ни одной марки категории «{}».".format(
                    tr.display_name(self.cat))
            )
        else:
            self.vm.say(
                u"Категория «{}»: семейств {}, марок {}.".format(
                    tr.display_name(self.cat),
                    len(self.types),
                    len(self.marks)
                )
            )

    # ---------- дерево ---------------------------------------------------

    def _assignment_note(self, family, type_name):
        u"""Подпись справа от имени узла и ключ цвета для неё."""
        own = tr.get_assignment(self.rules, self.cat, family, type_name)

        if own:
            return (
                u"   →   [{} : {}]".format(own.get("tag_family"),
                                           own.get("tag_type")),
                u"Accent"
            )

        if type_name:
            inherited = tr.get_assignment(self.rules, self.cat, family, None)

            if inherited:
                return (
                    u"   →   (от семейства) [{} : {}]".format(
                        inherited.get("tag_family"), inherited.get("tag_type")),
                    u"Muted"
                )

        return (None, None)

    def _header(self, name, family, type_name):
        block = TextBlock()
        block.Style = style(u"Body")
        block.TextWrapping = TextWrapping.NoWrap
        block.TextTrimming = TextTrimming.CharacterEllipsis
        block.Inlines.Add(Run(name))

        note, color_key = self._assignment_note(family, type_name)

        if note:
            run = Run(note)
            run.Foreground = brush(color_key)
            block.Inlines.Add(run)

        return block

    def _build_tree(self):
        self.tree.Items.Clear()
        self.family_nodes = {}

        query = (self.search.Text or u"").strip().lower()
        shown = 0

        for family in sorted(self.types.keys()):
            type_names = sorted(self.types[family])

            if query:
                family_hit = query in family.lower()
                hits = [t for t in type_names if query in t.lower()]

                if not family_hit and not hits:
                    continue

                type_names = type_names if family_hit else hits

            node = TreeViewItem()
            node.Tag = ("fam", family, None)
            node.Header = self._header(family, family, None)

            for type_name in type_names:
                child = TreeViewItem()
                child.Tag = ("type", family, type_name)
                child.Header = self._header(type_name, family, type_name)
                node.Items.Add(child)

            if query:
                node.IsExpanded = True

            self.tree.Items.Add(node)
            self.family_nodes[family] = node
            shown += 1

        self.vm.set_selection(False)

        if shown:
            self.tree_empty.Visibility = Visibility.Collapsed
        else:
            self.tree_empty.Visibility = Visibility.Visible

            if self.cat is None:
                self.tree_empty.Text = u"Выберите категорию слева."
            elif query:
                self.tree_empty.Text = u"Ничего не найдено. Измените запрос."
            else:
                self.tree_empty.Text = (
                    u"В проект не загружено ни одного семейства этой категории."
                )

    def _selected_node(self):
        node = self.tree.SelectedItem

        if isinstance(node, TreeViewItem):
            return node

        return None

    def _refresh_family(self, family):
        u"""Перерисовать подписи семейства и всех его типов."""
        node = self.family_nodes.get(family)

        if node is None:
            return

        node.Header = self._header(family, family, None)

        for child in node.Items:
            type_name = child.Tag[2]
            child.Header = self._header(type_name, family, type_name)

    # ---------- обработчики режима «по семейству и типу» ------------------

    def _on_node(self):
        node = self._selected_node()

        self.vm.set_selection(node is not None)

        if node is None:
            return

        kind, family, type_name = node.Tag

        rule = tr.get_assignment(self.rules, self.cat, family, type_name)

        self._filling = True
        self.cmb_mark.SelectedIndex = -1

        if rule:
            key = (rule.get("tag_family"), rule.get("tag_type"))

            if key in self.marks:
                self.cmb_mark.SelectedIndex = self.marks.index(key)

        self._filling = False
        self.vm.set_mark(self.cmb_mark.SelectedIndex >= 0)

    def _assign(self):
        node = self._selected_node()

        if node is None:
            self.vm.set_error(u"Выделите семейство или тип в дереве.")
            return

        if self.cmb_mark.SelectedIndex < 0:
            self.vm.set_error(u"Выберите марку в списке выше.")
            return

        tag_family, tag_type = self.marks[self.cmb_mark.SelectedIndex]
        kind, family, type_name = node.Tag

        tr.set_assignment(self.rules, self.cat, family, type_name,
                          tag_family, tag_type)

        self._refresh_family(family)
        self.vm.rules_changed()

        target = type_name if type_name else family

        self.vm.say(
            u"Назначено «{}» → [{} : {}]. Отмена вернёт всё как было.".format(
                target, tag_family, tag_type)
        )

    def _clear(self):
        node = self._selected_node()

        if node is None:
            self.vm.set_error(u"Выделите семейство или тип в дереве.")
            return

        kind, family, type_name = node.Tag

        if tr.get_assignment(self.rules, self.cat, family, type_name) is None:
            self.vm.say(u"У этого узла своего правила нет — очищать нечего.")
            return

        tr.remove_assignment(self.rules, self.cat, family, type_name)

        self._refresh_family(family)
        self.vm.rules_changed()
        self._on_node()

        target = type_name if type_name else family

        self.vm.say(
            u"Правило «{}» убрано. Отмена вернёт всё как было.".format(target)
        )

    # ---------- переключение режима ---------------------------------------

    def _on_mode(self):
        is_size = bool(self.window.FindName("RbModeSize").IsChecked)

        self.vm.set_size_mode(is_size)

        if is_size:
            self.vm.say(
                u"Пресет «{}»: строк {}.".format(
                    self.active_name or u"—", len(self.size_rows))
            )
        else:
            self.vm.say(u"Выберите категорию, затем семейство или тип в дереве.")

    # ---------- пресеты ---------------------------------------------------

    def _current_preset(self):
        return tr.active_preset(self.presets, self.active_name)

    def _fill_presets(self, select_name):
        u"""Перестроить список пресетов и выделить нужный."""
        self._filling = True
        self.lst_presets.Items.Clear()

        for preset in self.presets:
            self.lst_presets.Items.Add(preset.get("name"))

        index = 0

        for i, preset in enumerate(self.presets):
            if preset.get("name") == select_name:
                index = i
                break

        self.lst_presets.SelectedIndex = index
        self._filling = False

        self.active_name = self.presets[index].get("name")
        self.txt_preset_name.Text = self.active_name

        self._build_size_rules()

    def _on_preset(self):
        index = self.lst_presets.SelectedIndex

        if index < 0 or index >= len(self.presets):
            return

        self.active_name = self.presets[index].get("name")
        self.txt_preset_name.Text = self.active_name

        self._build_size_rules()

        self.vm.say(
            u"Активный пресет — «{}»: строк {}.".format(
                self.active_name, len(self.size_rows))
        )

    def _preset_name_input(self):
        return (self.txt_preset_name.Text or u"").strip()

    def _preset_add(self):
        name = self._preset_name_input()

        if not name:
            self.vm.set_error(u"Введите имя пресета в поле над кнопками.")
            return

        if tr.find_preset(self.presets, name) is not None:
            self.vm.set_error(u"Пресет «{}» уже есть.".format(name))
            return

        self.presets.append({"name": name, "rules": []})
        self._fill_presets(name)

        self.vm.say(u"Создан пресет «{}». Добавьте строки справа.".format(name))

    def _preset_rename(self):
        preset = self._current_preset()

        if preset is None:
            self.vm.set_error(u"Выделите пресет в списке.")
            return

        name = self._preset_name_input()

        if not name:
            self.vm.set_error(u"Введите новое имя в поле над кнопками.")
            return

        if name == preset.get("name"):
            self.vm.say(u"Имя не изменилось.")
            return

        if tr.find_preset(self.presets, name) is not None:
            self.vm.set_error(u"Пресет «{}» уже есть.".format(name))
            return

        old = preset.get("name")
        preset["name"] = name

        self._fill_presets(name)

        self.vm.say(
            u"Пресет «{}» переименован в «{}». Отмена вернёт всё как было."
            .format(old, name)
        )

    def _preset_delete(self):
        preset = self._current_preset()

        if preset is None:
            self.vm.set_error(u"Выделите пресет в списке.")
            return

        name = preset.get("name")
        self.presets.remove(preset)

        if not self.presets:
            self.presets.append({"name": tr.DEFAULT_PRESET_NAME, "rules": []})

        self._fill_presets(self.presets[0].get("name"))

        self.vm.say(
            u"Пресет «{}» удалён. Отмена вернёт всё как было.".format(name)
        )

    # ---------- строки размерных правил -----------------------------------

    def _build_size_rules(self):
        u"""Перестроить список строк активного пресета."""
        preset = self._current_preset()
        rules = list((preset or {}).get("rules") or [])

        self._filling = True
        self.lst_size_rules.Items.Clear()
        self.size_rows = []

        for rule in rules:
            self.lst_size_rules.Items.Add(size_rule_label(rule))
            self.size_rows.append(rule)

        self.lst_size_rules.SelectedIndex = -1
        self._filling = False

        self.vm.set_size_row(False)

        if self.size_rows:
            self.size_empty.Visibility = Visibility.Collapsed
        else:
            self.size_empty.Visibility = Visibility.Visible

        self._refresh_size_preview()

    def _fill_size_scopes(self, select_scope):
        u"""Список «на что действует строка»: все воздуховоды, семейство
        целиком, отдельный тип. select_scope — (семейство, тип)."""
        grouped = dict(self.load_types(self.size_cat) or {})

        self.size_scopes = [(None, None)]
        labels = [ALL_DUCTS]

        for family in sorted(grouped.keys()):
            self.size_scopes.append((family, None))
            labels.append(scope_label(family, None))

            for type_name in sorted(grouped[family]):
                self.size_scopes.append((family, type_name))
                labels.append(u"      {}".format(type_name))

        self._filling = True
        self.cmb_size_scope.Items.Clear()

        for text in labels:
            self.cmb_size_scope.Items.Add(text)

        scope = select_scope or (None, None)

        if scope in self.size_scopes:
            self.cmb_size_scope.SelectedIndex = self.size_scopes.index(scope)
        else:
            self.cmb_size_scope.SelectedIndex = 0

        self._filling = False

    def _size_scope(self):
        u"""Выбранная область действия: (семейство|None, тип|None)."""
        index = self.cmb_size_scope.SelectedIndex

        if 0 <= index < len(self.size_scopes):
            return self.size_scopes[index]

        return (None, None)

    def _fill_size_marks(self, select_key):
        u"""Список марок воздуховодов; select_key — (семейство, тип) марки."""
        self.size_marks = list(self._marks_for(self.size_cat))

        self._filling = True
        self.cmb_size_mark.Items.Clear()

        for tag_family, tag_type in self.size_marks:
            self.cmb_size_mark.Items.Add(u"{} : {}".format(tag_family, tag_type))

        if select_key and select_key in self.size_marks:
            self.cmb_size_mark.SelectedIndex = self.size_marks.index(select_key)
        else:
            self.cmb_size_mark.SelectedIndex = -1

        self._filling = False

    def _read_length(self, box, default):
        u"""Число из поля длины. Возврат: (значение, текст ошибки)."""
        text = (box.Text or u"").strip()

        if not text:
            return (default, None)

        try:
            value = int(text)
        except:
            return (None,
                    u"Длина надписи задаётся числом знаков: «{}» — не "
                    u"число.".format(text))

        if value < 1:
            return (None, u"Длина надписи не может быть меньше одного знака.")

        return (value, None)

    def _editor_rule(self):
        u"""Собрать строку из полей редактора. Возврат: (правило, ошибка)."""
        cat_enum = self.size_cat
        family, type_name = self._size_scope()

        min_len, error = self._read_length(self.txt_len_min, 1)

        if error:
            return (None, error)

        max_len, error = self._read_length(self.txt_len_max, None)

        if error:
            return (None, error)

        if max_len is not None and max_len < min_len:
            return (None,
                    u"Верхняя граница меньше нижней: «до» должно быть не "
                    u"меньше «от».")

        index = self.cmb_size_mark.SelectedIndex

        if index < 0 or index >= len(self.size_marks):
            if not self.size_marks:
                return (None,
                        u"В проект не загружено ни одной марки категории "
                        u"«{}».".format(tr.display_name(cat_enum)))

            return (None, u"Выберите марку.")

        tag_family, tag_type = self.size_marks[index]

        return ({
            "cat": cat_enum,
            "family": family,
            "type": type_name,
            "min_len": min_len,
            "max_len": max_len,
            "tag_family": tag_family,
            "tag_type": tag_type,
        }, None)

    def _update_size_buttons(self):
        u"""Основной кнопкой становится та, которая нужна прямо сейчас: при
        выделенной строке — «Изменить строку», иначе — «Добавить строку»."""
        row_selected = self._selected_size_rule() is not None

        self.btn_size_add.Style = style(
            u"Button.Flat" if row_selected else u"Button.Primary")
        self.btn_size_apply.Style = style(
            u"Button.Primary" if row_selected else u"Button.Flat")

    def _refresh_coverage(self):
        u"""Что из длин надписи покрыто строками для выбранной области."""
        family, type_name = self._size_scope()

        preset = self._current_preset()
        rules = (preset or {}).get("rules") or []
        title = scope_label(family, type_name)

        if not tr.rules_in_scope(rules, self.size_cat, family, type_name):
            self.vm.set_coverage(
                u"«{}»: подходящих строк нет — такие марки инструмент не "
                u"тронет.".format(title), True)
            return

        gaps = tr.size_coverage_gaps(rules, self.size_cat, family, type_name)

        if not gaps:
            self.vm.set_coverage(
                u"«{}»: покрыты все длины надписи.".format(title), False)
            return

        parts = []

        for low, high in gaps:
            if high is None:
                parts.append(u"{} и длиннее".format(low))
            elif low >= high:
                parts.append(u"{}".format(low))
            else:
                parts.append(u"{}–{}".format(low, high))

        self.vm.set_coverage(
            u"«{}»: не покрыты надписи длиной {}. Такие марки инструмент "
            u"пропустит.".format(title, u", ".join(parts)), True)

    def _refresh_size_preview(self):
        rule, error = self._editor_rule()
        current = self._selected_size_rule()

        self._update_size_buttons()
        self._refresh_coverage()

        if rule is None:
            self.vm.set_size_preview(error, False)
            return

        dirty = current is not None and not same_size_rule(rule, current)

        sample = length_example(rule.get("min_len"))

        if sample is None:
            sample = length_example(rule.get("max_len"))

        text = size_rule_label(rule)

        if sample:
            text = u"{}\nнапример надпись «{}»".format(text, sample)

        if dirty:
            text = u"{}\nПравка ещё не записана — нажмите «Изменить строку».".format(text)

        self.vm.set_size_preview(text, True, dirty)

        if dirty:
            self.vm.say(
                u"Поля справа отличаются от выделенной строки. Нажмите "
                u"«Изменить строку» (или Enter), чтобы записать правку."
            )

    def _selected_size_rule(self):
        index = self.lst_size_rules.SelectedIndex

        if 0 <= index < len(self.size_rows):
            return self.size_rows[index]

        return None

    def _on_size_row(self):
        rule = self._selected_size_rule()

        # Флаг ещё относится к прежней строке: поля пока не перезаписаны.
        was_dirty = self.vm.SizeDirty

        self.vm.set_size_row(rule is not None)

        if rule is None:
            return

        self._filling = True

        self.txt_len_min.Text = unicode(rule.get("min_len") or 1)

        max_len = rule.get("max_len")
        self.txt_len_max.Text = unicode(max_len) if max_len is not None else u""

        self._filling = False

        self._fill_size_scopes(tr.size_rule_scope(rule))
        self._fill_size_marks((rule.get("tag_family"), rule.get("tag_type")))
        self._refresh_size_preview()

        if was_dirty:
            self.vm.set_error(
                u"Правка прежней строки не записана — та строка осталась "
                u"как была."
            )

    def _make_room(self, rules, rule, replaced):
        u"""Освободить место под новый диапазон.

        Если соседняя строка начинается раньше, а заканчивается внутри нового
        диапазона (в том числе «и больше»), она просто уступает эстафету:
        её верхняя граница уезжает на знак ниже начала нового диапазона.
        Покрытие при этом не рвётся. Во всех остальных случаях — отказ, потому
        что подвинуть границу без потери покрытия нельзя.

        Возврат: (список подвинутых строк, текст ошибки).
        """
        low = rule.get("min_len") or 0
        high = rule.get("max_len")
        family, type_name = tr.size_rule_scope(rule)

        moved = []
        undo = []

        for _ in range(len(rules) + 1):
            clash = tr.find_size_overlap(rules, rule.get("cat"), low, high,
                                         ignore=replaced, family=family,
                                         type_name=type_name)

            if clash is None:
                return (moved, None)

            clash_low = clash.get("min_len") or 0
            clash_high = clash.get("max_len")

            takes_over = (clash_low < low
                          and (high is None
                               or (clash_high is not None
                                   and clash_high <= high)))

            if not takes_over:
                for row, old_high in undo:
                    row["max_len"] = old_high

                return ([], u"Диапазон пересекается со строкой «{}». Задайте "
                            u"той строке верхнюю границу или сузьте новую: "
                            u"диапазоны одной категории пересекаться не "
                            u"могут.".format(size_rule_label(clash)))

            undo.append((clash, clash_high))
            moved.append((clash, range_text(clash_low, clash_high)))
            clash["max_len"] = low - 1

        return (moved, None)

    def _store_size_rule(self, rule, replaced=None):
        u"""Записать строку в активный пресет; replaced — заменяемая строка."""
        preset = self._current_preset()

        if preset is None:
            self.vm.set_error(u"Сначала создайте пресет.")
            return False

        rules = preset.setdefault("rules", [])

        moved, error = self._make_room(rules, rule, replaced)

        if error:
            self.vm.set_error(error)
            return False

        self._moved_note = None

        if moved:
            parts = []

            for row, was in moved:
                family, type_name = tr.size_rule_scope(row)

                parts.append(u"«{}»: было {}, стало {}".format(
                    scope_label(family, type_name), was,
                    range_text(row.get("min_len"), row.get("max_len"))))

            self._moved_note = u" Подвинуты соседние строки — {}.".format(
                u"; ".join(parts))

        if replaced is not None and replaced in rules:
            rules[rules.index(replaced)] = rule
        else:
            rules.append(rule)

        tr.sort_size_rules(rules)

        self._build_size_rules()

        if rule in self.size_rows:
            self._filling = True
            self.lst_size_rules.SelectedIndex = self.size_rows.index(rule)
            self._filling = False
            self.vm.set_size_row(True)

            # Выделение поставлено кодом — SelectionChanged промолчал, поэтому
            # блок итога и кнопки пересчитываем руками.
            self._refresh_size_preview()

        return True

    def _size_add(self):
        rule, error = self._editor_rule()

        if rule is None:
            self.vm.set_error(error)
            return

        if self._store_size_rule(rule):
            self.vm.say(
                u"Строка добавлена: {}.{} Отмена вернёт всё как было.".format(
                    size_rule_label(rule), self._moved_note or u"")
            )

    def _size_apply(self):
        current = self._selected_size_rule()

        if current is None:
            self.vm.set_error(u"Выделите строку в списке слева.")
            return

        rule, error = self._editor_rule()

        if rule is None:
            self.vm.set_error(error)
            return

        if self._store_size_rule(rule, replaced=current):
            self.vm.say(
                u"Строка изменена: {}.{} Отмена вернёт всё как было.".format(
                    size_rule_label(rule), self._moved_note or u"")
            )

    def _size_delete(self):
        rule = self._selected_size_rule()

        if rule is None:
            self.vm.set_error(u"Выделите строку в списке слева.")
            return

        preset = self._current_preset()
        rules = (preset or {}).get("rules") or []

        if rule in rules:
            rules.remove(rule)

        self._build_size_rules()

        self.vm.say(
            u"Строка убрана: {}. Отмена вернёт всё как было.".format(
                size_rule_label(rule))
        )

    # ---------- экспорт и импорт -----------------------------------------

    def _export(self):
        dialog = SaveFileDialog()
        dialog.Title = u"Экспорт правил меток"
        dialog.Filter = u"JSON-файл (*.json)|*.json"
        dialog.FileName = u"PP_Tools_tag_rules_{}.json".format(
            datetime.now().strftime("%Y-%m-%d")
        )

        if dialog.ShowDialog() != True:
            return

        with open(dialog.FileName, "w") as f:
            json.dump({"rules": self.rules, "presets": self.presets},
                      f, indent=4)

        self.vm.say(u"Правила сохранены в {}".format(
            os.path.basename(dialog.FileName)))

    def _import(self):
        dialog = OpenFileDialog()
        dialog.Title = u"Импорт правил меток"
        dialog.Filter = u"JSON-файл (*.json)|*.json"

        if dialog.ShowDialog() != True:
            return

        with open(dialog.FileName, "r") as f:
            data = json.load(f)

        # Старые файлы — просто список правил, новые — словарь с двумя ключами.
        if isinstance(data, list):
            raw_rules = data
            raw_presets = []
        elif isinstance(data, dict):
            raw_rules = data.get("rules") or []
            raw_presets = data.get("presets") or []
        else:
            self.vm.set_error(u"Файл не содержит правил меток.")
            return

        imported = []

        for r in raw_rules:
            if isinstance(r, dict) and r.get("family"):
                imported.append({
                    "cat": r.get("cat"), "family": r.get("family"),
                    "type": r.get("type"), "tag_family": r.get("tag_family"),
                    "tag_type": r.get("tag_type"), "leader": bool(r.get("leader")),
                })

        presets = tr.normalize_presets(raw_presets) if raw_presets else []
        replace = bool(self.window.FindName("RbReplace").IsChecked)

        if replace:
            del self.rules[:]

        self.rules.extend(imported)

        added_presets = 0

        if presets:
            if replace:
                del self.presets[:]

            for preset in presets:
                name = preset.get("name")

                while tr.find_preset(self.presets, name) is not None:
                    name = name + u" (копия)"

                preset["name"] = name
                self.presets.append(preset)
                added_presets += 1

            self._fill_presets(self.presets[0].get("name"))

        if self.cat:
            self._build_tree()

        self.vm.rules_changed()

        mode = u"заменили текущие" if replace else u"добавлены к текущим"

        self.vm.say(
            u"Импортировано: правил {}, пресетов {} ({}). Отмена вернёт всё "
            u"как было.".format(len(imported), added_presets, mode)
        )

    # ---------- завершение ------------------------------------------------

    def _commit_pending_size_edit(self):
        u"""Записать правку полей в выделенную строку перед сохранением.

        Поля справа — редактор выделенной строки, и правка в них без нажатия
        «Изменить строку» раньше просто пропадала при «Сохранить». Теперь она
        применяется сама; если применить нельзя (пересечение диапазонов) —
        окно не закрывается, а пишет причину в статус.
        """
        current = self._selected_size_rule()

        if current is None:
            return True

        rule, error = self._editor_rule()

        if rule is None or same_size_rule(rule, current):
            return True

        if not self._store_size_rule(rule, replaced=current):
            return False

        self.vm.say(u"Строка изменена: {}".format(size_rule_label(rule)))

        return True

    def _pick(self, family_level):
        u"""Уйти на план за образцом.

        Выбрать элемент мышью при открытом модальном окне нельзя, поэтому окно
        закрывается, скрипт делает выбор и открывает его снова с тем же
        состоянием. Всё набранное уезжает вместе с окном и не теряется."""
        if not self._commit_pending_size_edit():
            return

        self.pick_request = u"family" if family_level else u"type"
        self.accepted = True
        self.window.DialogResult = True

    def _accept(self):
        if not self._commit_pending_size_edit():
            return

        self.accepted = True
        self.window.DialogResult = True

    def show(self):
        self.window.ShowDialog()

        if not self.accepted:
            return None

        return {
            "rules": self.rules,
            "presets": self.presets,
            "active": self.active_name,
            "pick": self.pick_request,
            "state": {
                "mode": u"size" if self.vm.SizeMode else u"family",
                "cat": self.cat,
                "preset": self.active_name,
            },
        }


def ask_tag_rules(rules, categories, load_marks, load_types,
                  size_presets=None, size_active=None, state=None):
    u"""Показать окно. Возврат: словарь с правилами, пресетами и состоянием
    либо None, если отменили.

    Ключ «pick» непустой — пользователь нажал «С плана»: окно закрылось ради
    выбора в модели, и его надо открыть снова, вернув «state»."""
    return TagToolsWindow(rules, categories, load_marks, load_types,
                          size_presets, size_active, state).show()

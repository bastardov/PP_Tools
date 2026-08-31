# -*- coding: utf-8 -*-
u"""Окно инструмента «Раздвинуть марки».

Окно модальное: собирает параметры, показывает предпросмотр и закрывается.
Про Revit не знает ничего — габариты марок уже замерены скриптом и приходят
списком словарей, а расчёт зовётся через функцию solve из состояния.

Благодаря этому предпросмотр («разъедется 39 марок из 47») считается на
каждое изменение параметров, не открывая ни одной транзакции. Ввод в полях
пересчитывается с задержкой: на плане с сотнями марок расчёт занимает доли
секунды, и дёргать его на каждую букву незачем.

Вызов:

    result = pp_spread_window.ask_options(state, saved)

state — словарь с ключами boxes, selected_ids, categories, ft_per_mm, scale,
solve. Возврат — словарь параметров плюс готовые moves, либо None, если
пользователь отказался.
"""

import os

import pp_wpf  # первым: добавляет clr-ссылки на сборки WPF

from System import TimeSpan
from System.Windows import Application, Thickness
from System.Windows.Controls import CheckBox
from System.Windows.Threading import DispatcherTimer


SCOPE_ALL = u"all"
SCOPE_SELECTED = u"selected"
SCOPE_CATEGORIES = u"categories"

MODE_AUTO = u"auto"
MODE_VERTICAL = u"vertical"
MODE_HORIZONTAL = u"horizontal"

GAP_MIN = 0.0
GAP_MAX = 20.0

OFFSET_MIN = 1.0
OFFSET_MAX = 300.0

LEADER_MIN = 0.0
LEADER_MAX = 100.0

DEFAULTS = {
    u"scope": SCOPE_ALL,
    u"mode": MODE_AUTO,
    u"gap_mm": 1.5,
    u"offset_mm": 20.0,
    u"leader_mm": 5.0,
    u"tangle": True,
    u"enable_leader": True,
    u"rebuild_elbow": True,
    u"categories": None,
}


def format_mm(value):
    u"""1.5 -> «1.5», 20.0 -> «20»."""
    try:
        number = float(value)

        if number == int(number):
            return unicode(int(number))

        return unicode(round(number, 2))
    except Exception:
        return unicode(value)


def parse_number(text):
    u"""Возвращает float или None. Запятая принимается как разделитель."""
    if text is None:
        return None

    text = unicode(text).strip().replace(u",", u".")

    if not text:
        return None

    try:
        return float(text)
    except ValueError:
        return None


def plural(count, one, few, many):
    u"""«1 марка», «3 марки», «17 марок»."""
    count = abs(int(count))

    if count % 100 in (11, 12, 13, 14):
        return many

    last = count % 10

    if last == 1:
        return one

    if last in (2, 3, 4):
        return few

    return many


def tags_word(count):
    return plural(count, u"марка", u"марки", u"марок")


# ======================================================================
#  ViewModel
# ======================================================================

class SpreadTagsVM(pp_wpf.Notifier):

    def __init__(self, state, saved):
        pp_wpf.Notifier.__init__(self)

        self._state = state
        self._boxes = state.get(u"boxes") or []
        self._selected = state.get(u"selected_ids") or set()
        self._all_categories = state.get(u"categories") or []
        self._scale = state.get(u"scale", 100)
        self._overlapping = state.get(u"overlapping", 0)
        self._crossing = state.get(u"crossing", 0)
        self._sizes = state.get(u"sizes")

        options = dict(DEFAULTS)

        if saved:
            options.update(saved)

        self._scope = options.get(u"scope")

        if self._scope == SCOPE_SELECTED and not self._selected:
            self._scope = SCOPE_ALL

        if self._scope not in (SCOPE_ALL, SCOPE_SELECTED, SCOPE_CATEGORIES):
            self._scope = SCOPE_ALL

        self._mode = options.get(u"mode")

        if self._mode not in (MODE_AUTO, MODE_VERTICAL, MODE_HORIZONTAL):
            self._mode = MODE_AUTO

        self._gap_text = format_mm(options.get(u"gap_mm"))
        self._offset_text = format_mm(options.get(u"offset_mm"))
        self._leader_text = format_mm(options.get(u"leader_mm"))

        self._tangle = bool(options.get(u"tangle"))
        self._enable_leader = bool(options.get(u"enable_leader"))
        self._rebuild_elbow = bool(options.get(u"rebuild_elbow"))

        self._checked = self._restore_categories(options.get(u"categories"))

        self._preview = None
        self._status = u""
        self._status_error = False

        self.revalidate()

    def _restore_categories(self, saved_keys):
        u"""Категории из прошлого запуска. Незнакомые молча отбрасываем:
        в другой модели их может не быть."""
        keys = set()

        for category in self._all_categories:
            keys.add(category[u"key"])

        if not saved_keys:
            return set(keys)

        restored = set()

        for key in saved_keys:
            try:
                key = int(key)
            except Exception:
                continue

            if key in keys:
                restored.add(key)

        return restored if restored else set(keys)

    # ---------- состояние, читаемое разметкой ----------------------

    @property
    def Intro(self):
        total = len(self._boxes)

        if not total:
            return u"На активном виде нет марок."

        beds = []

        if self._overlapping:
            beds.append(u"накладываются {}".format(self._overlapping))

        if self._crossing:
            beds.append(u"выноски перепутаны у {}".format(self._crossing))

        if not beds:
            return u"На активном виде {} {}, и с ними всё в порядке.".format(
                total, tags_word(total))

        return (
            u"На активном виде {} {}, из них {}. "
            u"Марки разъедутся на минимальное расстояние, остальные останутся на местах."
        ).format(total, tags_word(total), u" и ".join(beds))

    @property
    def SelectedLabel(self):
        if not self._selected:
            return u"Только выделенные"

        return u"Только выделенные ({})".format(len(self._selected))

    @property
    def HasSelection(self):
        return bool(self._selected)

    @property
    def IsScopeAll(self):
        return self._scope == SCOPE_ALL

    @property
    def IsScopeSelected(self):
        return self._scope == SCOPE_SELECTED

    @property
    def IsScopeCategories(self):
        return self._scope == SCOPE_CATEGORIES

    @property
    def IsAuto(self):
        return self._mode == MODE_AUTO

    @property
    def IsVertical(self):
        return self._mode == MODE_VERTICAL

    @property
    def IsHorizontal(self):
        return self._mode == MODE_HORIZONTAL

    @property
    def EnableLeader(self):
        return self._enable_leader

    @property
    def EnableTangle(self):
        return self._tangle

    @property
    def SizeHint(self):
        u"""Замеренный габарит марки. Без этой строки непонятно, почему марки
        разъезжаются именно так далеко."""
        if not self._sizes:
            return u""

        return (
            u"Замерено: марка в среднем {} \u00d7 {} мм на листе, "
            u"самая крупная {} \u00d7 {} мм. Если высота заметно больше видимого "
            u"текста, значит в семействе марки есть пустая строка или "
            u"подчёркивание \u2014 они тоже занимают место."
        ).format(
            format_mm(self._sizes.get(u"avg_w")),
            format_mm(self._sizes.get(u"avg_h")),
            format_mm(self._sizes.get(u"max_w")),
            format_mm(self._sizes.get(u"max_h"))
        )

    @property
    def GapHint(self):
        return self._model_hint(parse_number(self._gap_text))

    @property
    def OffsetHint(self):
        return self._model_hint(parse_number(self._offset_text))

    def _model_hint(self, value_mm):
        u"""Проектировщик считает по листу, Revit — по модели. Показываем оба."""
        if value_mm is None:
            return u""

        return u"в модели {} мм при масштабе 1:{}".format(
            format_mm(value_mm * self._scale), self._scale)

    @property
    def ResultText(self):
        if self._preview is None:
            return u"Проверьте значения полей"

        moved = self._preview.get(u"moved", 0)
        before = self._preview.get(u"tags_before", 0)

        if not before:
            return u"Раздвигать нечего"

        if not moved:
            return u"Ни одна марка не сдвинется"

        return u"Разъедется {} {} из {}".format(moved, tags_word(moved), before)

    @property
    def LeaderResult(self):
        u"""Отдельная строка про выноски: раздвижка и распутывание — разные
        беды, и мерить их одним числом нельзя."""
        if self._preview is None or not self._tangle:
            return u""

        before = self._preview.get(u"crossings_before", 0)
        after = self._preview.get(u"crossings_after", 0)
        through_before = self._preview.get(u"through_before", 0)
        through_after = self._preview.get(u"through_after", 0)

        parts = []

        if before:
            parts.append(u"пересечений выносок {} \u2192 {}".format(before, after))

        if through_before:
            parts.append(u"выносок сквозь чужой текст {} \u2192 {}".format(
                through_before, through_after))

        if not parts:
            return u"Выноски в порядке: ни пересечений, ни проходов сквозь чужой текст."

        return u"Выноски: " + u", ".join(parts) + u"."

    @property
    def ResultHint(self):
        if self._preview is None:
            return u"Пока значения не разобраны, посчитать результат нельзя."

        before = self._preview.get(u"tags_before", 0)
        after = self._preview.get(u"tags_after", 0)
        moved = self._preview.get(u"moved", 0)

        if not before:
            return u"Наложившихся марок в выбранной области нет."

        if not moved:
            return (
                u"Всё, что накладывается, попало в неподвижные: закреплено, "
                u"лежит в группе или не входит в выбранную область."
            )

        if not after:
            return u"Наложений не останется."

        return (
            u"Останется наложенных: {}. Им не хватило разрешённого смещения — "
            u"увеличьте максимальное смещение или разведите их вручную."
        ).format(after)

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
        if self._status_error:
            return False

        if self._preview is None:
            return False

        return bool(self._preview.get(u"moved"))

    # ---------- разобранные значения --------------------------------

    @property
    def gap_mm(self):
        return parse_number(self._gap_text)

    @property
    def offset_mm(self):
        return parse_number(self._offset_text)

    @property
    def leader_mm(self):
        return parse_number(self._leader_text)

    @property
    def mode(self):
        return self._mode

    @property
    def tangle(self):
        return self._tangle

    @property
    def scope(self):
        return self._scope

    @property
    def checked_categories(self):
        return set(self._checked)

    @property
    def numbers_ok(self):
        return not self._status_error

    # ---------- изменения из окна -----------------------------------

    def set_scope(self, scope):
        if scope == self._scope:
            return

        self._scope = scope
        self.notify(u"IsScopeAll", u"IsScopeSelected", u"IsScopeCategories")

    def set_mode(self, mode):
        if mode == self._mode:
            return

        self._mode = mode
        self.notify(u"IsAuto", u"IsVertical", u"IsHorizontal")

    def set_gap(self, text):
        self._gap_text = text
        self.notify(u"GapHint")
        self.revalidate()

    def set_offset(self, text):
        self._offset_text = text
        self.notify(u"OffsetHint")
        self.revalidate()

    def set_leader(self, text):
        self._leader_text = text
        self.revalidate()

    def set_tangle(self, value):
        self._tangle = bool(value)
        self.notify(u"EnableTangle")

    def set_enable_leader(self, value):
        self._enable_leader = bool(value)
        self.notify(u"EnableLeader")
        self.revalidate()

    def set_rebuild_elbow(self, value):
        self._rebuild_elbow = bool(value)

    def set_category(self, key, value):
        if value:
            self._checked.add(key)
        else:
            self._checked.discard(key)

    def set_preview(self, preview):
        self._preview = preview
        self.notify(u"ResultText", u"ResultHint", u"LeaderResult", u"IsValid")

    # ---------- проверка ввода ---------------------------------------

    def revalidate(self):
        error = self._first_error()

        if error:
            self._preview = None
            self._set_status(error, True)
            self.notify(u"ResultText", u"ResultHint", u"LeaderResult")
            return False

        self._set_status(u"", False)
        return True

    def _first_error(self):
        gap = self.gap_mm

        if gap is None or gap < GAP_MIN or gap > GAP_MAX:
            return u"Зазор между марками — число от {} до {} мм".format(
                format_mm(GAP_MIN), format_mm(GAP_MAX))

        offset = self.offset_mm

        if offset is None or offset < OFFSET_MIN or offset > OFFSET_MAX:
            return u"Максимальное смещение — число от {} до {} мм".format(
                format_mm(OFFSET_MIN), format_mm(OFFSET_MAX))

        if self._enable_leader:
            leader = self.leader_mm

            if leader is None or leader < LEADER_MIN or leader > LEADER_MAX:
                return u"Порог включения выноски — число от {} до {} мм".format(
                    format_mm(LEADER_MIN), format_mm(LEADER_MAX))

        return None

    def _set_status(self, text, is_error):
        self._status = text
        self._status_error = is_error
        self.notify(u"Status", u"StatusBrush", u"IsValid")

    # ---------- итог ---------------------------------------------------

    def result(self, moves):
        return {
            u"scope": self._scope,
            u"mode": self._mode,
            u"gap_mm": self.gap_mm,
            u"offset_mm": self.offset_mm,
            u"leader_mm": self.leader_mm,
            u"tangle": self._tangle,
            u"enable_leader": self._enable_leader,
            u"rebuild_elbow": self._rebuild_elbow,
            u"categories": sorted(self._checked),
            u"moves": moves,
            u"stats": dict(self._preview) if self._preview else {},
        }


# ======================================================================
#  Окно
# ======================================================================

class SpreadTagsWindow(object):

    def __init__(self, state, saved):
        xaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), u"ui.xaml")

        self.window = pp_wpf.load_window_file(xaml_path)
        self.state = state
        self.vm = SpreadTagsVM(state, saved)
        self.window.DataContext = self.vm

        self.accepted = False
        self._suppress = False
        self._last_moves = {}

        self._timer = DispatcherTimer()
        self._timer.Interval = TimeSpan.FromMilliseconds(260)

        self._fill_controls()
        self._wire()
        self._recompute()

        pp_wpf.set_owner(self.window)
        pp_wpf.fit_to_screen(self.window)

    # ---------- начальное состояние до подписки на события ----------

    def _fill_controls(self):
        find = self.window.FindName

        self._suppress = True

        try:
            find("ChipAll").IsChecked = self.vm.IsScopeAll
            find("ChipSelected").IsChecked = self.vm.IsScopeSelected
            find("ChipCategories").IsChecked = self.vm.IsScopeCategories

            find("ChipAuto").IsChecked = self.vm.IsAuto
            find("ChipVertical").IsChecked = self.vm.IsVertical
            find("ChipHorizontal").IsChecked = self.vm.IsHorizontal

            find("TxtGap").Text = self.vm._gap_text
            find("TxtOffset").Text = self.vm._offset_text
            find("TxtLeaderMm").Text = self.vm._leader_text

            find("ChkTangle").IsChecked = self.vm._tangle
            find("ChkLeader").IsChecked = self.vm._enable_leader
            find("ChkElbow").IsChecked = self.vm._rebuild_elbow

            self._build_categories()
        finally:
            self._suppress = False

    def _build_categories(self):
        u"""Список категорий строится кодом: он зависит от того, что на виде."""
        host = self.window.FindName("CategoriesHost")
        host.Children.Clear()

        checked = self.vm.checked_categories

        for category in self.state.get(u"categories") or []:
            box = CheckBox()

            try:
                box.Style = Application.Current.Resources[u"PP.CheckBox"]
            except Exception:
                pass

            box.Content = u"{} — {} шт.".format(
                category[u"name"], category[u"count"])
            box.Margin = Thickness(0, 3, 0, 3)
            box.IsChecked = category[u"key"] in checked

            handler = self._make_category_handler(category[u"key"])

            box.Checked += handler
            box.Unchecked += handler

            host.Children.Add(box)

    def _make_category_handler(self, key):
        u"""Фабрика: общий цикл с lambda отдал бы всем галочкам последний ключ."""

        @pp_wpf.guard(self._fail)
        def handler(sender, args):
            if self._suppress:
                return

            self.vm.set_category(key, bool(sender.IsChecked))
            self._recompute()

        return handler

    # ---------- подписки --------------------------------------------

    def _wire(self):
        find = self.window.FindName
        guard = pp_wpf.guard(self._fail)

        def make_scope_handler(scope):
            @guard
            def handler(sender, args):
                if self._suppress:
                    return

                self.vm.set_scope(scope)
                self._recompute()

            return handler

        find("ChipAll").Checked += make_scope_handler(SCOPE_ALL)
        find("ChipSelected").Checked += make_scope_handler(SCOPE_SELECTED)
        find("ChipCategories").Checked += make_scope_handler(SCOPE_CATEGORIES)

        def make_mode_handler(mode):
            @guard
            def handler(sender, args):
                if self._suppress:
                    return

                self.vm.set_mode(mode)
                self._recompute()

            return handler

        find("ChipAuto").Checked += make_mode_handler(MODE_AUTO)
        find("ChipVertical").Checked += make_mode_handler(MODE_VERTICAL)
        find("ChipHorizontal").Checked += make_mode_handler(MODE_HORIZONTAL)

        @guard
        def on_gap(sender, args):
            if self._suppress:
                return

            self.vm.set_gap(sender.Text)
            self._schedule()

        @guard
        def on_offset(sender, args):
            if self._suppress:
                return

            self.vm.set_offset(sender.Text)
            self._schedule()

        @guard
        def on_leader(sender, args):
            if self._suppress:
                return

            self.vm.set_leader(sender.Text)

        find("TxtGap").TextChanged += on_gap
        find("TxtOffset").TextChanged += on_offset
        find("TxtLeaderMm").TextChanged += on_leader

        @guard
        def on_tangle_flag(sender, args):
            if self._suppress:
                return

            self.vm.set_tangle(sender.IsChecked)
            self._recompute()

        chk_tangle = find("ChkTangle")
        chk_tangle.Checked += on_tangle_flag
        chk_tangle.Unchecked += on_tangle_flag

        @guard
        def on_leader_flag(sender, args):
            if self._suppress:
                return

            self.vm.set_enable_leader(sender.IsChecked)

        @guard
        def on_elbow_flag(sender, args):
            if self._suppress:
                return

            self.vm.set_rebuild_elbow(sender.IsChecked)

        chk_leader = find("ChkLeader")
        chk_leader.Checked += on_leader_flag
        chk_leader.Unchecked += on_leader_flag

        chk_elbow = find("ChkElbow")
        chk_elbow.Checked += on_elbow_flag
        chk_elbow.Unchecked += on_elbow_flag

        @guard
        def on_tick(sender, args):
            self._timer.Stop()
            self._recompute()

        self._timer.Tick += on_tick

        @guard
        def on_run(sender, args):
            self._accept()

        @guard
        def on_cancel(sender, args):
            self.window.Close()

        find("BtnRun").Click += on_run
        find("BtnCancel").Click += on_cancel

        pp_wpf.wire_keys(self.window, on_accept=self._accept)

    # ---------- расчёт предпросмотра ---------------------------------

    def _schedule(self):
        u"""Пересчёт с задержкой: на каждую букву в поле дёргать его незачем."""
        self._timer.Stop()
        self._timer.Start()

    def _scoped_boxes(self):
        u"""Копии габаритов с проставленной неподвижностью по выбранной области.

        Марки вне области из расчёта не выбрасываются: они остаются
        препятствиями, иначе соседи наедут прямо на них.
        """
        scope = self.vm.scope
        selected = self.state.get(u"selected_ids") or set()
        categories = self.vm.checked_categories

        result = []

        for box in self.state.get(u"boxes") or []:
            copy = dict(box)

            if not copy[u"frozen"]:
                if scope == SCOPE_SELECTED and copy[u"id"] not in selected:
                    copy[u"frozen"] = True
                    copy[u"reason"] = u"вне области раздвижки"

                elif scope == SCOPE_CATEGORIES and copy[u"cat"] not in categories:
                    copy[u"frozen"] = True
                    copy[u"reason"] = u"вне области раздвижки"

            result.append(copy)

        return result

    def _recompute(self):
        self._timer.Stop()

        if not self.vm.revalidate():
            self._last_moves = {}
            return

        solve = self.state.get(u"solve")
        ft_per_mm = self.state.get(u"ft_per_mm", 1.0)

        if solve is None:
            self._last_moves = {}
            return

        preview = solve(
            self._scoped_boxes(),
            self.vm.gap_mm * ft_per_mm,
            self.vm.offset_mm * ft_per_mm,
            self.vm.mode,
            self.vm.tangle
        )

        self._last_moves = preview.get(u"moves") or {}
        self.vm.set_preview(preview)

    # ---------- действия ---------------------------------------------

    def _accept(self):
        if self._timer.IsEnabled:
            self._recompute()

        if not self.vm.IsValid:
            return

        self.accepted = True

        # Присвоение DialogResult само закрывает модальное окно.
        self.window.DialogResult = True

    def _fail(self, message):
        self.vm._set_status(message, True)

    # ---------- запуск -------------------------------------------------

    def show(self):
        u"""Возвращает словарь параметров или None, если пользователь отказался."""
        self.window.ShowDialog()

        try:
            self._timer.Stop()
        except Exception:
            pass

        if not self.accepted:
            return None

        return self.vm.result(self._last_moves)


def ask_options(state, saved=None):
    return SpreadTagsWindow(state, saved).show()

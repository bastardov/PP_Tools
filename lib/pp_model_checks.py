# -*- coding: utf-8 -*-
"""Каркас проверок модели для инструмента «Проверка Модели» (панель «Проверка»).

Устроен по образцу pp_spec_checks.py: набор независимых проверок («стратегий»).

Как добавить проверку:
  * дописать CheckDefinition в get_check_definitions();
  * написать runner(doc, config, cache) -> CheckResult и, при нужде,
    опции в get_check_option_definitions() + дефолты в DEFAULT_MODEL_CONFIG.

У CheckDefinition есть поле kind: сейчас все проверки «читающие» (kind="report").
Поле оставлено на случай интерактивных проверок-действий (kind="action") —
тогда сценарий выполняется в script.py, которому доступны forms и UIApplication.
"""

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    ConnectorType,
    Domain,
    Element,
    ElementId,
    FilteredElementCollector,
    InsulationLiningBase,
    Level,
    StorageType,
    View,
    ViewType,
)

# Внутренние единицы Revit — футы. Пороги в UI задаём в мм.
FEET_TO_MM = 304.8


# --------------------------------------------------------------------------- #
#                                конфигурация                                 #
# --------------------------------------------------------------------------- #

DEFAULT_MODEL_CONFIG = {
    # Проверка «Трубы без расхода».
    # Пусто = встроенный «Расход» Revit (RBS_PIPE_FLOW_PARAM); иначе имя
    # своего параметра (напр. ADSK-параметр из внешнего расчёта).
    "pipe_flow_param": u"",
    # Маски имён систем через ; — проверять только их (пусто = все системы).
    "pipe_flow_include_systems": u"",
    # Маски имён систем через ; — исключить (напр. канализация, дренаж).
    "pipe_flow_exclude_systems": u"",
    # Проверка «Уклон у труб и воздуховодов».
    "slope_param": u"Уклон",
    # Значения уклона в ПРОЦЕНТАХ, которые считаем штатными и не показываем.
    # Разделитель — точка с запятой (запятая внутри числа = десятичная).
    # 100% = 45°, 57.7350% = 30°, 173.2051% = 60°; 0 = уклона нет.
    "slope_exclude_values": u"0; 100; 57.7350; 173.2051",
    # Допуск сравнения с исключениями, в процентных пунктах.
    "slope_tolerance": u"0.01",
    # Проверка «Элементы без системы (висящие)».
    # Ключи категорий через запятую (пусто = набор по умолчанию в runner).
    "orphan_categories": (
        u"OST_PipeAccessory, OST_PipeFitting, OST_MechanicalEquipment, "
        u"OST_DuctAccessory, OST_DuctTerminal"
    ),
    # Показывать частично подключённые (есть свободный конец, но не все).
    "orphan_include_partial": u"нет",
    # Проверка «Почти соединённые концы».
    # Допуск, мм: два свободных End-коннектора ближе этого — вероятно забыли соединить.
    "near_tolerance_mm": u"50",
    # Проверка «Короткие обрезки».
    "short_segment_mm": u"100",
    "short_segment_categories": u"OST_PipeCurves, OST_DuctCurves",
    # Проверка «Вырожденная геометрия» (длина ≈ 0).
    "degenerate_tolerance_mm": u"1",
    # Проверка «Дубли в одной точке».
    "duplicate_tolerance_mm": u"10",
    # Учитывать только совпадение типоразмера (да) или любой элемент категории (нет).
    "duplicate_same_type": u"да",
    "duplicate_categories": (
        u"OST_MechanicalEquipment, OST_PipeAccessory, OST_DuctAccessory, "
        u"OST_PipeCurves, OST_DuctCurves"
    ),
    # Проверка «Изоляция труб (наличие и тип)».
    # Правила по строкам: <ключи имени типа трубы> => <ключи имени типа изоляции>.
    # Слева и справа — подстроки через ; (совпадение по вхождению, регистр не важен).
    "insul_rules": (
        u"ГОСТ 10704; ГОСТ 3262 => Цилиндры некашированные\n"
        u"Полиэтилен сшитый; PE-Xa; PPR; PP-R => Трубки теплоизоляционные"
    ),
    # Маски систем через ; — где изоляция нужна (пусто = все) / исключить.
    "insul_include_systems": u"",
    "insul_exclude_systems": u"",
    # Показывать трубы, не попавшие ни в одно правило (нераспознанный класс).
    "insul_report_unclassified": u"нет",
    # Проверки ADSK_Позиция (не на своей категории / дубли у оборудования).
    "position_param": u"ADSK_Позиция",
    # Проверка «Полутон у Осей в шаблонах».
    # да = полутон должен стоять (покажем шаблоны, где снят); нет = наоборот.
    "grids_halftone_expected": u"да",
    "grids_tpl_include": u"",
    "grids_tpl_exclude": u"",
    # Проверка «Расстояние между ДП и ДВ (противодымная вентиляция)».
    # Маски через ; — по ним элемент относится к притоку (ДП) или к
    # вытяжке/дымоудалению (ДВ). Регистр не важен, сравнение по вхождению.
    "smoke_supply_masks": u"ДП",
    "smoke_exhaust_masks": u"ДВ",
    # Минимальное расстояние между притоком и вытяжкой, мм (норма — 5 м).
    "smoke_min_distance_mm": u"5000",
    "smoke_categories": u"OST_MechanicalEquipment",
    # Если имя системы пустое — искать маски в имени типа/семейства.
    "smoke_name_fallback": u"да",
    # Проверка «Высота дымоприёмного устройства».
    # В модели это обычно решётка (воздухораспределитель), надетая
    # на клапан дымоудаления, поэтому категория по умолчанию — воздухораспределители.
    "inlet_categories": u"OST_DuctTerminal",
    # Маски через ; — какие элементы считаем дымоприёмными.
    "inlet_masks": u"ДУ; ДВ",
    # Норма: низ устройства не ниже верха дверного проёма (~2100 мм).
    "inlet_min_height_mm": u"2100",
    # Если отметка уровня — конструктив, а не чистый пол: поправка в мм.
    "inlet_floor_offset_mm": u"0",
    # База отсчёта низа: «габарит» или «точка вставки».
    "inlet_base": u"габарит",
    "inlet_name_fallback": u"да",
}


# Категории с осевой геометрией (LocationCurve) — трубы/воздуховоды, гибкие.
CURVE_CATEGORY_OPTIONS = [
    (u"OST_PipeCurves", u"Трубы", BuiltInCategory.OST_PipeCurves),
    (u"OST_DuctCurves", u"Воздуховоды", BuiltInCategory.OST_DuctCurves),
    (u"OST_FlexPipeCurves", u"Гибкие трубы", BuiltInCategory.OST_FlexPipeCurves),
    (u"OST_FlexDuctCurves", u"Гибкие воздуховоды", BuiltInCategory.OST_FlexDuctCurves),
]

CURVE_CATEGORY_MAP = {
    key: built_in for key, _label, built_in in CURVE_CATEGORY_OPTIONS
}

SHORT_DEFAULT_KEYS = [u"OST_PipeCurves", u"OST_DuctCurves"]

# Все осевые категории для «Вырожденной геометрии» (без настройки категорий).
DEGENERATE_CATEGORIES = [
    built_in for _key, _label, built_in in CURVE_CATEGORY_OPTIONS
]


# Категории для проверки «Почти соединённые концы» — всё, что несёт MEP-коннекторы.
NEAR_CATEGORIES = [
    BuiltInCategory.OST_PipeCurves,
    BuiltInCategory.OST_DuctCurves,
    BuiltInCategory.OST_PipeFitting,
    BuiltInCategory.OST_DuctFitting,
    BuiltInCategory.OST_PipeAccessory,
    BuiltInCategory.OST_DuctAccessory,
    BuiltInCategory.OST_MechanicalEquipment,
    BuiltInCategory.OST_DuctTerminal,
]


# Категории для проверки «Дубли в одной точке».
DUPLICATE_CATEGORY_OPTIONS = [
    (u"OST_MechanicalEquipment", u"Оборудование", BuiltInCategory.OST_MechanicalEquipment),
    (u"OST_PipeAccessory", u"Арматура трубопроводов", BuiltInCategory.OST_PipeAccessory),
    (u"OST_DuctAccessory", u"Арматура воздуховодов", BuiltInCategory.OST_DuctAccessory),
    (u"OST_DuctTerminal", u"Воздухораспределители", BuiltInCategory.OST_DuctTerminal),
    (u"OST_PipeFitting", u"Соединит. детали труб", BuiltInCategory.OST_PipeFitting),
    (u"OST_DuctFitting", u"Соединит. детали возд.", BuiltInCategory.OST_DuctFitting),
    (u"OST_PipeCurves", u"Трубы", BuiltInCategory.OST_PipeCurves),
    (u"OST_DuctCurves", u"Воздуховоды", BuiltInCategory.OST_DuctCurves),
]

DUPLICATE_CATEGORY_MAP = {
    key: built_in for key, _label, built_in in DUPLICATE_CATEGORY_OPTIONS
}

DUPLICATE_DEFAULT_KEYS = [
    u"OST_MechanicalEquipment",
    u"OST_PipeAccessory",
    u"OST_DuctAccessory",
    u"OST_PipeCurves",
    u"OST_DuctCurves",
]

# Осевые категории (дубль = совпадение обоих концов, а не точки вставки).
DUPLICATE_CURVE_KEYS = set([
    u"OST_PipeCurves",
    u"OST_DuctCurves",
    u"OST_FlexPipeCurves",
    u"OST_FlexDuctCurves",
])


# Категории для проверки «висящих» элементов: (ключ, подпись, BuiltInCategory).
ORPHAN_CATEGORY_OPTIONS = [
    (u"OST_PipeAccessory", u"Арматура трубопроводов", BuiltInCategory.OST_PipeAccessory),
    (u"OST_PipeFitting", u"Соединительные детали трубопроводов", BuiltInCategory.OST_PipeFitting),
    (u"OST_MechanicalEquipment", u"Оборудование", BuiltInCategory.OST_MechanicalEquipment),
    (u"OST_DuctAccessory", u"Арматура воздуховодов", BuiltInCategory.OST_DuctAccessory),
    (u"OST_DuctTerminal", u"Воздухораспределители", BuiltInCategory.OST_DuctTerminal),
    (u"OST_DuctFitting", u"Соединительные детали воздуховодов", BuiltInCategory.OST_DuctFitting),
]

ORPHAN_CATEGORY_MAP = {
    key: built_in for key, _label, built_in in ORPHAN_CATEGORY_OPTIONS
}

# По умолчанию отмечены самые ходовые «висящие» категории.
ORPHAN_DEFAULT_KEYS = [
    u"OST_PipeAccessory",
    u"OST_PipeFitting",
    u"OST_MechanicalEquipment",
    u"OST_DuctAccessory",
    u"OST_DuctTerminal",
]


# Категории для проверки «Расстояние между ДП и ДВ».
SMOKE_CATEGORY_OPTIONS = [
    (u"OST_MechanicalEquipment", u"Оборудование", BuiltInCategory.OST_MechanicalEquipment),
    (u"OST_DuctTerminal", u"Воздухораспределители", BuiltInCategory.OST_DuctTerminal),
    (u"OST_DuctAccessory", u"Арматура воздуховодов", BuiltInCategory.OST_DuctAccessory),
    (u"OST_DuctCurves", u"Воздуховоды", BuiltInCategory.OST_DuctCurves),
]

SMOKE_CATEGORY_MAP = {
    key: built_in for key, _label, built_in in SMOKE_CATEGORY_OPTIONS
}

SMOKE_DEFAULT_KEYS = [u"OST_MechanicalEquipment"]


# Категории для проверки «Высота дымоприёмного устройства».
# Обычно это решётка на клапане, поэтому воздухораспределители идут
# первыми и единственные отмечены по умолчанию.
INLET_CATEGORY_OPTIONS = [
    (u"OST_DuctTerminal", u"Воздухораспределители", BuiltInCategory.OST_DuctTerminal),
    (u"OST_MechanicalEquipment", u"Оборудование", BuiltInCategory.OST_MechanicalEquipment),
    (u"OST_DuctAccessory", u"Арматура воздуховодов", BuiltInCategory.OST_DuctAccessory),
]

INLET_CATEGORY_MAP = {
    key: built_in for key, _label, built_in in INLET_CATEGORY_OPTIONS
}

INLET_DEFAULT_KEYS = [u"OST_DuctTerminal"]


# --------------------------------------------------------------------------- #
#                                модель данных                                #
# --------------------------------------------------------------------------- #

class CheckIssue(object):
    def __init__(self, message, element_ids=None):
        self.message = message
        self.element_ids = []

        if element_ids is None:
            return

        for element_id in element_ids:
            if element_id is None:
                continue
            try:
                self.element_ids.append(int(element_id))
            except:
                pass


class CheckResult(object):
    def __init__(self, key, title, checked_count, issues, info=None):
        self.key = key
        self.title = title
        self.checked_count = checked_count
        self.issues = issues or []
        # Свободный текст итога (для проверок-действий, у которых нет issues).
        self.info = info or u""


class CheckDefinition(object):
    # kind:
    #   "report" — обычная проверка: runner(doc, config, cache) -> CheckResult
    #   "action" — интерактивное действие; выполняется в script.py по action_key
    def __init__(self, key, title, description, runner=None,
                 option_keys=None, kind=u"report", action_key=None):
        self.key = key
        self.title = title
        self.description = description
        self.runner = runner
        self.option_keys = option_keys or []
        self.kind = kind
        self.action_key = action_key


class CheckOptionDefinition(object):
    # option_type:
    #   "text"                 — текстовое поле (по умолчанию)
    #   "param"                — имя параметра: поле плюс кнопка
    #                            «Выбрать…» (см. lib/pp_param_picker)
    #   "category_multiselect" — список категорий с галочками (choices)
    def __init__(self, key, label, check_keys, option_type=u"text", choices=None):
        self.key = key
        self.label = label
        self.check_keys = check_keys
        self.option_type = option_type
        self.choices = choices or []


class ElementCache(object):
    """Кэш коллекций элементов на время одного запуска (как в pp_spec_checks).

    Пока «читающих» проверок нет, но кэш готов для будущих стратегий и
    сохраняет ту же архитектуру, что и в «Проверке спецификации».
    """

    def __init__(self, doc):
        self.doc = doc
        self._by_category = {}
        self._all = None

    def get_by_categories(self, categories):
        key = tuple(sorted(unicode(category) for category in categories))
        cached = self._by_category.get(key)
        if cached is not None:
            return cached

        result = _collect_elements(self.doc, categories)
        self._by_category[key] = result
        return result

    def get_all(self):
        if self._all is None:
            self._all = _collect_all_elements(self.doc)
        return self._all


def create_element_cache(doc):
    return ElementCache(doc)


def _collect_elements(doc, categories):
    result = []
    for category in categories:
        try:
            collector = FilteredElementCollector(doc) \
                .OfCategory(category) \
                .WhereElementIsNotElementType()
            result.extend(list(collector.ToElements()))
        except:
            pass
    return result


def _collect_all_elements(doc):
    try:
        return list(
            FilteredElementCollector(doc)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except:
        return []


# --------------------------------------------------------------------------- #
#                             определения проверок                            #
# --------------------------------------------------------------------------- #

def get_default_config():
    return dict(DEFAULT_MODEL_CONFIG)


def get_check_option_definitions():
    return [
        CheckOptionDefinition(
            "pipe_flow_param",
            u"Параметр расхода (пусто = встроенный «Расход» Revit)",
            ["pipe_flow_missing"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "pipe_flow_include_systems",
            u"Проверять только системы (маски через ;, пусто = все)",
            ["pipe_flow_missing"]
        ),
        CheckOptionDefinition(
            "pipe_flow_exclude_systems",
            u"Исключить системы (маски через ;)",
            ["pipe_flow_missing"]
        ),
        CheckOptionDefinition(
            "slope_param",
            u"Параметр уклона",
            ["slope_unexpected"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "slope_exclude_values",
            u"Пропускать уклоны, % (через ; — напр. 0; 100; 57.7350; 173.2051)",
            ["slope_unexpected"]
        ),
        CheckOptionDefinition(
            "slope_tolerance",
            u"Допуск сравнения, процентных пунктов",
            ["slope_unexpected"]
        ),
        CheckOptionDefinition(
            "orphan_categories",
            u"Категории для проверки",
            ["orphan_no_system"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _b in ORPHAN_CATEGORY_OPTIONS]
        ),
        CheckOptionDefinition(
            "orphan_include_partial",
            u"Показывать частично подключённые (да/нет)",
            ["orphan_no_system"]
        ),
        CheckOptionDefinition(
            "near_tolerance_mm",
            u"Допуск сближения концов, мм",
            ["near_connectors"]
        ),
        CheckOptionDefinition(
            "short_segment_mm",
            u"Порог короткого участка, мм",
            ["short_segment"]
        ),
        CheckOptionDefinition(
            "short_segment_categories",
            u"Категории для проверки",
            ["short_segment"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _b in CURVE_CATEGORY_OPTIONS]
        ),
        CheckOptionDefinition(
            "degenerate_tolerance_mm",
            u"Порог вырожденной длины, мм (длина ≤ порога)",
            ["degenerate_geometry"]
        ),
        CheckOptionDefinition(
            "duplicate_tolerance_mm",
            u"Допуск совпадения точек, мм",
            ["duplicate_at_point"]
        ),
        CheckOptionDefinition(
            "duplicate_same_type",
            u"Только одинаковый типоразмер (да/нет)",
            ["duplicate_at_point"]
        ),
        CheckOptionDefinition(
            "duplicate_categories",
            u"Категории для проверки",
            ["duplicate_at_point"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _b in DUPLICATE_CATEGORY_OPTIONS]
        ),
        CheckOptionDefinition(
            "insul_rules",
            u"Правила: <тип трубы содержит> => <изоляция содержит> (по строкам)",
            ["pipe_insulation"],
            option_type=u"multiline"
        ),
        CheckOptionDefinition(
            "insul_include_systems",
            u"Проверять только системы (маски через ;, пусто = все)",
            ["pipe_insulation"]
        ),
        CheckOptionDefinition(
            "insul_exclude_systems",
            u"Исключить системы (маски через ;)",
            ["pipe_insulation"]
        ),
        CheckOptionDefinition(
            "insul_report_unclassified",
            u"Показывать трубы без правила (да/нет)",
            ["pipe_insulation"]
        ),
        CheckOptionDefinition(
            "position_param",
            u"Параметр позиции",
            ["position_wrong_category", "position_duplicate"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "grids_halftone_expected",
            u"Полутон у Осей должен стоять (да/нет)",
            ["grids_halftone"]
        ),
        CheckOptionDefinition(
            "grids_tpl_include",
            u"Проверять только шаблоны (маски имён через ;, пусто = все)",
            ["grids_halftone"]
        ),
        CheckOptionDefinition(
            "grids_tpl_exclude",
            u"Исключить шаблоны (маски имён через ;)",
            ["grids_halftone"]
        ),
        CheckOptionDefinition(
            "smoke_categories",
            u"Категории для проверки",
            ["smoke_distance"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _b in SMOKE_CATEGORY_OPTIONS]
        ),
        CheckOptionDefinition(
            "smoke_supply_masks",
            u"Приток противодымной — маски имён через ; (напр. ДП)",
            ["smoke_distance"]
        ),
        CheckOptionDefinition(
            "smoke_exhaust_masks",
            u"Вытяжка противодымной — маски имён через ; (напр. ДВ)",
            ["smoke_distance"]
        ),
        CheckOptionDefinition(
            "smoke_min_distance_mm",
            u"Минимальное расстояние, мм",
            ["smoke_distance"]
        ),
        CheckOptionDefinition(
            "smoke_name_fallback",
            u"Если системы нет — искать маски в имени типа (да/нет)",
            ["smoke_distance"]
        ),
        CheckOptionDefinition(
            "inlet_categories",
            u"Категории для проверки",
            ["smoke_inlet_height"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _b in INLET_CATEGORY_OPTIONS]
        ),
        CheckOptionDefinition(
            "inlet_masks",
            u"Дымоприёмные устройства — маски имён через ; (напр. ДУ; ДВ)",
            ["smoke_inlet_height"]
        ),
        CheckOptionDefinition(
            "inlet_min_height_mm",
            u"Минимальная высота низа от пола, мм",
            ["smoke_inlet_height"]
        ),
        CheckOptionDefinition(
            "inlet_floor_offset_mm",
            u"Поправка «уровень → чистый пол», мм",
            ["smoke_inlet_height"]
        ),
        CheckOptionDefinition(
            "inlet_base",
            u"База отсчёта низа: габарит / точка вставки",
            ["smoke_inlet_height"]
        ),
        CheckOptionDefinition(
            "inlet_name_fallback",
            u"Если системы нет — искать маски в имени типа (да/нет)",
            ["smoke_inlet_height"]
        ),
    ]


def get_check_definitions():
    return [
        CheckDefinition(
            "pipe_flow_missing",
            u"1. Трубы без расхода",
            u"Ищет трубы с пустым или нулевым расходом. Параметр расхода "
            u"настраивается: пусто = встроенный «Расход» Revit, иначе имя своего "
            u"параметра (напр. из внешнего расчёта). Можно ограничить проверку "
            u"по именам систем (маски-подстроки) и исключить системы без "
            u"расхода (канализация, дренаж и т.п.). Трубы без назначенной "
            u"системы помечаются отдельно.",
            runner=_run_pipe_flow_check,
            option_keys=[
                "pipe_flow_param",
                "pipe_flow_include_systems",
                "pipe_flow_exclude_systems",
            ],
            kind=u"report",
        ),
        CheckDefinition(
            "slope_unexpected",
            u"2. Уклон у труб и воздуховодов",
            u"Показывает трубы и воздуховоды, у которых в параметре «Уклон» "
            u"есть значение, кроме перечисленных в поле исключений. Штатные "
            u"диагональные участки задаются как проценты: 100% = 45°, "
            u"57.7350% = 30°, 173.2051% = 60°, 0 = уклона нет. Всё, что не "
            u"попало в исключения, попадает в отчёт. Сравнение идёт по "
            u"значению, которое Revit показывает в свойствах (проценты), с "
            u"заданным допуском. Элементы без значения уклона (например "
            u"вертикальные участки) пропускаются.",
            runner=_run_slope_check,
            option_keys=[
                "slope_param",
                "slope_exclude_values",
                "slope_tolerance",
            ],
            kind=u"report",
        ),
        CheckDefinition(
            "orphan_no_system",
            u"3. Элементы без системы (висящие)",
            u"Ищет элементы выбранных категорий (клапаны, краны, фитинги, "
            u"оборудование и т.п.), у которых есть MEP-коннекторы, но ни один не "
            u"подключён — то есть элемент «висит» и не входит ни в одну систему. "
            u"Если включить «частично подключённые», в отчёт добавятся элементы "
            u"с частью свободных концов. Элементы без MEP-коннекторов "
            u"пропускаются.",
            runner=_run_orphan_check,
            option_keys=["orphan_categories", "orphan_include_partial"],
            kind=u"report",
        ),
        CheckDefinition(
            "near_connectors",
            u"4. Почти соединённые концы",
            u"Ищет пары свободных (не подключённых) End-коннекторов труб, "
            u"воздуховодов, фитингов, арматуры, оборудования и "
            u"воздухораспределителей, которые расположены ближе заданного "
            u"допуска друг к другу, но не соединены. Это типовая ошибка «забыл "
            u"дотянуть/состыковать»: визуально примыкает, а по факту разрыв "
            u"сети. Допуск сближения задаётся в мм (по умолчанию 50). "
            u"Коннекторы одного элемента между собой не сравниваются.",
            runner=_run_near_connectors_check,
            option_keys=["near_tolerance_mm"],
            kind=u"report",
        ),
        CheckDefinition(
            "short_segment",
            u"5. Короткие обрезки",
            u"Находит трубы и воздуховоды короче заданного порога — обычно это "
            u"мусорные обрезки, оставшиеся от редактирования (разрыв, обрезка, "
            u"перетаскивание). Они засоряют спецификацию и подсчёты. Порог "
            u"задаётся в мм (по умолчанию 100), набор категорий — галочками. "
            u"Совсем вырожденные участки (длина ≈ 0) показывает отдельная "
            u"проверка «Вырожденная геометрия».",
            runner=_run_short_segment_check,
            option_keys=["short_segment_mm", "short_segment_categories"],
            kind=u"report",
        ),
        CheckDefinition(
            "degenerate_geometry",
            u"6. Вырожденная геометрия",
            u"Показывает трубы и воздуховоды с длиной практически ноль (меньше "
            u"или равно порогу, по умолчанию 1 мм). Такие элементы Revit иногда "
            u"допускает при ошибочном редактировании; они не видны, ломают "
            u"соединения и подсчёты, их нужно удалить. Порог задаётся в мм.",
            runner=_run_degenerate_check,
            option_keys=["degenerate_tolerance_mm"],
            kind=u"report",
        ),
        CheckDefinition(
            "duplicate_at_point",
            u"7. Дубли в одной точке",
            u"Ищет наложенные друг на друга элементы одной категории: "
            u"оборудование, арматуру, фитинги и воздухораспределители — по "
            u"точке вставки; трубы и воздуховоды — по совпадению обоих концов. "
            u"Совпадение проверяется с допуском (по умолчанию 10 мм). Опция "
            u"«только одинаковый типоразмер» (по умолчанию да) отсекает случаи "
            u"разных элементов в одном месте. Набор категорий — галочками.",
            runner=_run_duplicate_check,
            option_keys=[
                "duplicate_tolerance_mm",
                "duplicate_same_type",
                "duplicate_categories",
            ],
            kind=u"report",
        ),
        CheckDefinition(
            "pipe_insulation",
            u"8. Изоляция труб (наличие и тип)",
            u"Проверяет изоляцию труб по правилам, которые вы задаёте строками "
            u"вида «<тип трубы содержит> => <изоляция содержит>». Для каждой "
            u"трубы в зоне проверки: класс определяется по вхождению ключа в имя "
            u"типа трубы; если изоляции нет — «нет изоляции», если есть, но имя "
            u"её типа не содержит ожидаемого ключа — «не тот тип». Систем задают "
            u"зону проверки масками (где изоляция нужна). Трубы, не попавшие ни "
            u"в одно правило, по умолчанию пропускаются.",
            runner=_run_pipe_insulation_check,
            option_keys=[
                "insul_rules",
                "insul_include_systems",
                "insul_exclude_systems",
                "insul_report_unclassified",
            ],
            kind=u"report",
        ),
        CheckDefinition(
            "position_wrong_category",
            u"9. ADSK_Позиция не на своей категории",
            u"Параметр позиции (по умолчанию ADSK_Позиция) должен быть заполнен "
            u"только у Оборудования. Проверка показывает элементы ВСЕХ прочих "
            u"категорий, у которых этот параметр заполнен — это ошибка "
            u"(позицию поставили не туда).",
            runner=_run_position_category_check,
            option_keys=["position_param"],
            kind=u"report",
        ),
        CheckDefinition(
            "position_duplicate",
            u"10. Дубли ADSK_Позиция у оборудования",
            u"Среди Оборудования ищет одинаковые значения параметра позиции "
            u"(по умолчанию ADSK_Позиция): позиции должны быть уникальными, "
            u"поэтому любое повторение значения у двух и более единиц — ошибка. "
            u"Пустые значения не сравниваются.",
            runner=_run_position_duplicate_check,
            option_keys=["position_param"],
            kind=u"report",
        ),
        CheckDefinition(
            "grids_halftone",
            u"11. Полутон у Осей в шаблонах",
            u"Проверяет переопределение полутона у категории аннотаций «Оси» в "
            u"шаблонах видов. Берутся ТОЛЬКО планы этажей (обычные и инженерные "
            u"ОВиК-планы); 3D, разрезы, фасады, потолки и прочее не проверяются. "
            u"По умолчанию полутон должен стоять — проверка показывает шаблоны, "
            u"где он снят (ожидание меняется полем да/нет). Область сужается "
            u"масками имён шаблонов.",
            runner=_run_grids_halftone_check,
            option_keys=[
                "grids_halftone_expected",
                "grids_tpl_include",
                "grids_tpl_exclude",
            ],
            kind=u"report",
        ),
        CheckDefinition(
            "smoke_distance",
            u"12. Расстояние между ДП и ДВ (противодымная)",
            u"Проверяет разрыв между приточной противодымной вентиляцией (ДП) "
            u"и вытяжной / дымоудалением (ДВ): расстояние по прямой должно быть "
            u"не меньше заданного (по умолчанию 5000 мм). Элемент относится к ДП "
            u"или ДВ по маскам в имени системы; если имя системы пустое (частый "
            u"случай у крышных вентиляторов), маска ищется в имени типа или "
            u"семейства — это отключается полем «да/нет». Сравниваются только "
            u"пары приток↔вытяжка, внутри одной группы расстояние не "
            u"проверяется. Элементы, попавшие сразу под обе маски или ни под "
            u"одну, пропускаются.",
            runner=_run_smoke_distance_check,
            option_keys=[
                "smoke_categories",
                "smoke_supply_masks",
                "smoke_exhaust_masks",
                "smoke_min_distance_mm",
                "smoke_name_fallback",
            ],
            kind=u"report",
        ),
        CheckDefinition(
            "smoke_inlet_height",
            u"13. Высота дымоприёмного устройства",
            u"Проверяет, что низ дымоприёмного устройства не опускается ниже "
            u"заданной отметки от пола (по умолчанию 2100 мм — верх дверного "
            u"проёма). В модели дымоприёмное устройство — обычно решётка на "
            u"клапане дымоудаления, поэтому по умолчанию проверяются "
            u"Воздухораспределители, а отбор внутри категории идёт по маскам "
            u"имени системы (если системы нет — по имени типа или семейства; "
            u"маска должна начинать слово, поэтому «ДУ» ловит «ДУ1», но не "
            u"«возДУхораспределитель»). "
            u"Высота считается от отметки уровня до низа элемента: низ берётся "
            u"по габариту (рамка решётки) либо по точке вставки. Если уровни в "
            u"модели заданы по конструктиву, разницу до чистого пола укажите "
            u"полем «поправка». Когда уровень элемента оказывается выше самого "
            u"элемента (привязка не к своему этажу), берётся ближайший уровень "
            u"снизу.",
            runner=_run_smoke_inlet_height_check,
            option_keys=[
                "inlet_categories",
                "inlet_masks",
                "inlet_min_height_mm",
                "inlet_floor_offset_mm",
                "inlet_base",
                "inlet_name_fallback",
            ],
            kind=u"report",
        ),
    ]


def run_report_checks(doc, selected_keys, config, cache=None):
    """Запускает «читающие» проверки (kind == "report")."""
    selected_keys = selected_keys or []
    merged = get_default_config()
    if config:
        merged.update(config)

    if cache is None:
        cache = create_element_cache(doc)

    key_map = {}
    for definition in get_check_definitions():
        key_map[definition.key] = definition

    results = []
    for key in selected_keys:
        definition = key_map.get(key)
        if definition is None or definition.kind != u"report":
            continue
        if definition.runner is None:
            continue
        results.append(definition.runner(doc, merged, cache))

    return results


# --------------------------------------------------------------------------- #
#                     проверка №1: трубы без расхода (report)                 #
# --------------------------------------------------------------------------- #

FLOW_EPS = 1e-9


def _parse_masks(raw_text):
    result = []
    seen = set()
    cleaned = unicode(raw_text or u"")
    for part in cleaned.replace(u",", u";").split(u";"):
        token = part.strip().lower()
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _matches_any(name, masks):
    low = (name or u"").lower()
    for mask in masks:
        if mask in low:
            return True
    return False


def _pipe_system_name(pipe):
    try:
        p = pipe.get_Parameter(BuiltInParameter.RBS_SYSTEM_NAME_PARAM)
        if p is not None and p.HasValue:
            return (p.AsString() or u"").strip()
    except:
        pass
    return u""


def _pipe_flow_value(pipe, param_name):
    """Возвращает расход (float) или None, если значение отсутствует."""
    if param_name:
        try:
            p = pipe.LookupParameter(param_name)
            if (p is not None and p.StorageType == StorageType.Double
                    and p.HasValue):
                return p.AsDouble()
        except:
            pass
        return None
    try:
        p = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_FLOW_PARAM)
        if p is not None and p.HasValue:
            return p.AsDouble()
    except:
        pass
    return None


def _pipe_size_text(pipe):
    try:
        p = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
        if p is not None and p.HasValue:
            text = p.AsValueString()
            if text:
                return text
    except:
        pass
    return u""


def _pipe_label(pipe, system_name):
    parts = []
    if system_name:
        parts.append(u"система «{0}»".format(system_name))
    size = _pipe_size_text(pipe)
    if size:
        parts.append(size)
    try:
        parts.append(u"id {0}".format(pipe.Id.IntegerValue))
    except:
        pass
    return u", ".join(parts) if parts else u"труба"


def _run_pipe_flow_check(doc, config, cache):
    param_name = unicode(config.get("pipe_flow_param") or u"").strip()
    include = _parse_masks(config.get("pipe_flow_include_systems"))
    exclude = _parse_masks(config.get("pipe_flow_exclude_systems"))

    pipes = cache.get_by_categories([BuiltInCategory.OST_PipeCurves])
    issues = []
    checked = 0

    for pipe in pipes:
        system_name = _pipe_system_name(pipe)
        if include and not _matches_any(system_name, include):
            continue
        if exclude and _matches_any(system_name, exclude):
            continue

        try:
            eid = pipe.Id.IntegerValue
        except:
            continue

        checked += 1
        label = _pipe_label(pipe, system_name)
        value = _pipe_flow_value(pipe, param_name)

        if value is None:
            issues.append(CheckIssue(
                u"Расход не заполнен — {0}".format(label), [eid]))
        elif abs(value) < FLOW_EPS:
            if not system_name:
                issues.append(CheckIssue(
                    u"Нет системы, расход 0 — {0}".format(label), [eid]))
            else:
                issues.append(CheckIssue(
                    u"Расход 0 — {0}".format(label), [eid]))

    return CheckResult(u"pipe_flow_missing", u"1. Трубы без расхода",
                       checked, issues)


# --------------------------------------------------------------------------- #
#              проверка №2: уклон у труб и воздуховодов (report)              #
# --------------------------------------------------------------------------- #

SLOPE_CATEGORIES = [
    BuiltInCategory.OST_PipeCurves,
    BuiltInCategory.OST_DuctCurves,
]

# Имена BuiltInParameter уклона перебираем через getattr: набор отличается
# между версиями Revit, отсутствующие просто пропускаем.
SLOPE_BIP_NAMES = ["RBS_SLOPE", "RBS_PIPE_SLOPE", "RBS_DUCT_SLOPE"]


def _parse_float(raw_text, default_value):
    try:
        text = unicode(raw_text or u"").strip().replace(u",", u".")
        if not text:
            return default_value
        return float(text)
    except:
        return default_value


def _parse_number_list(raw_text):
    """Числа через ';' (запятая внутри числа = десятичный разделитель)."""
    result = []
    cleaned = unicode(raw_text or u"").replace(u"\n", u";")
    for part in cleaned.split(u";"):
        token = part.strip().replace(u",", u".")
        if not token:
            continue
        try:
            result.append(float(token))
        except:
            pass
    return result


def _get_slope_param(element, param_name):
    if param_name:
        try:
            p = element.LookupParameter(param_name)
            if p is not None:
                return p
        except:
            pass
    for name in SLOPE_BIP_NAMES:
        try:
            bip = getattr(BuiltInParameter, name, None)
            if bip is None:
                continue
            p = element.get_Parameter(bip)
            if p is not None:
                return p
        except:
            pass
    return None


def _parse_slope_text(text):
    """Разбирает то, что Revit показывает в свойствах: «57.7350%», «1:100»."""
    t = unicode(text or u"").strip()
    if not t:
        return None
    t = t.replace(u" ", u" ").replace(u",", u".")

    if u":" in t:
        parts = t.split(u":")
        if len(parts) == 2:
            try:
                a = float(_keep_number_chars(parts[0]))
                b = float(_keep_number_chars(parts[1]))
                if b:
                    return a / b * 100.0
            except:
                return None
        return None

    cleaned = _keep_number_chars(t)
    try:
        return float(cleaned)
    except:
        return None


def _keep_number_chars(text):
    out = []
    for ch in unicode(text or u""):
        if ch.isdigit() or ch in u".-":
            out.append(ch)
    return u"".join(out)


def _slope_percent(param):
    """Уклон в процентах (float) или None, если значения нет."""
    try:
        if not param.HasValue:
            return None
    except:
        pass

    text = None
    try:
        text = param.AsValueString()
    except:
        text = None

    if text:
        value = _parse_slope_text(text)
        if value is not None:
            return value

    # Запасной путь: внутреннее значение уклона — отношение (1.0 = 100%).
    try:
        return param.AsDouble() * 100.0
    except:
        return None


def _format_slope(value):
    try:
        text = u"{0:.4f}".format(value)
    except:
        return unicode(value)
    if u"." in text:
        text = text.rstrip(u"0").rstrip(u".")
    return u"{0}%".format(text)


def _element_size_text(element):
    try:
        p = element.get_Parameter(BuiltInParameter.RBS_CALCULATED_SIZE)
        if p is not None and p.HasValue:
            return (p.AsString() or u"").strip()
    except:
        pass
    return u""


def _element_label(element, system_name):
    parts = []
    try:
        if element.Category is not None:
            parts.append(element.Category.Name)
    except:
        pass
    if system_name:
        parts.append(u"система «{0}»".format(system_name))
    size = _element_size_text(element)
    if size:
        parts.append(size)
    try:
        parts.append(u"id {0}".format(element.Id.IntegerValue))
    except:
        pass
    return u", ".join(parts) if parts else u"элемент"


def _run_slope_check(doc, config, cache):
    param_name = unicode(config.get("slope_param") or u"").strip()
    excluded = _parse_number_list(config.get("slope_exclude_values"))
    tolerance = abs(_parse_float(config.get("slope_tolerance"), 0.01))

    elements = cache.get_by_categories(SLOPE_CATEGORIES)
    issues = []
    checked = 0

    for element in elements:
        param = _get_slope_param(element, param_name)
        if param is None:
            continue

        slope = _slope_percent(param)
        if slope is None:
            continue

        checked += 1

        skip = False
        for value in excluded:
            if abs(slope - value) <= tolerance:
                skip = True
                break
        if skip:
            continue

        try:
            eid = element.Id.IntegerValue
        except:
            continue

        system_name = _pipe_system_name(element)
        issues.append(CheckIssue(
            u"Уклон {0} — {1}".format(
                _format_slope(slope), _element_label(element, system_name)),
            [eid]))

    return CheckResult(u"slope_unexpected", u"2. Уклон у труб и воздуховодов",
                       checked, issues)


# --------------------------------------------------------------------------- #
#           проверка №3: элементы без системы / «висящие» (report)           #
# --------------------------------------------------------------------------- #

def _parse_category_keys(raw_text):
    result = []
    seen = set()
    cleaned = unicode(raw_text or u"").replace(u";", u",").replace(u"/", u",")
    for part in cleaned.split(u","):
        token = part.strip()
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _is_yes(raw_text):
    return unicode(raw_text or u"").strip().lower() in (
        u"да", u"yes", u"y", u"1", u"true", u"+")


def _connector_stats(element):
    """(всего физических коннекторов Piping/HVAC, из них подключённых).

    Возвращает (0, 0), если у элемента нет MEP-коннекторов (значит он не может
    «висеть» на сети и в проверке не участвует).
    """
    try:
        mep_model = element.MEPModel
    except:
        mep_model = None
    if mep_model is None:
        return (0, 0)

    try:
        manager = mep_model.ConnectorManager
    except:
        manager = None
    if manager is None:
        return (0, 0)

    try:
        connectors = manager.Connectors
    except:
        return (0, 0)

    total = 0
    connected = 0
    for connector in connectors:
        try:
            if connector.ConnectorType != ConnectorType.End:
                continue
        except:
            continue
        try:
            if connector.Domain not in (Domain.DomainPiping, Domain.DomainHvac):
                continue
        except:
            pass
        total += 1
        try:
            if connector.IsConnected:
                connected += 1
        except:
            pass
    return (total, connected)


def _run_orphan_check(doc, config, cache):
    keys = _parse_category_keys(config.get("orphan_categories"))
    if not keys:
        keys = list(ORPHAN_DEFAULT_KEYS)
    include_partial = _is_yes(config.get("orphan_include_partial"))

    categories = []
    for key in keys:
        built_in = ORPHAN_CATEGORY_MAP.get(key)
        if built_in is not None:
            categories.append(built_in)

    elements = cache.get_by_categories(categories)
    issues = []
    checked = 0

    for element in elements:
        total, connected = _connector_stats(element)
        if total == 0:
            continue

        try:
            eid = element.Id.IntegerValue
        except:
            continue

        checked += 1
        system_name = _pipe_system_name(element)
        label = _element_label(element, system_name)

        if connected == 0:
            issues.append(CheckIssue(
                u"Не подключён (висит) — {0}".format(label), [eid]))
        elif include_partial and connected < total:
            issues.append(CheckIssue(
                u"Частично подключён: свободно {0} из {1} — {2}".format(
                    total - connected, total, label), [eid]))

    return CheckResult(u"orphan_no_system",
                       u"3. Элементы без системы (висящие)", checked, issues)


# --------------------------------------------------------------------------- #
#                    общие геометрические хелперы (report)                    #
# --------------------------------------------------------------------------- #

def _get_connector_manager(element):
    """ConnectorManager элемента: у осевых (труба/воздуховод) он свой, у
    семейств (фитинг/арматура/оборудование) — через MEPModel."""
    try:
        cm = element.ConnectorManager
        if cm is not None:
            return cm
    except:
        pass
    try:
        mep = element.MEPModel
        if mep is not None:
            return mep.ConnectorManager
    except:
        pass
    return None


def _iter_end_connectors(element):
    """Список End-коннекторов домена Piping/HVAC (физические концы сети)."""
    manager = _get_connector_manager(element)
    if manager is None:
        return []
    try:
        connectors = manager.Connectors
    except:
        return []

    result = []
    for connector in connectors:
        try:
            if connector.ConnectorType != ConnectorType.End:
                continue
        except:
            continue
        try:
            if connector.Domain not in (Domain.DomainPiping, Domain.DomainHvac):
                continue
        except:
            pass
        result.append(connector)
    return result


def _location_curve(element):
    try:
        loc = element.Location
    except:
        return None
    if loc is None:
        return None
    try:
        return loc.Curve
    except:
        return None


def _element_length_mm(element):
    """Длина осевой геометрии в мм или None, если геометрии нет."""
    curve = _location_curve(element)
    if curve is None:
        return None
    try:
        return curve.Length * FEET_TO_MM
    except:
        return None


def _location_point(element):
    try:
        loc = element.Location
    except:
        return None
    if loc is None:
        return None
    try:
        point = loc.Point
        if point is not None:
            return point
    except:
        pass
    return None


def _cell(value, size):
    """Индекс ячейки сетки. Усечение int безопасно при переборе соседей ±1."""
    try:
        return int(value / size)
    except:
        return 0


def _safe_name(element):
    try:
        return Element.Name.GetValue(element)
    except:
        pass
    try:
        return element.Name or u""
    except:
        return u""


def _type_name(doc, element):
    try:
        type_element = doc.GetElement(element.GetTypeId())
        if type_element is not None:
            return _safe_name(type_element)
    except:
        pass
    return u""


def _parse_category_map(raw_text, category_map, default_keys):
    keys = _parse_category_keys(raw_text)
    if not keys:
        keys = list(default_keys)
    categories = []
    for key in keys:
        built_in = category_map.get(key)
        if built_in is not None:
            categories.append(built_in)
    return categories


# --------------------------------------------------------------------------- #
#              проверка №4: почти соединённые концы (report)                  #
# --------------------------------------------------------------------------- #

def _run_near_connectors_check(doc, config, cache):
    tol_mm = _parse_float(config.get("near_tolerance_mm"), 50.0)
    if tol_mm <= 0:
        tol_mm = 50.0
    tol_ft = tol_mm / FEET_TO_MM

    elements = cache.get_by_categories(NEAR_CATEGORIES)

    # Свободные концы: (element_id, XYZ).
    free = []
    for element in elements:
        try:
            eid = element.Id.IntegerValue
        except:
            continue
        for connector in _iter_end_connectors(element):
            try:
                if connector.IsConnected:
                    continue
            except:
                pass
            try:
                origin = connector.Origin
            except:
                continue
            if origin is None:
                continue
            free.append((eid, origin))

    # Пространственная сетка с шагом = допуску: пара ближе tol попадает в
    # соседние ячейки, поэтому достаточно перебрать 27 ячеек-соседей.
    grid = {}
    for index, item in enumerate(free):
        origin = item[1]
        key = (_cell(origin.X, tol_ft), _cell(origin.Y, tol_ft),
               _cell(origin.Z, tol_ft))
        grid.setdefault(key, []).append(index)

    used = set()
    issues = []
    for index, item in enumerate(free):
        if index in used:
            continue
        eid, origin = item
        cx = _cell(origin.X, tol_ft)
        cy = _cell(origin.Y, tol_ft)
        cz = _cell(origin.Z, tol_ft)

        best = None
        best_dist = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    bucket = grid.get((cx + dx, cy + dy, cz + dz))
                    if not bucket:
                        continue
                    for other in bucket:
                        if other <= index or other in used:
                            continue
                        other_eid, other_origin = free[other]
                        if other_eid == eid:
                            continue
                        try:
                            dist = origin.DistanceTo(other_origin)
                        except:
                            continue
                        if dist <= tol_ft and (best_dist is None
                                               or dist < best_dist):
                            best_dist = dist
                            best = other

        if best is None:
            continue

        used.add(index)
        used.add(best)
        other_eid = free[best][0]
        dist_mm = best_dist * FEET_TO_MM
        issues.append(CheckIssue(
            u"Свободные концы в {0} мм, но не соединены — id {1} и id {2}".format(
                int(round(dist_mm)), eid, other_eid),
            [eid, other_eid]))

    return CheckResult(u"near_connectors", u"4. Почти соединённые концы",
                       len(free), issues)


# --------------------------------------------------------------------------- #
#                    проверка №5: короткие обрезки (report)                   #
# --------------------------------------------------------------------------- #

LENGTH_EPS_MM = 1e-4


def _run_short_segment_check(doc, config, cache):
    threshold = _parse_float(config.get("short_segment_mm"), 100.0)
    if threshold <= 0:
        threshold = 100.0
    categories = _parse_category_map(
        config.get("short_segment_categories"),
        CURVE_CATEGORY_MAP, SHORT_DEFAULT_KEYS)

    elements = cache.get_by_categories(categories)
    issues = []
    checked = 0

    for element in elements:
        length = _element_length_mm(element)
        if length is None:
            continue
        checked += 1
        if LENGTH_EPS_MM < length < threshold:
            try:
                eid = element.Id.IntegerValue
            except:
                continue
            system_name = _pipe_system_name(element)
            issues.append(CheckIssue(
                u"Длина {0} мм — {1}".format(
                    int(round(length)), _element_label(element, system_name)),
                [eid]))

    return CheckResult(u"short_segment", u"5. Короткие обрезки", checked, issues)


# --------------------------------------------------------------------------- #
#                 проверка №6: вырожденная геометрия (report)                 #
# --------------------------------------------------------------------------- #

def _run_degenerate_check(doc, config, cache):
    tol_mm = _parse_float(config.get("degenerate_tolerance_mm"), 1.0)
    if tol_mm < 0:
        tol_mm = 1.0

    elements = cache.get_by_categories(DEGENERATE_CATEGORIES)
    issues = []
    checked = 0

    for element in elements:
        length = _element_length_mm(element)
        if length is None:
            continue
        checked += 1
        if length <= tol_mm:
            try:
                eid = element.Id.IntegerValue
            except:
                continue
            system_name = _pipe_system_name(element)
            issues.append(CheckIssue(
                u"Вырожденная (длина ~{0} мм) — {1}".format(
                    _format_length(length), _element_label(element, system_name)),
                [eid]))

    return CheckResult(u"degenerate_geometry", u"6. Вырожденная геометрия",
                       checked, issues)


def _format_length(length):
    try:
        text = u"{0:.2f}".format(length)
    except:
        return unicode(length)
    if u"." in text:
        text = text.rstrip(u"0").rstrip(u".")
    return text


# --------------------------------------------------------------------------- #
#                   проверка №7: дубли в одной точке (report)                 #
# --------------------------------------------------------------------------- #

def _run_duplicate_check(doc, config, cache):
    tol_mm = _parse_float(config.get("duplicate_tolerance_mm"), 10.0)
    if tol_mm <= 0:
        tol_mm = 10.0
    tol_ft = tol_mm / FEET_TO_MM
    same_type = _is_yes(config.get("duplicate_same_type"))

    keys = _parse_category_keys(config.get("duplicate_categories"))
    if not keys:
        keys = list(DUPLICATE_DEFAULT_KEYS)

    categories = []
    curve_category_ids = set()
    for key in keys:
        built_in = DUPLICATE_CATEGORY_MAP.get(key)
        if built_in is None:
            continue
        categories.append(built_in)
        if key in DUPLICATE_CURVE_KEYS:
            try:
                curve_category_ids.add(int(built_in))
            except:
                pass

    elements = cache.get_by_categories(categories)

    groups = {}
    checked = 0
    for element in elements:
        cat_id = _category_id(element)
        if cat_id is None:
            continue
        type_id = 0
        if same_type:
            try:
                type_id = element.GetTypeId().IntegerValue
            except:
                type_id = 0

        geom_key = None
        if cat_id in curve_category_ids:
            geom_key = _curve_endpoints_key(element, tol_ft)
        else:
            geom_key = _point_key(element, tol_ft)
        if geom_key is None:
            continue

        checked += 1
        full_key = (cat_id, type_id, geom_key)
        try:
            eid = element.Id.IntegerValue
        except:
            continue
        groups.setdefault(full_key, []).append((eid, element))

    issues = []
    for _key, entries in groups.items():
        if len(entries) < 2:
            continue
        ids = [eid for eid, _el in entries]
        first_element = entries[0][1]
        label = _duplicate_label(doc, first_element)
        issues.append(CheckIssue(
            u"Наложение {0} шт. — {1} (id: {2})".format(
                len(entries), label,
                u", ".join(unicode(i) for i in ids[:8])),
            ids))

    return CheckResult(u"duplicate_at_point", u"7. Дубли в одной точке",
                       checked, issues)


def _category_id(element):
    try:
        if element.Category is not None:
            return int(element.Category.Id.IntegerValue)
    except:
        pass
    return None


def _point_key(element, tol_ft):
    point = _location_point(element)
    if point is None:
        return None
    return (_cell(point.X, tol_ft), _cell(point.Y, tol_ft),
            _cell(point.Z, tol_ft))


def _curve_endpoints_key(element, tol_ft):
    curve = _location_curve(element)
    if curve is None:
        return None
    try:
        p0 = curve.GetEndPoint(0)
        p1 = curve.GetEndPoint(1)
    except:
        return None
    a = (_cell(p0.X, tol_ft), _cell(p0.Y, tol_ft), _cell(p0.Z, tol_ft))
    b = (_cell(p1.X, tol_ft), _cell(p1.Y, tol_ft), _cell(p1.Z, tol_ft))
    # Направление трубы не важно — концы упорядочиваем.
    return (a, b) if a <= b else (b, a)


def _duplicate_label(doc, element):
    parts = []
    try:
        if element.Category is not None:
            parts.append(element.Category.Name)
    except:
        pass
    type_name = _type_name(doc, element)
    if type_name:
        parts.append(u"тип «{0}»".format(type_name))
    return u", ".join(parts) if parts else u"элемент"


# --------------------------------------------------------------------------- #
#           проверка №8: изоляция труб — наличие и тип (report)              #
# --------------------------------------------------------------------------- #

def _parse_insulation_rules(raw_text):
    """Строки «ключи типа трубы => ключи изоляции» -> [(type_keys, insul_keys)].

    Ключи по обе стороны — подстроки через ; в нижнем регистре.
    """
    rules = []
    text = unicode(raw_text or u"").replace(u"\r\n", u"\n").replace(u"\r", u"\n")
    for line in text.split(u"\n"):
        line = line.strip()
        if not line or u"=>" not in line:
            continue
        left, right = line.split(u"=>", 1)
        type_keys = [k.strip().lower()
                     for k in left.replace(u",", u";").split(u";") if k.strip()]
        insul_keys = [k.strip().lower()
                      for k in right.replace(u",", u";").split(u";") if k.strip()]
        if type_keys and insul_keys:
            rules.append((type_keys, insul_keys))
    return rules


def _insulation_type_names(doc, pipe):
    """Имена типов изоляции, навешенной на трубу (список строк)."""
    names = []
    try:
        insul_ids = InsulationLiningBase.GetInsulationIds(doc, pipe.Id)
    except:
        insul_ids = None
    if not insul_ids:
        return names
    for insul_id in insul_ids:
        try:
            insul = doc.GetElement(insul_id)
        except:
            insul = None
        if insul is None:
            continue
        name = _type_name(doc, insul)
        if name:
            names.append(name)
    return names


def _run_pipe_insulation_check(doc, config, cache):
    rules = _parse_insulation_rules(config.get("insul_rules"))
    include = _parse_masks(config.get("insul_include_systems"))
    exclude = _parse_masks(config.get("insul_exclude_systems"))
    report_unclassified = _is_yes(config.get("insul_report_unclassified"))

    pipes = cache.get_by_categories([BuiltInCategory.OST_PipeCurves])
    issues = []
    checked = 0

    for pipe in pipes:
        system_name = _pipe_system_name(pipe)
        if include and not _matches_any(system_name, include):
            continue
        if exclude and _matches_any(system_name, exclude):
            continue

        type_name = _type_name(doc, pipe)
        low_type = (type_name or u"").lower()

        insul_keys = None
        for rule_type_keys, rule_insul_keys in rules:
            if any(key in low_type for key in rule_type_keys):
                insul_keys = rule_insul_keys
                break

        try:
            eid = pipe.Id.IntegerValue
        except:
            continue

        base = _pipe_label(pipe, system_name)
        if type_name:
            label = u"{0}, тип «{1}»".format(base, type_name)
        else:
            label = base

        if insul_keys is None:
            if report_unclassified:
                checked += 1
                issues.append(CheckIssue(
                    u"Класс трубы не распознан правилами — {0}".format(label),
                    [eid]))
            continue

        checked += 1
        insul_names = _insulation_type_names(doc, pipe)

        if not insul_names:
            issues.append(CheckIssue(
                u"Нет изоляции — {0}".format(label), [eid]))
            continue

        matched = False
        for name in insul_names:
            low = name.lower()
            if any(key in low for key in insul_keys):
                matched = True
                break

        if not matched:
            issues.append(CheckIssue(
                u"Не тот тип изоляции: «{0}» — {1}".format(
                    u"; ".join(insul_names), label), [eid]))

    return CheckResult(u"pipe_insulation", u"8. Изоляция труб (наличие и тип)",
                       checked, issues)


# --------------------------------------------------------------------------- #
#            проверки №9–10: ADSK_Позиция — категория и дубли                #
# --------------------------------------------------------------------------- #

def _param_text_from(param):
    """Текст значения параметра или u"" (пусто/нет значения)."""
    if param is None:
        return u""
    try:
        if not param.HasValue:
            return u""
    except:
        pass
    try:
        if param.StorageType == StorageType.String:
            return (param.AsString() or u"").strip()
    except:
        pass
    try:
        s = param.AsValueString()
        if s:
            return s.strip()
    except:
        pass
    return u""


def _read_param_text(element, param_name):
    try:
        return _param_text_from(element.LookupParameter(param_name))
    except:
        return u""


def _run_position_category_check(doc, config, cache):
    param_name = unicode(config.get("position_param") or u"ADSK_Позиция").strip()
    equip_cat = int(BuiltInCategory.OST_MechanicalEquipment)

    issues = []
    checked = 0

    for element in cache.get_all():
        cid = _category_id(element)
        if cid is None or cid == equip_cat:
            continue
        try:
            param = element.LookupParameter(param_name)
        except:
            param = None
        if param is None:
            continue

        checked += 1
        value = _param_text_from(param)
        if not value:
            continue

        try:
            eid = element.Id.IntegerValue
        except:
            continue

        issues.append(CheckIssue(
            u"{0} = «{1}» вне «Оборудования» — {2}".format(
                param_name, value, _duplicate_label(doc, element)),
            [eid]))

    return CheckResult(u"position_wrong_category",
                       u"9. ADSK_Позиция не на своей категории",
                       checked, issues)


def _run_position_duplicate_check(doc, config, cache):
    param_name = unicode(config.get("position_param") or u"ADSK_Позиция").strip()

    equipment = cache.get_by_categories([BuiltInCategory.OST_MechanicalEquipment])
    groups = {}
    order = []
    checked = 0

    for element in equipment:
        value = _read_param_text(element, param_name)
        if not value:
            continue
        checked += 1
        try:
            eid = element.Id.IntegerValue
        except:
            continue
        if value not in groups:
            groups[value] = []
            order.append(value)
        groups[value].append((eid, element))

    issues = []
    for value in order:
        entries = groups[value]
        if len(entries) < 2:
            continue
        ids = [eid for eid, _el in entries]
        names = []
        for _eid, element in entries[:8]:
            name = _read_param_text(element, u"ADSK_Наименование")
            if not name:
                name = _type_name(doc, element)
            names.append(name or u"—")
        issues.append(CheckIssue(
            u"Позиция «{0}» — {1} шт.: {2} (id: {3})".format(
                value, len(entries), u"; ".join(names),
                u", ".join(unicode(i) for i in ids[:8])),
            ids))

    return CheckResult(u"position_duplicate",
                       u"10. Дубли ADSK_Позиция у оборудования",
                       checked, issues)


# --------------------------------------------------------------------------- #
#            проверка №11: полутон у Осей в шаблонах видов (report)          #
# --------------------------------------------------------------------------- #

# Только планы этажей (обычный и инженерный ОВиК-план).
# 3D, разрезы, фасады, потолки и т.п. в этой проверке не участвуют.
GRID_TEMPLATE_VIEW_TYPES = (
    ViewType.FloorPlan,
    ViewType.EngineeringPlan,
)


def _run_grids_halftone_check(doc, config, cache):
    expected_on = _is_yes(config.get("grids_halftone_expected"))
    include = _parse_masks(config.get("grids_tpl_include"))
    exclude = _parse_masks(config.get("grids_tpl_exclude"))

    grids_cat = ElementId(BuiltInCategory.OST_Grids)
    issues = []
    checked = 0

    try:
        views = list(FilteredElementCollector(doc).OfClass(View).ToElements())
    except:
        views = []

    for view in views:
        try:
            if not view.IsTemplate:
                continue
        except:
            continue
        try:
            if view.ViewType not in GRID_TEMPLATE_VIEW_TYPES:
                continue
        except:
            continue

        name = _safe_name(view)
        if include and not _matches_any(name, include):
            continue
        if exclude and _matches_any(name, exclude):
            continue

        try:
            ogs = view.GetCategoryOverrides(grids_cat)
            halftone = bool(ogs.Halftone)
        except:
            continue

        checked += 1
        if halftone == expected_on:
            continue

        try:
            eid = view.Id.IntegerValue
            ids = [eid]
        except:
            ids = []

        if expected_on:
            message = u"Полутон снят (ожидается включён) — шаблон «{0}»".format(name)
        else:
            message = u"Полутон включён (ожидается снят) — шаблон «{0}»".format(name)
        issues.append(CheckIssue(message, ids))

    return CheckResult(u"grids_halftone", u"11. Полутон у Осей в шаблонах",
                       checked, issues)


# --------------------------------------------------------------------------- #
#         проверка №12: расстояние между ДП и ДВ противодымной (report)       #
# --------------------------------------------------------------------------- #

def _element_points(element):
    """Характерные точки элемента: точка вставки либо концы и середина оси."""
    point = _location_point(element)
    if point is not None:
        return [point]

    curve = _location_curve(element)
    if curve is None:
        return []

    points = []
    try:
        points.append(curve.GetEndPoint(0))
        points.append(curve.GetEndPoint(1))
    except:
        return []
    try:
        points.append(curve.Evaluate(0.5, True))
    except:
        pass
    return points


def _min_distance_ft(points_a, points_b):
    """Минимальное расстояние (футы) между двумя наборами точек."""
    best = None
    for point_a in points_a:
        for point_b in points_b:
            try:
                distance = point_a.DistanceTo(point_b)
            except:
                continue
            if best is None or distance < best:
                best = distance
    return best


def _smoke_search_text(doc, element, use_name_fallback):
    """(текст для поиска масок, имя системы). Пустой текст = элемент пропускаем."""
    system_name = _pipe_system_name(element)
    if system_name:
        return system_name, system_name
    if not use_name_fallback:
        return u"", u""

    parts = []
    type_name = _type_name(doc, element)
    if type_name:
        parts.append(type_name)
    own_name = _safe_name(element)
    if own_name and own_name not in parts:
        parts.append(own_name)
    return u" ".join(parts), u""


def _smoke_label(element, display_name):
    """Подпись элемента: категория, имя системы (или типа) и id."""
    parts = []
    try:
        if element.Category is not None:
            parts.append(element.Category.Name)
    except:
        pass
    if display_name:
        parts.append(u"«{0}»".format(display_name))
    try:
        parts.append(u"id {0}".format(element.Id.IntegerValue))
    except:
        pass
    return u", ".join(parts) if parts else u"элемент"


def _run_smoke_distance_check(doc, config, cache):
    supply_masks = _parse_masks(config.get("smoke_supply_masks")) or [u"дп"]
    exhaust_masks = _parse_masks(config.get("smoke_exhaust_masks")) or [u"дв"]
    use_name_fallback = _is_yes(config.get("smoke_name_fallback"))

    min_mm = _parse_float(config.get("smoke_min_distance_mm"), 5000.0)
    if min_mm <= 0:
        min_mm = 5000.0
    min_ft = min_mm / FEET_TO_MM

    categories = _parse_category_map(
        config.get("smoke_categories"), SMOKE_CATEGORY_MAP, SMOKE_DEFAULT_KEYS)
    elements = cache.get_by_categories(categories)

    supply = []
    exhaust = []
    for element in elements:
        search_text, system_name = _smoke_search_text(
            doc, element, use_name_fallback)
        if not search_text:
            continue

        is_supply = _matches_any(search_text, supply_masks)
        is_exhaust = _matches_any(search_text, exhaust_masks)
        # Ни одна маска не подошла или подошли обе — судить нельзя, пропускаем.
        if is_supply == is_exhaust:
            continue

        points = _element_points(element)
        if not points:
            continue

        try:
            element_id = element.Id.IntegerValue
        except:
            continue

        display = system_name or search_text
        record = (element, display, points, element_id)
        if is_supply:
            supply.append(record)
        else:
            exhaust.append(record)

    found = []
    for s_element, s_display, s_points, s_id in supply:
        for e_element, e_display, e_points, e_id in exhaust:
            distance = _min_distance_ft(s_points, e_points)
            if distance is None or distance >= min_ft:
                continue
            found.append((distance, s_element, s_display, s_id,
                          e_element, e_display, e_id))

    found.sort(key=lambda item: item[0])

    issues = []
    for distance, s_element, s_display, s_id, e_element, e_display, e_id in found:
        issues.append(CheckIssue(
            u"{0} м (норма {1} м) — ДП: {2}; ДВ: {3}".format(
                _format_metres(distance * FEET_TO_MM),
                _format_metres(min_mm),
                _smoke_label(s_element, s_display),
                _smoke_label(e_element, e_display)),
            [s_id, e_id]))

    info = u"Приток (ДП): {0}, вытяжка (ДВ): {1}, проверено пар: {2}".format(
        len(supply), len(exhaust), len(supply) * len(exhaust))
    if not supply or not exhaust:
        info += (u". Одна из групп пуста — проверьте маски имён "
                 u"и категории в настройках проверки")

    return CheckResult(u"smoke_distance",
                       u"12. Расстояние между ДП и ДВ (противодымная)",
                       len(supply) + len(exhaust), issues, info)


def _format_metres(value_mm):
    try:
        return (u"{0:.2f}".format(value_mm / 1000.0)).replace(u".", u",")
    except:
        return u"?"


# --------------------------------------------------------------------------- #
#     проверка №13: высота дымоприёмного устройства над полом (report)        #
# --------------------------------------------------------------------------- #

def _collect_levels(doc):
    """Список (отметка в футах, имя) всех уровней модели, по возрастанию."""
    levels = []
    try:
        collector = FilteredElementCollector(doc).OfClass(Level) \
            .WhereElementIsNotElementType()
        for level in collector.ToElements():
            try:
                levels.append((level.Elevation, _safe_name(level)))
            except:
                pass
    except:
        pass
    levels.sort(key=lambda item: item[0])
    return levels


def _element_level(doc, element):
    """(отметка в футах, имя) уровня элемента или (None, u"")."""
    level_id = None
    try:
        level_id = element.LevelId
    except:
        level_id = None

    if level_id is None or level_id == ElementId.InvalidElementId:
        # У разных семейств уровень лежит в разных BIP, набор разнится по
        # версиям Revit — поэтому перебираем через getattr.
        for name in (u"FAMILY_LEVEL_PARAM",
                     u"INSTANCE_SCHEDULE_ONLY_LEVEL_PARAM",
                     u"SCHEDULE_LEVEL_PARAM",
                     u"RBS_START_LEVEL_PARAM"):
            built_in = getattr(BuiltInParameter, name, None)
            if built_in is None:
                continue
            try:
                param = element.get_Parameter(built_in)
                if param is None or not param.HasValue:
                    continue
                candidate = param.AsElementId()
                if (candidate is not None
                        and candidate != ElementId.InvalidElementId):
                    level_id = candidate
                    break
            except:
                pass

    if level_id is None or level_id == ElementId.InvalidElementId:
        return None, u""

    try:
        level = doc.GetElement(level_id)
    except:
        return None, u""
    if level is None:
        return None, u""
    try:
        return level.Elevation, _safe_name(level)
    except:
        return None, u""


def _nearest_level_below(levels, z_ft, tolerance_ft):
    """Ближайший уровень на отметке элемента или ниже неё."""
    best = None
    for elevation, name in levels:
        if elevation > z_ft + tolerance_ft:
            continue
        if best is None or elevation > best[0]:
            best = (elevation, name)
    if best is None:
        return None, u""
    return best


def _element_bottom_z(element, use_point):
    """Отметка низа элемента в футах: габарит или точка вставки."""
    if use_point:
        point = _location_point(element)
        if point is not None:
            return point.Z

    try:
        box = element.get_BoundingBox(None)
    except:
        box = None
    if box is not None:
        try:
            return box.Min.Z
        except:
            pass

    point = _location_point(element)
    if point is not None:
        return point.Z
    return None


def _is_point_base(raw_text):
    """«точка вставки» / «вставка» / «point» = считать по точке вставки."""
    text = unicode(raw_text or u"").strip().lower()
    if not text:
        return False
    return (text.startswith(u"точ") or text.startswith(u"вст")
            or text.startswith(u"point"))


def _format_mm(value_mm):
    try:
        return u"{0:.0f}".format(value_mm)
    except:
        return u"?"


def _matches_word_start(text, masks):
    """Маска должна начинать слово: «ДУ1» подходит, «воздуховод» — нет.

    Обычное сравнение по вхождению здесь опасно: маска «ДУ» находится
    внутри слова «возДУхораспределитель», а имя типа мы как раз
    просматриваем, когда у элемента нет системы."""
    low = (text or u"").lower()
    for mask in masks:
        if not mask:
            continue
        start = 0
        while True:
            index = low.find(mask, start)
            if index < 0:
                break
            previous = low[index - 1] if index > 0 else u""
            if not previous or not (previous.isalpha() or previous.isdigit()):
                return True
            start = index + 1
    return False


def _run_smoke_inlet_height_check(doc, config, cache):
    masks = _parse_masks(config.get("inlet_masks")) or [u"ду"]
    use_name_fallback = _is_yes(config.get("inlet_name_fallback"))
    use_point = _is_point_base(config.get("inlet_base"))

    min_mm = _parse_float(config.get("inlet_min_height_mm"), 2100.0)
    if min_mm <= 0:
        min_mm = 2100.0
    floor_offset_mm = _parse_float(config.get("inlet_floor_offset_mm"), 0.0)

    categories = _parse_category_map(
        config.get("inlet_categories"), INLET_CATEGORY_MAP, INLET_DEFAULT_KEYS)
    elements = cache.get_by_categories(categories)

    levels = _collect_levels(doc)
    # Допуск, чтобы элемент ровно на отметке уровня не «проваливался» ниже.
    tolerance_ft = 10.0 / FEET_TO_MM

    checked = 0
    skipped_no_level = 0
    found = []
    for element in elements:
        search_text, system_name = _smoke_search_text(
            doc, element, use_name_fallback)
        if not search_text or not _matches_word_start(search_text, masks):
            continue

        z_ft = _element_bottom_z(element, use_point)
        if z_ft is None:
            continue

        elevation, level_name = _element_level(doc, element)
        # Уровень выше самого элемента = привязка не к своему этажу,
        # иначе высота ушла бы в минус. Берём ближайший уровень снизу.
        if elevation is None or elevation > z_ft + tolerance_ft:
            elevation, level_name = _nearest_level_below(
                levels, z_ft, tolerance_ft)

        if elevation is None:
            skipped_no_level += 1
            continue

        checked += 1
        height_mm = (z_ft - elevation) * FEET_TO_MM - floor_offset_mm
        if height_mm >= min_mm:
            continue

        try:
            element_id = element.Id.IntegerValue
        except:
            continue

        display = system_name or search_text
        found.append((height_mm, element, display, level_name, element_id))

    found.sort(key=lambda item: item[0])

    issues = []
    for height_mm, element, display, level_name, element_id in found:
        issues.append(CheckIssue(
            u"{0} мм от пола (норма {1} мм) — {2}, уровень «{3}»".format(
                _format_mm(height_mm),
                _format_mm(min_mm),
                _smoke_label(element, display),
                level_name or u"?"),
            [element_id]))

    info = u"Дымоприёмных устройств найдено: {0}".format(checked)
    if not checked:
        info += (u". Ни один элемент не подошёл — проверьте маски имён "
                 u"и категории в настройках проверки")
    if skipped_no_level:
        info += u". Без уровня, пропущено: {0}".format(skipped_no_level)
    if use_point:
        info += u". База отсчёта — точка вставки"
    if floor_offset_mm:
        info += u". Поправка пола: {0} мм".format(_format_mm(floor_offset_mm))

    return CheckResult(u"smoke_inlet_height",
                       u"13. Высота дымоприёмного устройства",
                       checked, issues, info)

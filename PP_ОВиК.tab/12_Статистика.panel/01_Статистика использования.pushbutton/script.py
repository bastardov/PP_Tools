# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

# Кнопка «Статистика использования».
#
# Здесь живёт вся логика подсчёта, окно (pp_stats_window.py) только показывает
# готовые строки.
#
# Режим «По фазам модели» отвечает на вопрос, какие инструменты идут в начале
# моделирования, а какие ближе к оформлению: время работы над каждой моделью
# приводится к шкале 0–100 % (см. build_phases), поэтому модели с разным
# сроком жизни складываются в одну картину.

import os
import sys
import math
import datetime

import pp_usage

# Модуль окна лежит рядом со скриптом
_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

import pp_wpf
import pp_stats_window


TITLE = u"Статистика использования"

GROUP_BY_TOOL = 0
GROUP_BY_USER = 1
GROUP_BY_BOTH = 2
GROUP_BY_PHASE = 3

UNKNOWN = u"—"

ALL_MODELS = u"Все модели"

# --- настройки шкалы «фазы модели» ---------------------------------------
#
# PHASE_BUCKETS         на сколько долей режется жизнь модели (для гистограммы)
# IDLE_CAP_SECONDS      пауза между запусками, которая ещё считается работой.
#                       Всё, что дольше, обрезается: если модель месяц не
#                       открывали, этот месяц не должен съедать шкалу. Так
#                       проценты считаются от накопленного РАБОЧЕГО времени,
#                       а не от календарного.
# MIN_RECORDS_PER_MODEL модель с парой запусков шкалы не образует — пропускаем.
PHASE_BUCKETS = 10
IDLE_CAP_SECONDS = 30 * 60
MIN_RECORDS_PER_MODEL = 5

# Столбики гистограммы. Первый символ — «здесь не было ничего».
SPARK_CHARS = u"·▁▂▃▄▅▆▇█"


# ---------------------------------------------------------------------------
# ОБЩЕЕ
# ---------------------------------------------------------------------------

def _value(record, key):
    value = record.get(key)
    if value is None or value == "":
        return UNKNOWN
    return value


def _touch(bucket, stamp):
    u"""Обновляет счётчик и границы периода."""
    bucket["count"] += 1
    if stamp:
        if not bucket["first"] or stamp < bucket["first"]:
            bucket["first"] = stamp
        if not bucket["last"] or stamp > bucket["last"]:
            bucket["last"] = stamp


def parse_stamp(text):
    u"""«2026-08-21T16:41:45» → datetime. Кривая строка даёт None."""
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# МОДЕЛИ
# ---------------------------------------------------------------------------

def model_key(record):
    u"""Ключ группировки по модели — ИМЯ модели, а не путь.

    Путь центральной модели (`model_key` в записи) точнее, но появился только
    в августе 2026: записи до него знают лишь заголовок документа. Группируй мы
    по пути — одна и та же модель разъехалась бы на «до» и «после». Имя же
    pp_usage приводит к общему виду в обоих случаях, поэтому история не рвётся.
    """
    if not isinstance(record, dict):
        return None
    return pp_usage.get_model_label(record)


def list_models(records):
    u"""Список моделей для выпадающего списка: [(подпись, ключ), ...].

    Первым пунктом всегда «Все модели» с ключом None, дальше — по убыванию
    числа запусков.
    """
    buckets = {}
    for record in records:
        key = model_key(record)
        if not key:
            continue
        buckets[key] = buckets.get(key, 0) + 1

    items = sorted(buckets.items(), key=lambda pair: (-pair[1], pair[0]))

    models = [(ALL_MODELS, None)]
    for key, count in items:
        models.append((u"{0}  ({1})".format(key, count), key))
    return models


def filter_by_model(records, key):
    u"""Оставляет записи одной модели. Пустой ключ — вернуть всё."""
    if not key:
        return records
    return [record for record in records if model_key(record) == key]


# ---------------------------------------------------------------------------
# ФАЗА МОДЕЛИ (нормализованное время)
# ---------------------------------------------------------------------------

def build_phases(records):
    u"""Для каждой записи считает её место на шкале жизни модели, 0–100 %.

    Возвращает (пары [(запись, фаза)], сведения о разборе).

    Как считается: записи модели сортируются по времени, между соседними
    берётся пауза, обрезанная сверху до IDLE_CAP_SECONDS. Накопленная сумма
    таких пауз — «сколько рабочего времени модель прожила к этому моменту»;
    делим на итог, получаем проценты. Обрезка нужна, чтобы простой между
    этапами проекта не выдавал себя за работу и не сдвигал всю шкалу.

    Модели короче MIN_RECORDS_PER_MODEL запусков пропускаются: по трём кликам
    шкала получится бессмысленной.
    """
    groups = {}
    for record in records:
        key = model_key(record)
        stamp = parse_stamp(record.get("ts")) if key else None
        if not key or stamp is None:
            continue
        groups.setdefault(key, []).append((stamp, record))

    pairs = []
    info = {"models": 0, "skipped_models": 0, "skipped_records": 0}

    for key in groups:
        items = sorted(groups[key], key=lambda pair: pair[0])

        if len(items) < MIN_RECORDS_PER_MODEL:
            info["skipped_models"] += 1
            info["skipped_records"] += len(items)
            continue

        elapsed = [0.0]
        for index in range(1, len(items)):
            gap = (items[index][0] - items[index - 1][0]).total_seconds()
            if gap < 0:
                gap = 0.0
            elapsed.append(elapsed[-1] + min(gap, float(IDLE_CAP_SECONDS)))

        total = elapsed[-1]
        if total <= 0:
            # все запуски пришлись на одну секунду — шкалы нет
            info["skipped_models"] += 1
            info["skipped_records"] += len(items)
            continue

        info["models"] += 1
        for index, item in enumerate(items):
            pairs.append((item[1], elapsed[index] / total * 100.0))

    return pairs, info


def spark(histogram):
    u"""Гистограмма числами → строка столбиков; масштаб свой у каждой строки."""
    top = max(histogram) if histogram else 0
    if not top:
        return SPARK_CHARS[0] * len(histogram)

    text = u""
    last = len(SPARK_CHARS) - 1
    for value in histogram:
        if not value:
            text += SPARK_CHARS[0]
        else:
            level = int(math.ceil(float(value) / top * last))
            text += SPARK_CHARS[max(1, min(level, last))]
    return text


def aggregate_phases(records):
    u"""Строки режима «По фазам модели»: инструмент → где он живёт на шкале."""
    pairs, info = build_phases(records)

    headers = [(u"Инструмент", 240), (u"Панель", 130), (u"Запусков", 80),
               (u"Средняя фаза, %", 120), (u"Разброс, %", 100),
               (u"Ход модели: начало → конец", 190, u"spark")]

    if not pairs:
        note = (u"Шкала строится только по модели, где набралось хотя бы {0} "
                u"запусков. Пока таких нет — попользуйтесь инструментами, "
                u"учёт идёт сам.").format(MIN_RECORDS_PER_MODEL)
        return headers, [], note

    buckets = {}
    for record, phase in pairs:
        tool = _value(record, "tool")
        if tool not in buckets:
            buckets[tool] = {"panel": UNKNOWN, "panel_ts": u"",
                             "phases": [],
                             "histogram": [0] * PHASE_BUCKETS}
        bucket = buckets[tool]

        # Панель берём из самой свежей записи: инструменты между панелями
        # переезжают, и показывать надо нынешнее место, а не первое попавшееся
        stamp = record.get("ts") or u""
        if stamp >= bucket["panel_ts"]:
            bucket["panel_ts"] = stamp
            bucket["panel"] = _value(record, "panel")

        bucket["phases"].append(phase)

        index = int(phase / 100.0 * PHASE_BUCKETS)
        if index >= PHASE_BUCKETS:
            index = PHASE_BUCKETS - 1
        bucket["histogram"][index] += 1

    rows = []
    for tool in buckets:
        bucket = buckets[tool]
        phases = bucket["phases"]
        count = len(phases)
        average = sum(phases) / count

        # разброс: чем он больше, тем ровнее инструмент размазан по всей работе
        variance = sum([(value - average) ** 2 for value in phases]) / count
        spread = math.sqrt(variance)

        rows.append([tool, bucket["panel"], count,
                     int(round(average)), int(round(spread)),
                     spark(bucket["histogram"])])

    # По возрастанию средней фазы: сверху то, с чего модель начинают
    rows.sort(key=lambda row: row[3])

    note = (u"Шкала: 0 % — первый запуск в модели, 100 % — последний. "
            u"Считается по накопленному рабочему времени: паузы длиннее "
            u"{0} мин в счёт не идут. Моделей в расчёте: {1}.").format(
        IDLE_CAP_SECONDS // 60, info["models"])
    if info["skipped_models"]:
        note += u" Пропущено коротких моделей: {0}.".format(
            info["skipped_models"])
    return headers, rows, note


# ---------------------------------------------------------------------------
# АГРЕГАЦИЯ
# ---------------------------------------------------------------------------

def aggregate(records, group_by):
    u"""Возвращает (заголовки, строки, пояснение к режиму)."""
    if group_by == GROUP_BY_PHASE:
        return aggregate_phases(records)

    buckets = {}

    for record in records:
        if not isinstance(record, dict):
            continue
        tool = _value(record, "tool")
        user = _value(record, "user")
        panel = _value(record, "panel")
        stamp = record.get("ts") or ""

        if group_by == GROUP_BY_TOOL:
            key = tool
        elif group_by == GROUP_BY_USER:
            key = user
        else:
            key = (tool, user)

        if key not in buckets:
            buckets[key] = {"count": 0, "first": stamp, "last": stamp,
                            "panel": panel, "users": set(), "tools": set()}
        bucket = buckets[key]
        bucket["users"].add(user)
        bucket["tools"].add(tool)
        _touch(bucket, stamp)

    rows = []
    if group_by == GROUP_BY_TOOL:
        headers = [(u"Инструмент", 240), (u"Панель", 130), (u"Запусков", 70),
                   (u"Польз.", 60), (u"Первый", 90), (u"Последний", 90)]
        for tool, bucket in buckets.items():
            rows.append([tool, bucket["panel"], bucket["count"],
                         len(bucket["users"]), bucket["first"][:10],
                         bucket["last"][:10]])
    elif group_by == GROUP_BY_USER:
        headers = [(u"Пользователь", 200), (u"Запусков", 80),
                   (u"Инструментов", 100), (u"Первый", 100), (u"Последний", 100)]
        for user, bucket in buckets.items():
            rows.append([user, bucket["count"], len(bucket["tools"]),
                         bucket["first"][:10], bucket["last"][:10]])
    else:
        headers = [(u"Инструмент", 220), (u"Пользователь", 150),
                   (u"Запусков", 80), (u"Первый", 90), (u"Последний", 90)]
        for key, bucket in buckets.items():
            tool, user = key
            rows.append([tool, user, bucket["count"],
                         bucket["first"][:10], bucket["last"][:10]])

    count_index = 1 if group_by == GROUP_BY_USER else 2
    rows.sort(key=lambda r: r[count_index], reverse=True)
    return headers, rows, u""


def overall_summary(records):
    u"""Итоговая строка: всего запусков, инструментов, пользователей, период."""
    tools, users, models, stamps = set(), set(), set(), []
    for record in records:
        if not isinstance(record, dict):
            continue
        tools.add(_value(record, "tool"))
        users.add(_value(record, "user"))
        model = model_key(record)
        if model:
            models.add(model)
        stamp = record.get("ts")
        if stamp:
            stamps.append(stamp)

    text = (u"Всего запусков: {0}    Инструментов: {1}    "
            u"Пользователей: {2}    Моделей: {3}").format(
        len(records), len(tools), len(users), len(models))
    if stamps:
        text += u"    Период: {0} — {1}".format(min(stamps)[:10], max(stamps)[:10])
    return text


# ---------------------------------------------------------------------------
# ТОЧКА ВХОДА
# ---------------------------------------------------------------------------

def main():
    folder = pp_usage.get_log_dir(create=False)
    records = pp_usage.read_records(folder)

    if not records:
        pp_wpf.show_report(
            u"Учёт ведётся с момента установки этой версии: каждая нажатая кнопка "
            u"сразу пишет строку в лог. Попользуйтесь инструментами и загляните "
            u"сюда снова.\n\nПапка логов:\n{0}".format(folder),
            title=u"Записей пока нет",
            subtitle=TITLE
        )
        return

    pp_stats_window.show(_HERE, records, folder, aggregate, overall_summary,
                         list_models(records), filter_by_model)


try:
    main()

except Exception as ex:
    pp_wpf.show_report(
        unicode(ex),
        title=u"Ошибка",
        subtitle=TITLE,
        is_error=True
    )

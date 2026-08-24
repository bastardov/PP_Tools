# AI_обмен — формат обмена ИИ Агент ↔ Revit

Папка обмена между Revit и ИИ-агентом. Тут лежат два файла:

- `model_export.json` — пишет кнопка **Выгрузить** (вкладка «ИИ Агент»).
- `commands.json` — читает кнопка **Загрузить**. Этот файл готовит ИИ-агент.

Единицы: длины — **мм**, площади — **м²**. Координаты в системе проекта
(ось Y = условный «север» проекта; истинный север — в `meta.true_north_deg`).

## model_export.json (выгрузка)

```
{
  "meta": {
    "schema_version": 1,
    "document": "...",
    "exported": "YYYY-MM-DD HH:MM:SS",
    "units": {"length": "mm", "area": "m2"},
    "true_north_deg": <угол или null>,
    "env_params": [ ...имена параметров теплопотерь... ],
    "counts": {"spaces":N,"walls":N,"openings":N,"floors":N}
  },
  "spaces":   [ {id, number, name, level, area_m2, point[x,y,z],
                 bbox{min,max}, polygon[[x,y],...], params{...}} ],
  "walls":    [ {id, is_curtain, type, family, level, width_mm,
                 start[x,y,z], end[x,y,z], normal[x,y], area_m2,
                 bbox, params{...}} ],
  "openings": [ {id, kind("Окно"/"Дверь"), type, family, level,
                 host_id, point[x,y,z], area_m2, bbox, params{...}} ],
  "floors":   [ {id, type, family, level, area_m2, bbox, params{...}} ]
}
```

`params` у каждого элемента — словарь `имя_параметра -> {storage, value, text}`,
где `storage` ∈ Double/String/Integer/ElementId, `value` — сырое значение
(Double — внутренние единицы Revit), `text` — как показано в интерфейсе.

Выгружаемые параметры ограждающих элементов (`env_params`):
`ADSK_Температура в помещении`, `PP_Номер имя помещения`, `ADSK_Размер_Площадь`,
`PP_Ориентация по стороне света`, `PP_Добавка на сторону света`.
У пространств выгружается `ADSK_Температура в помещении`.

## commands.json (загрузка)

```
{
  "commands": [
    {"id": 123456, "params": {
        "PP_Номер имя помещения": "101 Кабинет",
        "ADSK_Температура в помещении": "20 °C",
        "PP_Ориентация по стороне света": "С",
        "PP_Добавка на сторону света": "1.1"
    }}
  ]
}
```

Правила значений:
- Значения задаются **как в интерфейсе Revit** (display). Для числовых
  параметров загрузчик сначала пробует `SetValueString` (с единицами проекта),
  при неудаче пишет как внутреннее значение.
- Текстовые — строкой; сторона света: `С, СВ, В, ЮВ, Ю, ЮЗ, З, СЗ`.
- Пишутся только параметры экземпляра; `read-only` пропускаются.
- Остальные параметры элементов не трогаются.

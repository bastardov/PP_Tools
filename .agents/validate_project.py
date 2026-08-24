# -*- coding: utf-8 -*-
u"""Единая автономная проверка PP_Tools без запуска Revit.

Обычный запуск возвращает ошибку только для дефектов, способных сломать загрузку
плагина или сохранность настроек. Накопленные замечания старого кода выводятся
отдельно. Ключ --strict считает замечания ошибками — режим для постепенной чистки.
"""

from __future__ import print_function

import argparse
import ast
import importlib.util
import io
import json
import os
import re
import struct
import sys
import tempfile
import token
import tokenize


AGENTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(AGENTS_DIR)
LIB_DIR = os.path.join(ROOT, "lib")

SKIP_DIRS = set(["BACKUPS", ".git", "telemetry", "_logs", "AI_обмен"])


class Findings(object):
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.stats = {}

    def error(self, message):
        self.errors.append(message)

    def warning(self, message):
        self.warnings.append(message)


def rel(path):
    return os.path.relpath(path, ROOT)


def read_text(path):
    with io.open(path, "r", encoding="utf-8-sig") as handle:
        return handle.read()


def discover():
    py_paths = []
    xaml_paths = []
    pushbuttons = []
    panels = []
    tabs = []

    for folder, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        base = os.path.basename(folder)
        if base.endswith(".pushbutton"):
            pushbuttons.append(folder)
        elif base.endswith(".panel"):
            panels.append(folder)
        elif base.endswith(".tab"):
            tabs.append(folder)

        for name in filenames:
            path = os.path.join(folder, name)
            if name.endswith(".py") and not path.startswith(AGENTS_DIR + os.sep):
                py_paths.append(path)
            elif name.endswith(".xaml"):
                xaml_paths.append(path)

    return {
        "py": sorted(py_paths),
        "xaml": sorted(xaml_paths),
        "pushbuttons": sorted(pushbuttons),
        "panels": sorted(panels),
        "tabs": sorted(tabs),
    }


def bundle_value(text, key):
    match = re.search(r"(?m)^{}\s*:\s*(.+?)\s*$".format(re.escape(key)), text)
    if not match:
        return None
    return match.group(1).strip().strip("\"'")


def png_info(path):
    with io.open(path, "rb") as handle:
        data = handle.read()
    if len(data) < 26 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(u"не является PNG")
    width, height = struct.unpack(">II", data[16:24])
    color_type = data[25]
    has_alpha = color_type in (4, 6) or b"tRNS" in data
    return width, height, has_alpha


def check_bundles_and_assets(paths, out):
    for folder in paths["tabs"] + paths["panels"]:
        bundle = os.path.join(folder, "bundle.yaml")
        if not os.path.isfile(bundle):
            out.error(u"Нет bundle.yaml: {}".format(rel(folder)))
            continue
        title = bundle_value(read_text(bundle), "title")
        if not title:
            out.error(u"Нет title в {}".format(rel(bundle)))
        elif folder.endswith(".panel") and re.match(r"^\d{2}_", title):
            out.error(u"Номер панели попал в title: {}".format(rel(bundle)))

    for folder in paths["pushbuttons"]:
        script_path = os.path.join(folder, "script.py")
        icon_path = os.path.join(folder, "icon.png")
        bundle_path = os.path.join(folder, "bundle.yaml")

        if not os.path.isfile(script_path):
            out.error(u"Нет script.py: {}".format(rel(folder)))
        else:
            head = u"\n".join(read_text(script_path).splitlines()[:10])
            if "pp_usage.log(__file__)" not in head:
                out.error(u"Нет учёта использования в начале: {}".format(rel(script_path)))

        if not os.path.isfile(icon_path):
            out.error(u"Нет icon.png: {}".format(rel(folder)))
        else:
            try:
                width, height, has_alpha = png_info(icon_path)
                if (width, height) != (32, 32):
                    out.warning(u"Иконка не 32×32: {} ({}×{})".format(
                        rel(icon_path), width, height))
                if not has_alpha:
                    out.warning(u"У иконки не найден прозрачный канал: {}".format(
                        rel(icon_path)))
            except Exception as ex:
                out.error(u"Некорректная иконка {}: {}".format(rel(icon_path), ex))

        if not os.path.isfile(bundle_path):
            out.warning(u"Нет bundle.yaml у кнопки: {}".format(rel(folder)))
        else:
            text = read_text(bundle_path)
            if not bundle_value(text, "title"):
                out.warning(u"Нет title: {}".format(rel(bundle_path)))
            if not re.search(r"(?m)^tooltip\s*:", text):
                out.warning(u"Нет tooltip: {}".format(rel(bundle_path)))

    for name in ("layout", "_layout"):
        for panel in paths["panels"]:
            layout_path = os.path.join(panel, name)
            if not os.path.isfile(layout_path):
                continue
            for item in read_text(layout_path).splitlines():
                item = item.strip()
                if not item:
                    continue
                candidates = [
                    os.path.join(panel, item),
                    os.path.join(panel, item + ".pushbutton"),
                    os.path.join(panel, item + ".stack"),
                ]
                if not any(os.path.exists(path) for path in candidates):
                    out.warning(u"В {} указан отсутствующий элемент: {}".format(
                        rel(layout_path), item))


def load_wpf_validator():
    path = os.path.join(AGENTS_DIR, "validate_wpf.py")
    spec = importlib.util.spec_from_file_location("pp_validate_wpf", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def controller_paths(xaml_path, py_paths):
    folder = os.path.dirname(xaml_path)
    name = os.path.basename(xaml_path)
    by_folder = [path for path in py_paths if os.path.dirname(path) == folder]
    if name == "ui.xaml":
        return by_folder

    stem = os.path.splitext(name)[0]
    direct = os.path.join(folder, stem + ".py")
    if direct in py_paths:
        return [direct]

    if stem in ("pp_check_setup", "pp_check_report"):
        controller = os.path.join(folder, "pp_check_windows.py")
        return [controller] if controller in py_paths else []
    return []


def check_wpf_and_python(paths, out):
    validator = load_wpf_validator()
    validator.reset()
    trees = validator.check_py_syntax(paths["py"])
    roots = validator.check_xaml_wellformed(paths["xaml"])

    theme_path = os.path.join(LIB_DIR, "pp_theme.xaml")
    keys = validator.theme_keys(theme_path)
    local_keys = {}
    for path, root in roots.items():
        local_keys[path] = set(
            element.get(validator.XKEY)
            for element in root.iter()
            if element.get(validator.XKEY)
        )
        validator.check_setter_targetname(path, root)

    validator.check_static_resources(paths["xaml"], keys, local_keys)
    validator.check_code_resource_keys(paths["py"], keys)
    validator.check_gridlength(paths["py"])

    xamls_by_controller = {}
    controllers_by_xaml = {}
    for xaml_path in roots:
        for controller in controller_paths(xaml_path, paths["py"]):
            if controller in trees:
                xamls_by_controller.setdefault(controller, []).append(xaml_path)
                controllers_by_xaml.setdefault(xaml_path, []).append(controller)

    # Один контроллер может обслуживать несколько разметок. Например,
    # pp_check_windows.py работает и с setup, и с report — имена нужны общие.
    for controller, xaml_group in xamls_by_controller.items():
        all_names = set()
        for xaml_path in xaml_group:
            all_names |= validator.xaml_names(roots[xaml_path])
        validator.check_findname([controller], all_names)

    for xaml_path, controllers in controllers_by_xaml.items():
        props = set()
        for controller in controllers:
            props |= validator.vm_properties(trees[controller])
        names = validator.xaml_names(roots[xaml_path])
        validator.check_bindings(
            xaml_path, read_text(xaml_path), names, props
        )

    for message in validator.PROBLEMS:
        out.error(message)

    py3_only = 0
    bare_except = 0
    legacy_alerts = 0
    missing_unicode = {}
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                py3_only += 1
                out.error(u"f-string несовместим с IronPython 2.7: {}:{}".format(
                    rel(path), node.lineno))
            if isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.NamedExpr,
                                 ast.AnnAssign, ast.Nonlocal, ast.YieldFrom)):
                py3_only += 1
                out.error(u"Python 3 синтаксис: {}:{}".format(rel(path), node.lineno))
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                bare_except += 1
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if getattr(node, "returns", None) is not None \
                        or any(getattr(arg, "annotation", None) is not None
                               for arg in node.args.args) \
                        or getattr(node.args, "kwonlyargs", []):
                    py3_only += 1
                    out.error(u"Аннотации или keyword-only аргументы Python 3: {}:{}"
                              .format(rel(path), node.lineno))

        text = read_text(path)
        if re.search(r"(?m)^\s*(?:from\s+pathlib\s+import|import\s+pathlib\b)", text):
            out.error(u"pathlib несовместим с правилами проекта: {}".format(rel(path)))
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "forms.alert" in line:
                legacy_alerts += 1

        # В Python 2 обычная строка с кириллицей является байтовой. Проверка
        # работает токенами, поэтому комментарии не дают ложных срабатываний.
        try:
            with io.open(path, "rb") as handle:
                tokens = list(tokenize.tokenize(handle.readline))
            previous = None
            count = 0
            for item in tokens:
                if item.type == token.STRING \
                        and re.search(u"[А-Яа-яЁё]", item.string):
                    prefix = re.match(r"(?i)^([rubf]*)", item.string).group(1).lower()
                    adjacent_ur = previous is not None \
                        and previous.type == token.NAME \
                        and previous.string.lower() in ("u", "ur", "ru") \
                        and previous.end == item.start
                    if "u" not in prefix and not adjacent_ur:
                        count += 1
                if item.type not in (
                        tokenize.ENCODING, tokenize.NL, tokenize.NEWLINE,
                        token.INDENT, token.DEDENT):
                    previous = item
            if count:
                missing_unicode[rel(path)] = count
        except Exception:
            pass

    if bare_except:
        out.warning(u"Найдено bare except: {}. Проверять постепенно, без массовой замены."
                    .format(bare_except))
    if legacy_alerts:
        out.warning(u"Остались вызовы forms.alert: {}.".format(legacy_alerts))
    if missing_unicode:
        total = sum(missing_unicode.values())
        top = sorted(missing_unicode.items(), key=lambda item: item[1], reverse=True)[:5]
        top_text = u"; ".join(u"{} — {}".format(path, count)
                              for path, count in top)
        out.warning(
            u"Русских строк без u-префикса: {} в {} файлах. Больше всего: {}"
            .format(total, len(missing_unicode), top_text)
        )
    out.stats["python3_only"] = py3_only
    out.stats["bare_except"] = bare_except
    out.stats["forms_alert"] = legacy_alerts


def import_settings_module():
    path = os.path.join(LIB_DIR, "pp_settings.py")
    spec = importlib.util.spec_from_file_location("pp_settings_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_settings_recovery(out):
    module = import_settings_module()
    original_get_path = module.get_settings_path
    original_log = module._settings_log
    handle, path = tempfile.mkstemp(prefix="pp_settings_check_", suffix=".json")
    os.close(handle)
    os.remove(path)
    folder = os.path.dirname(path)
    prefix = os.path.basename(path)
    try:
        module.get_settings_path = lambda: path
        module._settings_log = lambda message: None
        try:
            first = module._default_copy()
            first["pipe_pair_distance_mm"] = 610.0
            first["tag_size_active"] = u"Проверка UTF-8"
            module.save_settings(first)

            second = module._default_copy()
            second["pipe_pair_distance_mm"] = 620.0
            module.save_settings(second)

            backup_path = module.get_settings_backup_path(path)
            if not os.path.isfile(backup_path):
                out.error(u"Тест настроек: после второй записи не создан .bak")

            with io.open(path, "wb") as handle:
                handle.write(b"{broken json")

            recovered = module.load_settings()
            if recovered.get("pipe_pair_distance_mm") != 610.0:
                out.error(u"Тест настроек: повреждённый JSON не восстановлен из .bak")
            if not os.path.isfile(path):
                out.error(u"Тест настроек: основной JSON не восстановлен")
            archives = [name for name in os.listdir(folder)
                        if ".invalid_" in name]
            if not archives:
                out.error(u"Тест настроек: повреждённый JSON не сохранён отдельно")

            bad_import = path + ".bad_import.json"
            with io.open(bad_import, "w", encoding="utf-8") as handle:
                json.dump({"show_warning_report": "yes"}, handle)
            try:
                module.import_settings(bad_import)
                out.error(u"Тест настроек: импорт принял неверный тип значения")
            except ValueError:
                pass

            # Прямая запись тоже обязана сначала сохранить повреждённый main,
            # а не перетереть им рабочую резервную копию.
            with io.open(path, "wb") as handle:
                handle.write(b"{broken again")
            third = module._default_copy()
            third["pipe_pair_distance_mm"] = 630.0
            module.save_settings(third)
            if module.load_settings().get("pipe_pair_distance_mm") != 630.0:
                out.error(u"Тест настроек: прямая запись после повреждения не восстановилась")
        finally:
            module.get_settings_path = original_get_path
            module._settings_log = original_log
    finally:
        # Удаляем только файлы с уникальным префиксом текущего теста.
        for name in os.listdir(folder):
            if not name.startswith(prefix):
                continue
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                os.remove(candidate)


def print_report(paths, out, strict=False):
    print(u"PP_Tools — автономная проверка")
    print(u"Корень: {}".format(ROOT))
    print(u"Проверено: вкладок {}, панелей {}, кнопок {}, Python {}, XAML {}"
          .format(len(paths["tabs"]), len(paths["panels"]),
                  len(paths["pushbuttons"]), len(paths["py"]),
                  len(paths["xaml"])))

    if out.warnings:
        print(u"\n--- ЗАМЕЧАНИЯ ({}) ---".format(len(out.warnings)))
        for message in out.warnings:
            print(u"  ~ " + message)

    if out.errors:
        print(u"\n--- ОШИБКИ ({}) ---".format(len(out.errors)))
        for message in out.errors:
            print(u"  ! " + message)

    if not out.errors and not out.warnings:
        print(u"\nВсе проверки пройдены без замечаний.")
    elif not out.errors:
        print(u"\nКритических ошибок нет.")

    if out.errors:
        return 1
    if strict and out.warnings:
        return 2
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="PP_Tools project preflight without Revit"
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="treat accumulated project warnings as failures"
    )
    args = parser.parse_args(argv)

    paths = discover()
    out = Findings()
    check_bundles_and_assets(paths, out)
    check_wpf_and_python(paths, out)
    try:
        check_settings_recovery(out)
    except Exception as ex:
        out.error(u"Автономный тест pp_settings завершился с ошибкой: {}".format(ex))

    return print_report(paths, out, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""UniPay: получить открытую работу по номеру. Только стандартная библиотека."""
# Only public data is downloaded; no downloaded Python is executed.
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

VARIANTS = frozenset(('customer', 'card', 'account', 'merchant', 'authorization',
                     'limits', 'fraud', 'fx', 'ledger', 'notification', 'audit',
                     'reporting', 'clearing', 'settlement'))
ROOT_FILES = frozenset(('README.md', 'START_HERE.md', 'check.py', 'requirements.txt',
                        '.gitignore', '.gitattributes', '.unipay/course.json'))
RUNTIME_FILES = frozenset(('check.py', 'requirements.txt', '.gitattributes'))
LOCK = '.unipay-issue.lock'


class DeliveryError(Exception):
    """Отказ выдачи: прежние решения нельзя исправлять или затирать автоматически."""


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def no_links(path):
    # Проверка исходного пути до нормализации не прячет ссылку за resolve().
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise DeliveryError(f'Ссылки и junction не поддерживаются: {part}')


def absolute_folder(path):
    path = Path(path).absolute()
    no_links(path)
    path = Path(os.path.abspath(path))
    if path.exists() and not path.is_dir():
        raise DeliveryError(f'Нужна папка: {path}')
    return path


def relative_file(name):
    if not isinstance(name, str) or not name or '\\' in name:
        raise DeliveryError(f'Недопустимый путь: {name!r}')
    parts = name.split('/')
    for part in parts:
        if (not re.fullmatch(r'[A-Za-z0-9_.-]+', part) or part in ('.', '..')
                or part.lower() == '.git' or part.endswith('.')
                or re.fullmatch(r'(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?', part, re.I)):
            raise DeliveryError(f'Недопустимый путь: {name!r}')
    if PurePosixPath(name).is_absolute():
        raise DeliveryError(f'Абсолютный путь в выдаче: {name}')
    return name


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise DeliveryError(f'Не удалось прочитать {path}: {error}') from error


def course(variant):
    return {'format': 1, 'course': 'unipay-oop', 'variant': variant}


def load_bundle(directory):
    """Прочитать и проверить содержимое; не исполняет ни один файл payload."""
    directory = absolute_folder(directory)
    no_links(directory / 'ISSUE.json')
    manifest = read_json(directory / 'ISSUE.json')
    if (not isinstance(manifest, dict) or manifest.get('format') != 1
            or manifest.get('course') != 'unipay-oop'
            or not isinstance(manifest.get('variant'), str) or manifest['variant'] not in VARIANTS
            or type(manifest.get('lab')) is not int or manifest['lab'] not in range(1, 7)):
        raise DeliveryError('Неизвестный формат, вариант или номер работы')
    for key in ('source_commit', 'source_tree'):
        if not re.fullmatch('[0-9a-f]{40}', str(manifest.get(key))):
            raise DeliveryError(f'Не указан полный Git SHA: {key}')
    hashes = manifest.get('files')
    runtime = manifest.get('runtime_sha256')
    if (not isinstance(hashes, dict) or not hashes or not isinstance(runtime, dict)
            or set(runtime) != RUNTIME_FILES):
        raise DeliveryError('Некорректный перечень файлов/требований к запускателю')
    lab = f"lab{manifest['lab']:02d}"
    prefix = f'labs/{lab}/'
    workflow = f'.github/workflows/{lab}.yml'
    for name, value in [*hashes.items(), *runtime.items()]:
        relative_file(name)
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
            raise DeliveryError(f'Некорректная SHA-256: {name}')
    for name in hashes:
        if not (name.startswith(prefix) or name == workflow
                or (manifest['lab'] == 1 and name in ROOT_FILES)):
            raise DeliveryError(f'Файл вне выдаваемой работы: {name}')
    required = {prefix + 'TASK.md', prefix + 'app/api.py', prefix + 'tests/test_contract.py', workflow}
    if manifest['lab'] == 1:
        required.update(ROOT_FILES)
    if not required <= hashes.keys():
        raise DeliveryError('В выдаче отсутствуют обязательные файлы')
    payload = directory / 'payload'
    no_links(payload)
    if not payload.is_dir():
        raise DeliveryError('Нет папки payload')
    actual = {}
    for root, dirs, files in os.walk(payload, followlinks=False):
        for name in [*dirs, *files]:
            no_links(Path(root) / name)
        for name in files:
            file = Path(root) / name
            key = relative_file(file.relative_to(payload).as_posix())
            if not file.is_file():
                raise DeliveryError(f'Не обычный файл: {file}')
            actual[key] = file.read_bytes()
    if set(actual) != set(hashes):
        raise DeliveryError('Состав payload не совпадает с ISSUE.json')
    for name, data in actual.items():
        if digest(data) != hashes[name]:
            raise DeliveryError(f'Не совпадает контрольная сумма: {name}')
    if manifest['lab'] == 1:
        if actual['.unipay/course.json'] != json_bytes(course(manifest['variant'])):
            raise DeliveryError('Вариант в course.json не совпадает с выдачей')
        if any(hashes[name] != runtime[name] for name in RUNTIME_FILES):
            raise DeliveryError('Начальные файлы запуска не соответствуют манифесту')
    return manifest, actual


def target_path(root, name):
    path = root / relative_file(name)
    no_links(path)
    for parent in path.parents:
        if parent == root:
            break
        if parent.exists() and not parent.is_dir():
            raise DeliveryError(f'Путь занят файлом: {parent}')
    return path


def plan(root, manifest, data, own_lock=False, bootstrap_files=None):
    """Сначала проверяем все условия. Ничего не записывается."""
    if (root / LOCK).exists() and not own_lock:
        raise DeliveryError('Папка занята другой выдачей или остался её lock. Разберите причину; файл не удаляется автоматически.')
    number, variant = manifest['lab'], manifest['variant']
    receipt = f'.unipay/issues/lab{number:02d}.json'
    receipt_path = target_path(root, receipt)
    course_path = target_path(root, '.unipay/course.json')
    if course_path.exists():
        if read_json(course_path) != course(variant):
            raise DeliveryError('Репозиторий принадлежит другому варианту/формату курса')
        for name, expected in manifest['runtime_sha256'].items():
            path = target_path(root, name)
            if not path.is_file() or digest(path.read_bytes()) != expected:
                raise DeliveryError(f'Файл запуска изменён или несовместим: {name}. Автоматическая замена запрещена.')
        if receipt_path.exists():
            if read_json(receipt_path) == manifest:
                return {}  # Повтор выдачи никогда не восстанавливает starter поверх решения.
            raise DeliveryError('Этот номер уже выдан в другой версии. Нужна отдельная согласованная правка, не перезапись.')
        if number == 1:
            raise DeliveryError('Незавершённая первая выдача: есть course.json без квитанции')
        for previous in range(1, number):
            path = target_path(root, f'.unipay/issues/lab{previous:02d}.json')
            value = read_json(path)
            if (not isinstance(value, dict) or value.get('variant') != variant
                    or value.get('lab') != previous or value.get('course') != 'unipay-oop'):
                raise DeliveryError('Не совпадают сведения о предыдущей выдаче')
    else:
        if number != 1:
            raise DeliveryError('Сначала примените выдачу ЛР1. Старый ручной экспорт автоматически не мигрируется.')
        existing = {p.name for p in root.iterdir()} if root.exists() else set()
        bootstrap_files = bootstrap_files or {}
        if set(bootstrap_files) - {'unipay.py'}:
            raise DeliveryError('Разрешён только сам загрузчик unipay.py')
        for name, expected in bootstrap_files.items():
            path = target_path(root, name)
            if not path.is_file() or digest(path.read_bytes()) != expected:
                raise DeliveryError('Загрузчик в целевой папке изменился во время подготовки')
        if existing - {'.git', *bootstrap_files, *([LOCK] if own_lock else [])}:
            raise DeliveryError('Первая выдача требует пустую папку (допускаются .git и проверенный unipay.py). Ничего не перезаписано.')
    lab_path = target_path(root, f'labs/lab{number:02d}')
    if lab_path.exists():
        raise DeliveryError(f'{lab_path} уже существует без квитанции. Не перезаписываем даже пустую папку.')
    changes = dict(data)
    changes[receipt] = json_bytes(manifest)
    for name in changes:
        if target_path(root, name).exists():
            raise DeliveryError(f'Коллизия: {name} уже существует. Ничего не перезаписано.')
    return changes


def create_parents(path, root, created):
    if path == root or path.exists():
        return
    create_parents(path.parent, root, created)
    path.mkdir()  # Не заменяет и не принимает чужой файл/ссылку.
    created.append(path)


def apply_bundle(bundle, target, apply=False, *, bootstrap_files=None):
    manifest, data = load_bundle(bundle)
    root = absolute_folder(target)
    changes = plan(root, manifest, data, bootstrap_files=bootstrap_files)
    if not apply or not changes:
        return {'variant': manifest['variant'], 'lab': manifest['lab'], 'target': str(root),
                'files': sorted(changes), 'applied': False, 'already_applied': not changes}
    root.mkdir(parents=True, exist_ok=True)
    no_links(root)
    lock = root / LOCK
    try:
        handle = lock.open('xb')
    except FileExistsError as error:
        raise DeliveryError('Одновременно уже выполняется другая выдача') from error
    created_files, created_dirs = [], []
    try:
        with handle:
            handle.write(b'UniPay delivery in progress\n')
        # Повторный preflight после захвата lock, затем только создание новых файлов.
        changes = plan(root, manifest, data, own_lock=True, bootstrap_files=bootstrap_files)
        for name, content in changes.items():  # Квитанция всегда последняя.
            path = target_path(root, name)
            create_parents(path.parent, root, created_dirs)
            with path.open('xb') as output:
                created_files.append(path)
                output.write(content)
    except BaseException:
        # Откат обычного сбоя записи; не удаляются файлы прошлых работ.
        for path in reversed(created_files):
            path.unlink()
        for directory in reversed(created_dirs):
            directory.rmdir()
        raise
    finally:
        lock.unlink()
    return {'variant': manifest['variant'], 'lab': manifest['lab'], 'target': str(root),
            'files': sorted(changes), 'applied': True, 'already_applied': not changes}






# === Публичный источник и интерфейс ===
import argparse
import lzma
import json
from pathlib import Path
import re
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

BASE_URL = 'https://raw.githubusercontent.com/kandrusyak/unipay-course/main/'
CLIENT_VERSION = 1
MAX_CATALOG = 512 * 1024
MAX_DOWNLOAD = 4 * 1024 * 1024
MAX_EXPANDED = 8 * 1024 * 1024
VARIANT_ORDER = ('customer', 'card', 'account', 'merchant', 'authorization', 'limits',
                 'fraud', 'fx', 'ledger', 'notification', 'audit', 'reporting', 'clearing', 'settlement')


def json_data(data):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Повтор ключа JSON')
            result[key] = value
        return result
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=unique)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise DeliveryError('Повреждённый JSON выдачи') from error


def public_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'raw.githubusercontent.com'
            or not parsed.path.startswith('/kandrusyak/unipay-course/')
            or parsed.query or parsed.fragment):
        raise DeliveryError('Загрузка разрешена только из публичного unipay-course по HTTPS')
    return url


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_data(path, limit):
    relative_file(path)
    url = public_url(BASE_URL + path)
    request = Request(url, headers={'User-Agent': 'UniPay-Lab-Downloader/1', 'Accept': '*/*'})
    try:
        with build_opener(PublicRedirect()).open(request, timeout=20) as response:
            public_url(response.geturl())
            raw = response.headers.get('Content-Length')
            if raw is not None and (not raw.isdigit() or int(raw) > limit):
                raise DeliveryError('Размер ответа превышает допустимый')
            data = response.read(limit + 1)
    except HTTPError as error:
        raise DeliveryError(f'GitHub вернул HTTP {error.code}. Работа может быть ещё не опубликована; повторите позже.') from error
    except (URLError, OSError) as error:
        raise DeliveryError(f'Не удалось скачать материалы: {error}. Проверьте сеть/прокси. Файлы работы не изменены.') from error
    if len(data) > limit:
        raise DeliveryError('Слишком большой ответ сервера')
    return data


def load_catalog(fetch=fetch_data):
    data = fetch('catalog.json', MAX_CATALOG)
    if len(data) > MAX_CATALOG:
        raise DeliveryError('Слишком большой каталог')
    value = json_data(data)
    if (not isinstance(value, dict) or type(value.get('format')) is not int or value['format'] != 1
            or value.get('course') != 'unipay-oop' or value.get('variants') != list(VARIANT_ORDER)
            or type(value.get('min_client')) is not int or value['min_client'] < 1
            or not isinstance(value.get('labs'), dict)):
        raise DeliveryError('Неизвестный формат публичного каталога')
    if value['min_client'] > CLIENT_VERSION:
        raise DeliveryError('Нужна новая версия unipay.py: скачайте её с того же репозитория')
    for number, entry in value['labs'].items():
        if number not in tuple(str(n) for n in range(1, 7)) or not isinstance(entry, dict):
            raise DeliveryError('Некорректный номер в каталоге')
        sha = entry.get('sha256')
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
            raise DeliveryError('В каталоге нет контрольной суммы пакета')
        path = entry.get('path')
        if path != f'releases/lab{int(number):02d}-{sha}.json.xz':
            raise DeliveryError('Путь выдачи не соответствует её номеру и контрольной сумме')
        if type(entry.get('size')) is not int or not 0 < entry['size'] <= MAX_DOWNLOAD:
            raise DeliveryError('Недопустимый размер выдачи')
        for key in ('source_commit', 'source_tree'):
            if not re.fullmatch('[0-9a-f]{40}', str(entry.get(key))):
                raise DeliveryError('В каталоге нет версии источников')
    return value


def unpack_release(data, entry, lab, variant):
    if len(data) != entry['size'] or digest(data) != entry['sha256']:
        raise DeliveryError('Контрольная сумма скачанного пакета не совпала. Ничего не установлено.')
    try:
        compressed = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=64 * 1024 * 1024)
        expanded = compressed.decompress(data, max_length=MAX_EXPANDED + 1)
    except (lzma.LZMAError, EOFError) as error:
        raise DeliveryError('Повреждённый сжатый пакет') from error
    if len(expanded) > MAX_EXPANDED:
        raise DeliveryError('Распакованный пакет слишком велик')
    if not compressed.eof or compressed.unused_data:
        raise DeliveryError('Оборванный пакет или лишние сжатые данные')
    release = json_data(expanded)
    if (not isinstance(release, dict) or release.get('course') != 'unipay-oop'
            or type(release.get('lab')) is not int or release['lab'] != lab
            or not isinstance(release.get('variants'), dict)
            or set(release['variants']) != set(VARIANTS)):
        raise DeliveryError('Состав пакета не соответствует каталогу')
    bundle = release['variants'][variant]
    if not isinstance(bundle, dict) or set(bundle) != {'manifest', 'payload'}:
        raise DeliveryError('Неверная форма выдачи варианта')
    manifest, payload = bundle['manifest'], bundle['payload']
    if (not isinstance(manifest, dict) or manifest.get('variant') != variant
            or manifest.get('lab') != lab or type(manifest.get('lab')) is not int
            or not isinstance(payload, dict) or not payload):
        raise DeliveryError('В пакете указан другой вариант или номер')
    for key in ('source_commit', 'source_tree'):
        if manifest.get(key) != entry[key]:
            raise DeliveryError('Версия пакета не совпадает с каталогом')
    for path, text in payload.items():
        relative_file(path)
        if not isinstance(text, str):
            raise DeliveryError('Файлы должны содержать текст UTF-8')
    return manifest, payload


def saved_variant(target):
    path = target / '.unipay/course.json'
    no_links(path)
    if not path.exists():
        return None
    value = read_json(path)
    if (not isinstance(value, dict) or value.get('variant') not in VARIANTS
            or value != course(value['variant'])):
        raise DeliveryError('Некорректные сведения о варианте в .unipay/course.json')
    return value['variant']


def choose_variant(value):
    if value is None:
        return None
    value = value.strip().lower()
    if value.isdigit() and 1 <= int(value) <= len(VARIANT_ORDER):
        return VARIANT_ORDER[int(value) - 1]
    if value not in VARIANTS:
        raise DeliveryError('Укажите название варианта или его номер 1–14 (список: --list)')
    return value


def bootstrap_loader(target):
    # Исключение из правила пустой папки — только сам выполняющийся файл,
    # а не произвольное имя/хеш из сетевого манифеста.
    script = Path(__file__).absolute()
    no_links(script)
    if script.name == 'unipay.py' and script.parent == target:
        return {'unipay.py': digest(script.read_bytes())}
    return {}


def receive(lab, variant, target, *, dry_run=False, yes=False, fetch=fetch_data, confirm=None, bootstrap=None):
    if type(lab) is not int or lab not in range(1, 7) or variant not in VARIANTS:
        raise DeliveryError('Неизвестный вариант или номер')
    target = absolute_folder(target)
    saved = saved_variant(target)
    if saved is not None and saved != variant:
        raise DeliveryError(f'В этом репозитории выбран {saved}, а не {variant}; смена варианта запрещена')
    catalog = load_catalog(fetch)
    if str(lab) not in catalog['labs']:
        opened = ', '.join(sorted(catalog['labs'], key=int)) or 'пока нет'
        raise DeliveryError(f'ЛР{lab} ещё не опубликована. Доступны: {opened}. Получите её после открытия преподавателем.')
    entry = catalog['labs'][str(lab)]
    manifest, payload = unpack_release(fetch(entry['path'], MAX_DOWNLOAD), entry, lab, variant)
    # Временная папка содержит только данные. Python из сети никогда не импортируется.
    with tempfile.TemporaryDirectory(prefix='unipay-download-') as temporary:
        root = Path(temporary)
        (root / 'ISSUE.json').write_bytes(json_bytes(manifest))
        for name, text in payload.items():
            path = root / 'payload' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode('utf-8'))
        load_bundle(root)
        result = apply_bundle(root, target, bootstrap_files=bootstrap)
        if result['already_applied']:
            print(f'ЛР{lab} уже получена. Решение не перезаписано.')
            return result
        print(f'{variant} · ЛР{lab} · источник {manifest["source_commit"][:12]}')
        print(f'Папка: {target}\nБудет добавлено файлов: {len(result["files"])}')
        if dry_run:
            for name in result['files']:
                print('  + ' + name)
            print('Предварительный просмотр: файлы не записаны.')
            return result
        if not yes:
            if confirm is None:
                if not sys.stdin.isatty():
                    raise DeliveryError('Для неинтерактивного запуска добавьте --yes; для просмотра --dry-run')
                confirm = input
            if confirm('Добавить работу, не меняя прежние файлы? [д/Н] ').strip().lower() not in ('д', 'да', 'y', 'yes'):
                print('Отменено. Файлы не изменены.')
                return result
        result = apply_bundle(root, target, apply=True, bootstrap_files=bootstrap)
    print(f'Готово. Задание: labs/lab{lab:02d}/TASK.md')
    print(f'Проверка: python check.py lab{lab:02d}')
    print('Git-коммиты, push, установка библиотек и запуск кода не выполнялись.')
    return result


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description='Получить открытую лабораторную UniPay из публичного репозитория.')
    parser.add_argument('lab', type=int, nargs='?', choices=range(1, 7), help='Номер лабораторной 1–6')
    parser.add_argument('--variant', help='Название или номер варианта; указывается при первой выдаче')
    parser.add_argument('--target', type=Path, default=Path('.'), help='Корень своего клона; по умолчанию текущая папка')
    parser.add_argument('--list', action='store_true', help='Показать варианты и открытые номера')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Проверить и показать добавления без записи')
    mode.add_argument('--yes', action='store_true', help='Подтвердить добавление без интерактивного вопроса')
    args = parser.parse_args()
    try:
        if sys.version_info < (3, 13):
            raise DeliveryError('Для курса нужен Python 3.13 или новее; используйте установленный Python курса')
        if args.list:
            catalog = load_catalog()
            print('Варианты:')
            for number, name in enumerate(VARIANT_ORDER, 1):
                print(f'  {number:2}: {name}')
            print('Открыты лабораторные: ' + (', '.join(sorted(catalog['labs'], key=int)) or 'пока нет'))
            return 0
        if args.lab is None:
            parser.error('Укажите номер, например: python unipay.py 1 --variant card, или --list')
        target = absolute_folder(args.target)
        variant = choose_variant(args.variant) or saved_variant(target)
        if variant is None:
            if not sys.stdin.isatty():
                raise DeliveryError('При первом запуске укажите --variant card (или свой вариант)')
            print(', '.join(f'{i}={v}' for i, v in enumerate(VARIANT_ORDER, 1)))
            variant = choose_variant(input('Вариант, назначенный преподавателем: '))
        receive(args.lab, variant, target, dry_run=args.dry_run, yes=args.yes,
                bootstrap=bootstrap_loader(target))
    except (DeliveryError, OSError, EOFError) as error:
        parser.exit(2, f'Получение остановлено: {error}\n')
    except KeyboardInterrupt:
        parser.exit(130, '\nПрервано пользователем.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

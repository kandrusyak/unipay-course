"""Включается в один unipay.py; обновляет только памятку при явном --update-guide."""
GUIDE_FILES = frozenset(('START_HERE.md', 'START_HERE.pdf'))


def public_file_bytes(name, value):
    """Текстовый формат остаётся прежним; бинарным может быть только PDF памятки."""
    if isinstance(value, str):
        data = value.encode('utf-8')
    elif (name == 'START_HERE.pdf' and isinstance(value, dict)
          and set(value) == {'encoding', 'data'} and value['encoding'] == 'base64'
          and isinstance(value['data'], str)):
        if len(value['data']) > MAX_DOWNLOAD * 2:
            raise DeliveryError('PDF превышает предел загрузки.')
        try:
            data = base64.b64decode(value['data'], validate=True)
        except (ValueError, binascii.Error) as error:
            raise DeliveryError('Некорректная упаковка PDF.') from error
        if not data.startswith(b'%PDF-') or not data.rstrip().endswith(b'%%EOF'):
            raise DeliveryError('Полученный файл не является PDF памятки.')
    else:
        raise DeliveryError('Неподдерживаемый формат файла: ' + name)
    if len(data) > MAX_DOWNLOAD:
        raise DeliveryError('Файл превышает предел загрузки.')
    return data


def guide_plan(root, manifest, data, own_lock=False):
    """Сопоставить старую квитанцию с разрешённой правкой; не читать решения."""
    if manifest['lab'] != 1:
        raise DeliveryError('Обновление памятки относится только к ЛР1.')
    if (root / LOCK).exists() and not own_lock:
        raise DeliveryError('Папка занята другой установкой. Ничего не изменено.')
    if read_json(target_path(root, '.unipay/course.json')) != course(manifest['variant']):
        raise DeliveryError('Не совпадает вариант курса.')
    receipt_name = '.unipay/issues/lab01.json'
    receipt_path = target_path(root, receipt_name)
    before_receipt = receipt_path.read_bytes()
    old = read_json(receipt_path)
    if old == manifest:
        return {}, {}  # Повтор не восстанавливает даже изменённую памятку.
    update = manifest.get('guide_update')
    if (not isinstance(update, dict) or set(update) != {'previous_manifest_sha256'}
            or update['previous_manifest_sha256'] != digest(json_bytes(old))):
        raise DeliveryError('Эта версия выдачи не подходит для обновления памятки. Обратитесь к преподавателю.')
    old_fixed = {k: v for k, v in old.items() if k not in ('files', 'guide_update')}
    new_fixed = {k: v for k, v in manifest.items() if k not in ('files', 'guide_update')}
    old_other = {k: v for k, v in old['files'].items() if k not in GUIDE_FILES}
    new_other = {k: v for k, v in manifest['files'].items() if k not in GUIDE_FILES}
    if old_fixed != new_fixed or old_other != new_other:
        raise DeliveryError('Обновление затрагивает не только памятку. Автоматическое применение запрещено.')
    if not GUIDE_FILES <= data.keys() or not GUIDE_FILES <= manifest['files'].keys():
        raise DeliveryError('Не хватает Markdown или PDF памятки.')
    for name, expected in manifest['runtime_sha256'].items():
        if digest(target_path(root, name).read_bytes()) != expected:
            raise DeliveryError('Файл запуска изменён: ' + name)
    changes, before = {}, {}
    for name in sorted(GUIDE_FILES):
        path = target_path(root, name)
        if path.exists() and not path.is_file():
            raise DeliveryError('Путь памятки занят не файлом: ' + name)
        current = path.read_bytes() if path.exists() else None
        wanted = data[name]
        if current == wanted:
            continue
        previous_hash = old['files'].get(name)
        if current is None:
            if previous_hash is not None:
                raise DeliveryError('Прежняя памятка удалена: ' + name + '. Нужен ручной разбор.')
        elif previous_hash is None or digest(current) != previous_hash:
            raise DeliveryError('В памятке есть свои изменения: ' + name + '. Они не перезаписаны.')
        changes[name], before[name] = wanted, current
    # История старой выдачи сохраняется отдельно; квитанция нового состояния последняя.
    history = '.unipay/history/lab01-' + update['previous_manifest_sha256'] + '.json'
    history_path = target_path(root, history)
    if history_path.exists():
        if not history_path.is_file() or history_path.read_bytes() != json_bytes(old):
            raise DeliveryError('Конфликт сохранённой квитанции: ' + history)
    else:
        changes[history], before[history] = json_bytes(old), None
    changes[receipt_name], before[receipt_name] = json_bytes(manifest), before_receipt
    return changes, before


def replace_guide_file(path, content):
    """Один атомарный rename в том же каталоге; файл назначения предварительно проверен."""
    descriptor, temporary_name = tempfile.mkstemp(prefix='.unipay-guide-', dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(content)
        no_links(path)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_guide_update(bundle, target, apply=False):
    manifest, data = load_bundle(bundle)
    root = absolute_folder(target)
    changes, before = guide_plan(root, manifest, data)
    result = dict(variant=manifest['variant'], lab=1, target=str(root), files=sorted(changes),
                  applied=False, already_applied=not changes, guide_only=True)
    if not apply or not changes:
        return result
    lock = target_path(root, LOCK)
    try:
        handle = lock.open('xb')
    except FileExistsError as error:
        raise DeliveryError('Одновременно уже выполняется другая выдача.') from error
    created_dirs, written = [], []
    release_lock = True
    try:
        with handle:
            handle.write(b'UniPay guide update in progress\n')
        # Повторная проверка после lock. Файлы лабораторных вообще не заменяются.
        changes, before = guide_plan(root, manifest, data, own_lock=True)
        for name, content in changes.items():
            path = target_path(root, name)
            current = path.read_bytes() if path.exists() else None
            if current != before[name]:
                raise DeliveryError('Файл изменился во время обновления: ' + name)
            create_parents(path.parent, root, created_dirs)
            replace_guide_file(path, content)
            written.append(name)
    except BaseException as original:
        try:
            for name in reversed(written):
                path = target_path(root, name)
                if path.read_bytes() != changes[name]:
                    raise DeliveryError('Файл изменён параллельно: ' + name)
                if before[name] is None:
                    path.unlink()
                else:
                    replace_guide_file(path, before[name])
            for directory in reversed(created_dirs):
                directory.rmdir()
        except BaseException as rollback_error:
            release_lock = False
            raise DeliveryError('Обновление памятки прервано; lock сохранён. Не удаляйте файлы, обратитесь к преподавателю.') from rollback_error
        raise original
    finally:
        if release_lock:
            lock.unlink()
    result.update(files=sorted(changes), applied=bool(changes), already_applied=not changes)
    return result

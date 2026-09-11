"""Точечная надстройка выдачи PDF; исходники заданий не затрагиваются."""
from pathlib import Path


def upgrade_client(text):
    replacements = [
        ("manifest['lab'] == 1 and name in ROOT_FILES", "manifest['lab'] == 1 and name in (ROOT_FILES | {'START_HERE.pdf'})"),
        ('CLIENT_VERSION = 1', 'CLIENT_VERSION = 2'),
        ("version='UniPay client 1'", "version='UniPay client 2'"),
        ("if name.casefold() in names or not isinstance(text, str) or len(text.encode('utf-8')) > MAX_DOWNLOAD:",
         "if name.casefold() in names or len(public_file_bytes(name, text)) > MAX_DOWNLOAD:"),
        ("path.write_bytes(text.encode('utf-8'))", "path.write_bytes(public_file_bytes(name, text))"),
        ('fetch=download, ask=input):', 'fetch=download, ask=input, update_guide=False):'),
        ('preview = apply_bundle(bundle, root, False)',
         'installer = apply_guide_update if update_guide else apply_bundle\n        preview = installer(bundle, root, False)'),
        ('result = apply_bundle(bundle, root, True)', 'result = installer(bundle, root, True)'),
        ("parser.add_argument('--list',", "parser.add_argument('--update-guide', action='store_true', help='Обновить только памятку уже полученной ЛР1; решения не менять')\n    parser.add_argument('--list',"),
        ("install(number, variant, root, catalog, yes=args.yes, dry_run=args.dry_run)",
         "install(number, variant, root, catalog, yes=args.yes, dry_run=args.dry_run, update_guide=args.update_guide)"),
        ("print('  + ' + name)", "print(('  ~ ' if update_guide else '  + ') + name)"),
        ("ask('Добавить эти файлы? [y/N]: ')", "ask('Обновить только памятку? [y/N]: ' if update_guide else 'Добавить эти файлы? [y/N]: ')"),
    ]
    for old, new in replacements:
        if text.count(old) != 1:
            raise ValueError(f'Изменился исходник клиента: {old!r}')
        text = text.replace(old, new, 1)
    marker = '\ndef main():\n'
    if text.count(marker) != 1:
        raise ValueError('Не найден единственный main клиента')
    runtime = Path(__file__).with_name('guide_runtime.py').read_text(encoding='utf-8')
    text = text.replace(marker, '\n\n' + runtime + '\n' + marker, 1)
    compile(text, 'unipay.py', 'exec')
    return text

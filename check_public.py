"""Проверка опубликованных материалов без доступа к приватному репозиторию."""
import argparse
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

import unipay

ROOT = Path(__file__).resolve().parent


def command(target, *arguments, expected=0):
    result = subprocess.run([sys.executable, *arguments], cwd=target, capture_output=True,
                            text=True, encoding='utf-8', timeout=90, check=False)
    assert result.returncode == expected, result.stdout + result.stderr
    return result.stdout + result.stderr


def offline(run_tests):
    catalog_bytes = (ROOT / 'catalog.json').read_bytes()
    catalog = unipay.validate_catalog(unipay.decode_json(catalog_bytes))
    expected_paths = set()
    checked = 0
    with tempfile.TemporaryDirectory(prefix='unipay-cli-version-') as temporary:
        target = Path(temporary)
        (target / 'unipay.py').write_bytes((ROOT / 'unipay.py').read_bytes())
        (target / 'catalog.json').write_bytes(catalog_bytes)
        # Настоящий main текущего интерпретатора, а не обход проверки версии через install.
        script = ("import sys; from pathlib import Path; import unipay; "
                  "unipay.download=lambda name,limit:Path(name).read_bytes(); "
                  "sys.argv=['unipay.py','--list']; raise SystemExit(unipay.main())")
        text = command(target, '-c', script)
        assert 'Открытые лабораторные:' in text
    for number, packages in sorted(catalog['labs'].items(), key=lambda item: int(item[0])):
        for variant in unipay.ORDER:
            entry = packages[variant]
            path = ROOT / entry['path']
            expected_paths.add(path.resolve())
            manifest, files = unipay.decode_public_bundle(path.read_bytes(), entry, int(number), variant)
            with tempfile.TemporaryDirectory(prefix='unipay-public-verify-') as temporary:
                bundle = Path(temporary)
                (bundle / 'ISSUE.json').write_bytes(unipay.json_bytes(manifest))
                for name, text in files.items():
                    target = bundle / 'payload' / unipay.relative_file(name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(unipay.public_file_bytes(name, text))
                    if name.endswith('.py'):
                        ast.parse(text, filename=name)
                unipay.load_bundle(bundle)
            if number == '1':
                with tempfile.TemporaryDirectory(prefix='unipay-first-download-') as temporary:
                    target = Path(temporary)
                    (target / 'unipay.py').write_bytes((ROOT / 'unipay.py').read_bytes())
                    fetch = lambda name, limit: (ROOT / name).read_bytes()
                    assert not unipay.install(1, variant, target, catalog, dry_run=True, fetch=fetch)['applied']
                    assert {p.name for p in target.iterdir()} == {'unipay.py'}
                    unipay.install(1, variant, target, catalog, yes=True, fetch=fetch)
                    if run_tests:
                        command(target, 'check.py', 'lab01')
                    for name, content in files.items():
                        assert (target / name).read_bytes() == unipay.public_file_bytes(name, content)
                    changed = target / 'labs/lab01/app/domain/__init__.py'
                    changed.write_text('# student work must survive a repeated download\n', encoding='utf-8')
                    before = changed.read_bytes()
                    assert unipay.install(1, variant, target, catalog, yes=True, fetch=fetch)['already_applied']
                    assert changed.read_bytes() == before
            checked += 1
    actual_paths = {p.resolve() for p in (ROOT / 'bundles').glob('*') if p.is_file()}
    assert actual_paths == expected_paths, 'В основной ветке должны быть только опубликованные пакеты'
    print(f'Public check: {checked} вариантов проверено; CLI версии, установка и сохранение решений работают.')


def live():
    """После merge: анонимно скачать реальный клиент и выполнить его из чистой папки."""
    request = urllib.request.Request(unipay.PUBLIC_ROOT + 'unipay.py?t=' + str(time.time_ns()),
                                     headers={'User-Agent': 'UniPay-Public-Acceptance/1'})
    with urllib.request.urlopen(request, timeout=30) as response:
        assert response.status == 200 and response.geturl().startswith(unipay.PUBLIC_ROOT)
        data = response.read(65537)
    assert len(data) <= 65536 and data == (ROOT / 'unipay.py').read_bytes(), 'Опубликованный клиент отличается от проверяемого'
    with tempfile.TemporaryDirectory(prefix='unipay-anonymous-') as temporary:
        target = Path(temporary)
        (target / 'unipay.py').write_bytes(data)
        listing = command(target, 'unipay.py', '--list')
        assert 'Открытые лабораторные: 1' in listing
        command(target, 'unipay.py', '1', '--variant', 'card', '--yes')
        command(target, 'check.py', 'lab01')
        assert (target / 'START_HERE.pdf').read_bytes() == (ROOT / 'START_HERE.pdf').read_bytes()
        assert (target / 'START_HERE.md').read_bytes() == (ROOT / 'START_HERE.md').read_bytes()
        changed = target / 'labs/lab01/app/domain/__init__.py'
        changed.write_text('# retained student modification\n', encoding='utf-8')
        command(target, 'unipay.py', '1', '--yes')
        assert changed.read_text(encoding='utf-8') == '# retained student modification\n'
        catalog = unipay.validate_catalog(unipay.decode_json((ROOT / 'catalog.json').read_bytes()))
        # Закрытость следующего номера проверяем только пока он не опубликован.
        unavailable = next((n for n in range(2, 7) if str(n) not in catalog['labs']), None)
        if unavailable is not None:
            text = command(target, 'unipay.py', str(unavailable), '--yes', expected=2)
            assert 'ещё не опубликована' in text
            assert not (target / f'labs/lab{unavailable:02d}').exists()
    print('LIVE: anonymous download, actual CLI, Lab1 tests, repeat preservation and unopened-number rejection passed.')


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-lab1-tests', action='store_true')
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if args.live:
        live()
    else:
        offline(args.run_lab1_tests)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

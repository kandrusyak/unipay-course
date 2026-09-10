"""Проверка опубликованных данных без доступа к репозиторию преподавателя."""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile

import unipay

ROOT = Path(__file__).resolve().parent


def main():
    catalog = unipay.validate_catalog(unipay.decode_json((ROOT/'catalog.json').read_bytes()))
    expected_paths = set()
    checked = 0
    for number, packages in sorted(catalog['labs'].items(), key=lambda item: int(item[0])):
        for variant in unipay.ORDER:
            entry = packages[variant]
            path = ROOT / entry['path']
            expected_paths.add(path.resolve())
            data = path.read_bytes()
            manifest, files = unipay.decode_public_bundle(data, entry, int(number), variant)
            with tempfile.TemporaryDirectory(prefix='unipay-public-verify-') as temp:
                root = Path(temp)
                (root/'ISSUE.json').write_bytes(unipay.json_bytes(manifest))
                for name, text in files.items():
                    target = root/'payload'/unipay.relative_file(name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(text.encode('utf-8'))
                    if name.endswith('.py'):
                        ast.parse(text, filename=name)
                unipay.load_bundle(root)
            if number == '1':
                with tempfile.TemporaryDirectory(prefix='unipay-first-download-') as temp:
                    target = Path(temp)
                    (target/'unipay.py').write_bytes((ROOT/'unipay.py').read_bytes())
                    fetch = lambda name, limit: (ROOT/name).read_bytes()
                    assert not unipay.install(1,variant,target,catalog,dry_run=True,fetch=fetch)['applied']
                    assert set(p.name for p in target.iterdir()) == {'unipay.py'}
                    unipay.install(1,variant,target,catalog,yes=True,fetch=fetch)
                    assert unipay.install(1,variant,target,catalog,yes=True,fetch=fetch)['already_applied']
                    if '--run-lab1-tests' in sys.argv:
                        subprocess.run([sys.executable,'check.py','lab01'],cwd=target,check=True,timeout=30)
            checked += 1
    actual_paths = {p.resolve() for p in (ROOT/'bundles').glob('*') if p.is_file()}
    assert actual_paths == expected_paths, 'В Git должны быть только опубликованные номера'
    print(f'Public check: {checked} вариантов лабораторных проверено; неопубликованных файлов нет.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

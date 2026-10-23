"""Anonymous public install test for every released variant; never writes Git."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, str(Path.cwd()))
import unipay

expected_source = 'db501bfc09e0518756618f258511d92bfb35d7a9'
url = unipay.PUBLIC_ROOT + 'catalog.json?lab04check=' + str(time.time_ns())
with urllib.request.urlopen(url, timeout=30) as response:
    catalog = unipay.validate_catalog(json.loads(response.read()))
assert set(catalog['labs']) == {'1', '2', '3', '4'}
assert len(catalog['labs']['4']) == 14
assert all(e['source_commit'] == expected_source for e in catalog['labs']['4'].values())
with urllib.request.urlopen(unipay.PUBLIC_ROOT + 'unipay.py?lab04check=' + str(time.time_ns()), timeout=30) as response:
    public_client = response.read()
assert public_client == Path('unipay.py').read_bytes(), 'Public installer unexpectedly changed'
cache = {}


def fetch(name, limit):
    if name not in cache:
        cache[name] = unipay.download(name, limit)
    assert len(cache[name]) <= limit
    return cache[name]


rows = []
with tempfile.TemporaryDirectory(prefix='lab04-live-') as temp:
    for variant in unipay.ORDER:
        root = Path(temp) / variant
        root.mkdir()
        (root/'unipay.py').write_bytes(public_client)
        with contextlib.redirect_stdout(io.StringIO()):
            for n in (1,2,3):
                assert unipay.install(n, variant, root, catalog, yes=True, fetch=fetch)['applied']
                p = root/f'labs/lab{n:02}/app/domain/__init__.py'
                p.write_bytes(p.read_bytes() + b'\n# real prior solution preserved\n')
            before = {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
            assert unipay.install(4, variant, root, catalog, yes=True, fetch=fetch)['applied']
            assert all((root/p).read_bytes() == data for p,data in before.items())
        tests = subprocess.run([sys.executable, 'check.py', 'lab04'], cwd=root, text=True, capture_output=True, timeout=90)
        assert tests.returncode == 0, (variant, tests.stdout, tests.stderr)
        changed = root/'labs/lab04/app/domain/__init__.py'
        changed.write_text('# retain lab04 solution\n', encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            assert unipay.install(4, variant, root, catalog, yes=True, fetch=fetch)['already_applied']
        assert changed.read_text(encoding='utf-8') == '# retain lab04 solution\n'
        assert not (root/'labs/lab05').exists() and not (root/'labs/lab06').exists()
        rows.append(dict(variant=variant, public_download=True, installed=True, old_solutions_preserved=True, repeat_safe=True, green_starter=tests.stdout.strip()))
report=dict(status='PASS', source_commit=expected_source, open_labs=sorted(catalog['labs']), variants=len(rows), rows=rows, installer_sha256=hashlib.sha256(public_client).hexdigest())
Path('lab04-live-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))

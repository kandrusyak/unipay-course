"""Recreate only reviewed Lab4 student packets in a clean public worktree."""
import base64
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import lzma
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path.cwd()
BASE = '70d5bb860fcbc10f142d6fb13dd9ed1351374218'
SOURCE = 'db501bfc09e0518756618f258511d92bfb35d7a9'
RECIPE_HASH = os.environ['RECIPE_SHA256']
EXPECTED_TREE = os.environ['CANDIDATE_TREE']
BRANCH = 'release/lab04-ready-20261023'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def jb(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


encoded = (ROOT / '.github/release/lab04-recipe.b64').read_bytes()
assert digest(encoded) == RECIPE_HASH
recipe = json.loads(lzma.decompress(base64.b64decode(encoded)))
assert recipe['version'] == 1 and recipe['lab'] == 4
assert recipe['public_base'] == BASE and recipe['source']['source_commit'] == SOURCE
assert recipe['candidate_tree'] == EXPECTED_TREE
assert git(ROOT, 'ls-remote', 'origin', 'refs/heads/main').split()[0] == BASE, 'main moved; review instead of overwriting'
work = Path(os.environ['RUNNER_TEMP']) / 'lab04-candidate'
subprocess.run(['git', 'worktree', 'add', '--detach', str(work), BASE], check=True)
initial = {p: subprocess.check_output(['git', '-C', str(work), 'show', BASE + ':' + p]) for p in git(work, 'ls-files').splitlines()}
spec = importlib.util.spec_from_file_location('release_client', work / 'unipay.py')
client = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = client
spec.loader.exec_module(client)
old_catalog = client.validate_catalog(json.loads(initial['catalog.json']))
assert set(old_catalog['labs']) == {'1', '2', '3'}
assert set(recipe['variants']) == set(recipe['entries']) == set(client.ORDER)
catalog = copy.deepcopy(old_catalog)
catalog['labs']['4'] = recipe['entries']
client.validate_catalog(catalog)
new_paths = {'catalog.json'}
for variant in client.ORDER:
    base_entry = old_catalog['labs']['3'][variant]
    change = recipe['variants'][variant]
    assert change['base_sha256'] == base_entry['sha256']
    old_manifest, old_files = client.decode_public_bundle((work / base_entry['path']).read_bytes(), base_entry, 3, variant)
    files = {p.replace('lab03', 'lab04'): text for p, text in old_files.items()}
    for name, edits in change['edits'].items():
        assert str(client.relative_file(name)) == name
        assert name.startswith('labs/lab04/') or name == '.github/workflows/lab04.yml'
        if edits is None:
            del files[name]
        else:
            lines = files.get(name, '').splitlines(keepends=True)
            for start, end, additions in reversed(edits):
                assert 0 <= start <= end <= len(lines)
                lines[start:end] = additions
            files[name] = ''.join(lines)
    assert all(p.startswith('labs/lab04/') or p == '.github/workflows/lab04.yml' for p in files)
    manifest = copy.deepcopy(old_manifest)
    manifest.update(lab=4, **recipe['source'], files={p:digest(t.encode('utf-8')) for p,t in files.items()})
    document = dict(format=1, course='unipay-oop', lab=4, variant=variant, manifest=manifest, files=files)
    data = jb(dict(encoding='xz+base64', data=base64.b64encode(lzma.compress(jb(document), preset=6)).decode('ascii')))
    entry = recipe['entries'][variant]
    assert entry['source_commit'] == SOURCE and entry['source_tree'] == recipe['source']['source_tree']
    assert digest(data) == entry['sha256'] and len(data) == entry['size']
    assert entry['path'] == 'bundles/' + entry['sha256'] + '.json'
    assert client.decode_public_bundle(data, entry, 4, variant) == (manifest, files)
    destination = work / entry['path']
    assert not destination.exists()
    destination.write_bytes(data)
    new_paths.add(entry['path'])
(work / 'catalog.json').write_bytes(jb(catalog))
assert len(new_paths) == 15
assert all((work / p).read_bytes() == raw for p,raw in initial.items() if p != 'catalog.json')
assert all(catalog['labs'][n] == old_catalog['labs'][n] for n in ('1', '2', '3'))
rows = []
with tempfile.TemporaryDirectory(prefix='lab04-acceptance-') as temporary:
    for variant in client.ORDER:
        target = Path(temporary) / variant
        target.mkdir()
        (target / 'unipay.py').write_bytes(initial['unipay.py'])
        fetch = lambda name, limit: (work / name).read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            for number in (1,2,3):
                assert client.install(number, variant, target, catalog, yes=True, fetch=fetch)['applied']
                p = target / f'labs/lab{number:02}/app/domain/__init__.py'
                p.write_bytes(p.read_bytes() + b'\n# preserve prior student solution\n')
            before = {p.relative_to(target).as_posix():p.read_bytes() for p in target.rglob('*') if p.is_file()}
            assert not client.install(4, variant, target, catalog, dry_run=True, fetch=fetch)['applied']
            assert before == {p.relative_to(target).as_posix():p.read_bytes() for p in target.rglob('*') if p.is_file()}
            assert client.install(4, variant, target, catalog, yes=True, fetch=fetch)['applied']
            assert all((target / p).read_bytes() == raw for p,raw in before.items())
        tests = subprocess.run([sys.executable, 'check.py', 'lab04'], cwd=target, text=True, capture_output=True, timeout=90)
        assert tests.returncode == 0, (variant, tests.stdout, tests.stderr)
        p = target / 'labs/lab04/app/domain/__init__.py'
        p.write_text('# retain lab04 work\n', encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            assert client.install(4, variant, target, catalog, yes=True, fetch=fetch)['already_applied']
            assert p.read_text(encoding='utf-8') == '# retain lab04 work\n'
            for n in (5,6):
                try:
                    client.install(n, variant, target, catalog, yes=True, fetch=fetch)
                except client.DeliveryError:
                    pass
                else:
                    raise AssertionError('Future lab was opened')
        rows.append(dict(variant=variant, install=True, dry_run=True, retained_solutions=True, repeat_safe=True, starter_tests=tests.stdout.strip(), labs56_closed=True))
subprocess.run([sys.executable, 'check_public.py', '--run-lab1-tests'], cwd=work, check=True)
subprocess.run(['git', '-C', str(work), 'add', 'catalog.json', 'bundles'], check=True)
assert set(git(work, 'diff', '--cached', '--name-only').splitlines()) == new_paths
assert git(work, 'write-tree') == EXPECTED_TREE
assert git(ROOT, 'ls-remote', 'origin', 'refs/heads/main').split()[0] == BASE
subprocess.run(['git', 'config', 'user.name', 'github-actions[bot]'], check=True)
subprocess.run(['git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com'], check=True)
message = 'Open Lab 4 for all fourteen variants\n\nSource ' + SOURCE + '. Preserve Lab1-Lab3, the installer and all previous catalog entries. Labs5-6 remain closed.'
commit = git(ROOT, 'commit-tree', EXPECTED_TREE, '-p', BASE, '-m', message)
subprocess.run(['git', 'push', 'origin', commit + ':refs/heads/' + BRANCH], check=True)
report = dict(status='PASS', source=recipe['source'], public_base=BASE, candidate_commit=commit, candidate_tree=EXPECTED_TREE, paths=sorted(new_paths), rows=rows)
(ROOT / 'lab04-public-verification.json').write_bytes(jb(report))
print(json.dumps(report, ensure_ascii=False, indent=2))

"""Reconstruct only reviewed public Lab3 data; exact packet and tree hashes gate release."""
import base64
import contextlib
import copy
import hashlib
import io
import json
import lzma
from pathlib import Path
import sys
import tempfile
import types

SOURCE = 'dd84e4c1b25fb000b93255220e421d19fcce3e17'
TREE = 'aa011bdfdbc0cd1baae91f4d25b912209c6d3fd1'
RECIPE_HASH = '95a46639abdaebc5954f61d0126e08c6d9dc1b5c9e12eab2aaa2a4dfcd95f746'
CLIENT_HASH = '1f6fb6ff6c9fab28ca33fe87fa4b1bf2f11e690c9e28fcdb7d0e76b767937340'
CATALOG_HASH = '5d1130149532a1827b72959447ebaed8dd4c9d21f494d794b044d6d17bb4e6f3'
REPAIR_HASH = '41c1c5a0def4d2e2afc04622133e470a6acd9083fd38019b62ef115b6affbdcf'
CORRUPT_HASH = 'c52e699ea5b73a2b879130bec344af0b498b79997b757eef5d688ad9e2dd3b54'
VARIANTS = set('account audit authorization card clearing customer fraud fx ledger limits merchant notification reporting settlement'.split())

def digest(data):
    return hashlib.sha256(data).hexdigest()

def encode_json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')

def checked(data, entry):
    assert len(data) == entry['size'] and digest(data) == entry['sha256'], entry
    assert entry['path'] == 'bundles/' + entry['sha256'] + '.json'

def decoded(data):
    return json.loads(lzma.decompress(base64.b64decode(json.loads(data)['data']), memlimit=134217728))

def upgrade_checks(client, catalog, payloads):
    def install(*args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return client.install(*args, **kwargs)
    def files(root):
        return {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with tempfile.TemporaryDirectory(prefix='release-lab03-') as temp:
        for variant in sorted(VARIANTS):
            target = Path(temp) / variant
            def fetch(name, limit):
                data = payloads[name]
                assert len(data) <= limit
                return data
            for number in (1, 2):
                assert install(number, variant, target, catalog, yes=True, fetch=fetch)['applied']
                p = target / f'labs/lab{number:02d}/app/domain/{variant}.py'
                p.write_bytes(p.read_bytes() + b'\n# retained student work\n')
            before = files(target)
            assert not install(3, variant, target, catalog, dry_run=True, fetch=fetch)['applied']
            assert files(target) == before
            assert install(3, variant, target, catalog, yes=True, fetch=fetch)['applied']
            assert all((target / p).read_bytes() == data for p, data in before.items())
            p = target / f'labs/lab03/app/domain/{variant}.py'
            p.write_bytes(p.read_bytes() + b'\n# retained lab03 solution\n')
            after = files(target)
            assert install(3, variant, target, catalog, yes=True, fetch=fetch)['already_applied']
            assert files(target) == after
            try:
                install(4, variant, target, catalog, yes=True, fetch=lambda *args: sys.exit('Unexpected Lab4 download'))
            except client.DeliveryError:
                pass
            else:
                raise AssertionError('Lab4 must stay closed')
            assert not any((target / f'labs/lab{n:02d}').exists() for n in (4, 5, 6))
            print('PASS upgrade, dry-run, previous work, repeat, closed future labs:', variant)

def build(root, recipe_path):
    recipe_raw = recipe_path.read_bytes()
    assert digest(recipe_raw) == RECIPE_HASH, 'Transfer data corrupted'
    recipe = json.loads(lzma.decompress(base64.b64decode(recipe_raw), memlimit=134217728))
    assert recipe['base'] == 'b025116514180d26da54d7b07cd67e70ba54a449'
    assert recipe['source_commit'] == SOURCE and recipe['source_tree'] == TREE
    assert set(recipe['entries']) == set(recipe['files']) == VARIANTS
    previous = json.loads((root / 'catalog.json').read_bytes())
    assert set(previous['labs']) == {'1', '2'}, 'Catalog changed; do not replace editions'
    raw_client = (root / 'unipay.py').read_bytes()
    assert digest(raw_client) == CLIENT_HASH, 'Public installer changed; re-review required'
    client = types.ModuleType('actual_public_unipay')
    client.__file__ = str(root / 'unipay.py')
    exec(compile(raw_client, client.__file__, 'exec'), client.__dict__)
    client.validate_catalog(previous)
    outputs, payloads = {}, {}
    for number, entries in previous['labs'].items():
        assert set(entries) == VARIANTS
        for variant, entry in entries.items():
            raw = (root / entry['path']).read_bytes()
            if digest(raw) != entry['sha256']:
                assert number == '2' and variant == 'merchant'
                assert entry['sha256'] == REPAIR_HASH and digest(raw) == CORRUPT_HASH
                assert len(raw) == entry['size'] == 10331 and raw[9282] == 97
                raw = raw[:9282] + bytes([101]) + raw[9283:]
                checked(raw, entry)
                outputs[entry['path']] = raw
                print('Restored one transport byte; original Merchant/Lab2 SHA-256 and assignment unchanged')
            checked(raw, entry)
            client.decode_public_bundle(raw, entry, int(number), variant)
            payloads[entry['path']] = raw
    for variant, entry in recipe['entries'].items():
        assert entry['variant'] == variant and entry['source_commit'] == SOURCE and entry['source_tree'] == TREE
        old = decoded(payloads[previous['labs']['2'][variant]['path']])
        new = copy.deepcopy(old)
        new['lab'] = 3
        new['files'] = {}
        for name, edits in recipe['files'][variant].items():
            assert (name.startswith('labs/lab03/') or name == '.github/workflows/lab03.yml') and '..' not in Path(name).parts
            original = old['files'].get(name.replace('lab03', 'lab02'), '')
            lines = original.splitlines(keepends=True)
            if edits is not None:
                for start, end, text in reversed(edits):
                    assert 0 <= start <= end <= len(lines)
                    lines[start:end] = [text]
                text = ''.join(lines)
            else:
                text = original
            new['files'][name] = text
        new['manifest'].update(lab=3, source_commit=SOURCE, source_tree=TREE,
                               files={name: digest(text.encode('utf-8')) for name, text in new['files'].items()})
        packed = lzma.compress(encode_json(new), preset=6)
        raw = encode_json(dict(encoding='xz+base64', data=base64.b64encode(packed).decode('ascii')))
        checked(raw, entry)
        _, texts = client.decode_public_bundle(raw, entry, 3, variant)
        assert texts == new['files']
        outputs[entry['path']] = payloads[entry['path']] = raw
    catalog = copy.deepcopy(previous)
    catalog['labs']['3'] = recipe['entries']
    assert catalog['labs']['1'] == previous['labs']['1'] and catalog['labs']['2'] == previous['labs']['2']
    client.validate_catalog(catalog)
    raw_catalog = encode_json(catalog)
    assert digest(raw_catalog) == CATALOG_HASH
    upgrade_checks(client, catalog, payloads)
    outputs['catalog.json'] = raw_catalog
    assert len(outputs) in (15, 16)
    # Only write after the complete packet, installer and upgrade validation.
    for name, raw in outputs.items():
        p = root / name
        assert not p.is_symlink() and not p.parent.is_symlink()
        if name != 'catalog.json' and p.exists():
            assert name == 'bundles/' + REPAIR_HASH + '.json'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    print('PASS: all approved Lab3 packet hashes reproduced; installer unchanged')
    return outputs

if __name__ == '__main__':
    build(Path.cwd(), Path('.github/release/lab03-recipe.b64'))

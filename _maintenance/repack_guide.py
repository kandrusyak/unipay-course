"""Перепаковать только START_HERE в опубликованной ЛР1. Без commit/push и новых номеров."""
import argparse
import base64
import copy
import hashlib
import json
import lzma
from pathlib import Path
import sys
import tempfile


def prepare(client, root, markdown, pdf):
    catalog_bytes = (root / 'catalog.json').read_bytes()
    catalog = client.validate_catalog(client.decode_json(catalog_bytes))
    if '1' not in catalog['labs']:
        raise ValueError('ЛР1 ещё не опубликована.')
    if not markdown.startswith('# С чего начать\n') or not pdf.startswith(b'%PDF-') or not pdf.rstrip().endswith(b'%%EOF'):
        raise ValueError('Некорректный Markdown или PDF памятки.')
    new_catalog = copy.deepcopy(catalog)
    changes, deletions, evidence = {}, [], {}
    for variant in client.ORDER:
        entry = catalog['labs']['1'][variant]
        manifest, files = client.decode_public_bundle((root / entry['path']).read_bytes(), entry, 1, variant)
        # Проверяем не только упаковку, но и исходные контрольные суммы.
        with tempfile.TemporaryDirectory(prefix='unipay-guide-source-') as temporary:
            temp = Path(temporary)
            (temp / 'ISSUE.json').write_bytes(client.json_bytes(manifest))
            for name, value in files.items():
                dest = temp / 'payload' / client.relative_file(name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(client.public_file_bytes(name, value))
            client.load_bundle(temp)
        revised_files = copy.deepcopy(files)
        revised_files['START_HERE.md'] = markdown
        revised_files['START_HERE.pdf'] = dict(encoding='base64', data=base64.b64encode(pdf).decode('ascii'))
        if revised_files == files:
            continue
        revised = copy.deepcopy(manifest)
        revised['files']['START_HERE.md'] = client.digest(markdown.encode('utf-8'))
        revised['files']['START_HERE.pdf'] = client.digest(pdf)
        revised['guide_update'] = dict(previous_manifest_sha256=client.digest(client.json_bytes(manifest)))
        other = lambda values: {k:v for k,v in values.items() if k not in client.GUIDE_FILES}
        if other(revised_files) != other(files) or other(revised['files']) != other(manifest['files']):
            raise ValueError('Обнаружены изменения за пределами памятки: ' + variant)
        document = dict(format=1, course='unipay-oop', lab=1, variant=variant, manifest=revised, files=revised_files)
        packed = lzma.compress(client.json_bytes(document), preset=6)
        body = client.json_bytes(dict(encoding='xz+base64', data=base64.b64encode(packed).decode('ascii')))
        sha = client.digest(body)
        new_entry = dict(entry, path=f'bundles/{sha}.json', sha256=sha, size=len(body))
        checked, received = client.decode_public_bundle(body, new_entry, 1, variant)
        with tempfile.TemporaryDirectory(prefix='unipay-guide-repack-') as temporary:
            temp = Path(temporary)
            (temp / 'ISSUE.json').write_bytes(client.json_bytes(checked))
            for name, value in received.items():
                dest = temp / 'payload' / client.relative_file(name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(client.public_file_bytes(name, value))
            client.load_bundle(temp)
        changes[new_entry['path']] = body
        deletions.append(entry['path'])
        new_catalog['labs']['1'][variant] = new_entry
        evidence[variant] = dict(before=entry['sha256'], after=sha,
            source_commit=entry['source_commit'], source_tree=entry['source_tree'],
            unchanged_files=len(other(files)), changed_files=['START_HERE.md'], added_files=['START_HERE.pdf'])
    if not changes:
        return {}, [], {}
    new_catalog['minimum_client'] = max(2, catalog['minimum_client'])
    assert {k:v for k,v in new_catalog['labs'].items() if k != '1'} == {k:v for k,v in catalog['labs'].items() if k != '1'}
    client.validate_catalog(new_catalog)
    changes['START_HERE.pdf'] = pdf
    changes['START_HERE.md'] = markdown.encode('utf-8')
    changes['catalog.json'] = client.json_bytes(new_catalog)
    return changes, deletions, evidence


def write_prepared(client, root, changes, deletions):
    if not changes:
        return
    backup = {}
    for name in [*changes, *deletions]:
        path = client.target_path(root, name)
        backup[name] = path.read_bytes() if path.exists() else None
    applied = []
    try:
        for name, content in changes.items():
            path = client.target_path(root, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            client.replace_guide_file(path, content)
            applied.append(name)
        for name in deletions:
            client.target_path(root, name).unlink()
            applied.append(name)
    except BaseException:
        for name in reversed(applied):
            path = client.target_path(root, name)
            if backup[name] is None:
                path.unlink()
            else:
                client.replace_guide_file(path, backup[name])
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-repo', type=Path, required=True)
    parser.add_argument('--markdown', type=Path, required=True)
    parser.add_argument('--pdf', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    from publish import client_module
    client = client_module()
    root = client.absolute_folder(args.public_repo)
    changes, deletions, evidence = prepare(client, root, args.markdown.read_text(encoding='utf-8'), args.pdf.read_bytes())
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    if args.apply:
        write_prepared(client, root, changes, deletions)
        print('Перепакована только памятка. Проверьте diff и выполните commit/push отдельно.')
    else:
        print('Предварительный просмотр. Файлы не менялись.')


if __name__ == '__main__':
    main()

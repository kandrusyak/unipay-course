"""Build the explicitly requested Lab1 guide revision on the maintenance branch."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
from guide_client import upgrade_client
from repack_guide import prepare, write_prepared


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Unexpected source layout: ' + old)
    return text.replace(old, new, 1)


def main():
    old_client = (ROOT / 'unipay.py').read_text(encoding='utf-8')
    new_client = upgrade_client(old_client)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader('guide_new_client', loader=None))
    module.__file__ = str(ROOT / 'unipay.py')
    exec(compile(new_client, module.__file__, 'exec'), module.__dict__)
    pdf = base64.b64decode(''.join(p.read_text(encoding='ascii').strip()
                                   for p in sorted(Path(__file__).parent.glob('pdf.*'))), validate=True)
    if hashlib.sha256(pdf).hexdigest() != 'cc16c8fb4bac10cafe507c59ca753de9616d208911481aee4ed0c2f7e399a00a':
        raise ValueError('PDF does not match the approved local file')
    markdown = (ROOT / 'START_HERE.md').read_text(encoding='utf-8')
    catalog = module.validate_catalog(module.decode_json((ROOT / 'catalog.json').read_bytes()))
    if set(catalog['labs']) != {'1'}:
        raise ValueError('Expected only the already-published Lab1')
    entry = catalog['labs']['1']['card']
    _, files = module.decode_public_bundle((ROOT / entry['path']).read_bytes(), entry, 1, 'card')
    (ROOT / 'checks').mkdir(exist_ok=True)
    (ROOT / 'checks/start-guide-before.md').write_bytes(files['START_HERE.md'].encode('utf-8'))
    changes, deletions, evidence = prepare(module, ROOT, markdown, pdf)
    if len(evidence) != 14:
        raise ValueError('Exactly 14 public variants must be repacked')
    write_prepared(module, ROOT, changes, deletions)
    (ROOT / 'unipay.py').write_text(new_client, encoding='utf-8', newline='\n')
    report = dict(scope='Only START_HERE.md and START_HERE.pdf; no lab code/test changes',
                  previous_public_commit='e4a621591d75a4f8640f39c9e04483de72af227d',
                  pdf_sha256=hashlib.sha256(pdf).hexdigest(), variants=evidence)
    (ROOT / 'checks/guide-repack.json').write_bytes(module.json_bytes(report))
    checker = (ROOT / 'check_public.py').read_text(encoding='utf-8')
    checker = replace_once(checker, "target.write_bytes(text.encode('utf-8'))",
                           'target.write_bytes(unipay.public_file_bytes(name, text))')
    checker = replace_once(checker, "== content.encode('utf-8')",
                           '== unipay.public_file_bytes(name, content)')
    checker = replace_once(checker, "        command(target, 'check.py', 'lab01')\n        changed =",
                           "        command(target, 'check.py', 'lab01')\n        assert (target / 'START_HERE.pdf').read_bytes() == (ROOT / 'START_HERE.pdf').read_bytes()\n        assert (target / 'START_HERE.md').read_bytes() == (ROOT / 'START_HERE.md').read_bytes()\n        changed =")
    (ROOT / 'check_public.py').write_text(checker, encoding='utf-8', newline='\n')
    workflow = ROOT / '.github/workflows/public.yml'
    workflow.write_text(replace_once(workflow.read_text(encoding='utf-8'),
                                    '      - run: python unipay.py --help',
                                    '      - run: python unipay.py --help\n      - run: python -m pytest -q checks/test_guide.py'), encoding='utf-8', newline='\n')
    attributes = ROOT / '.gitattributes'
    attributes.write_text(attributes.read_text(encoding='utf-8').rstrip() + '\n*.pdf binary\n', encoding='utf-8', newline='\n')
    readme = ROOT / 'README.md'
    text = replace_once(readme.read_text(encoding='utf-8'),
                        'Прочитайте `START_HERE.md` и задание',
                        'Прочитайте `START_HERE.pdf` (или `START_HERE.md`) и задание')
    marker = '## Выполнение работы\n'
    update = ('## Обновление памятки\n\n'
              'Если ЛР1 уже получена, скачайте актуальный `unipay.py` по ссылке выше и замените прежний файл. '
              'Затем выполните:\n\n```sh\npython unipay.py 1 --update-guide\n```\n\n'
              'Команда обновляет только памятку и добавляет её PDF-версию. Решения и тесты не меняются. '
              'Если вы редактировали саму памятку, загрузчик остановится — обратитесь к преподавателю.\n\n')
    readme.write_text(replace_once(text, marker, update + marker), encoding='utf-8', newline='\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

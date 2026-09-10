"""Однократный перенос уже публичной ЛР1 в принятый формат. Не меняет Git refs.

Не получает приватные исходники и не исполняет содержимое пакета. Все 14 документов
до записи объектов обязаны совпасть с отпечатками локальной выдачи teaching-v1.0.
"""
import base64
import hashlib
import json
import lzma
import os
from pathlib import Path
import urllib.request

REPO = 'kandrusyak/unipay-course'
SEED = 'https://raw.githubusercontent.com/' + REPO + '/b887304477af426309fbe5005a05e4fc6642e9ba/releases/lab01-fddb8d86fc205ffa3c63812f266d13d9f052f644e3703c41ac984e46b9427dac.json.xz'
REVISION = dict(source_commit='d25eb9b55917d56d3de1f042ac9102a5345a85c2', source_tree='e7c46c3eb938dfbea061c5404d3ab8af42a67035')
RAW_EXPECTED = {
    'account': 'ce3dfbd626781c00ff45ffd61496d78f8a1db5c459fee992e8cba43a880a08e3',
    'audit': '9d43a68a03ff40ad488e788973c0afe09c392e49590d85512e8e0dd101e79b32',
    'authorization': '5284550fcba9c31049007f09267d3d04619dc93a253d36bc2bdca73689939b1d',
    'card': '541b413b4c4cd8fc4e5bc2e04c7a35d14e35540edc7447e3a28c5591d0b32041',
    'clearing': '3481d6d2c33d7dff5962639f11f2c2245085fe29a323502d1a6ca1fe9b345d72',
    'customer': 'b1ea769a1a5e547fe36ec87de2e901d38b95ebbdfa69cbf4662a4cef74477779',
    'fraud': '2fdf70810826cc1c3f834d60fb90e08cb01cc174a445ec006144ecaa84385e60',
    'fx': 'bfba5cca1684ac99e66da684b22bdaac87888745cf49eddb3d869b1e058c9cc2',
    'ledger': '30b992d9a90373f486e6a3dcafe54906eb74cb750c78ae283fcbd336a759b542',
    'limits': '392c65a977133e17b4cc8d80f4680416d0c65d9e7bf3a7fbc2428a71c99f120f',
    'merchant': 'f73591f89104ff9ebd36525e646b01bce413072b93f57275fcdaa5557e05d092',
    'notification': '84981c4c6a24c615dea667aa21ce274e58acf8c2c1f5a56b718d42d0b6ab87d3',
    'reporting': '66253626ab9747a0cbf21d88ad4967ae6f59d37184c19ef9a74c1fd2e101fb0d',
    'settlement': 'b9d2fee179327805f3bd57a2cb9b410507eadec52ee66b26307e96e6cd9b9490',
}
NEXT = '''## Следующая лабораторная

Скачанный `unipay.py` используется для всех номеров. В корне своего репозитория:

```sh
python unipay.py --list
python unipay.py 2
```

Номер замените нужным. Скрипт запоминает вариант в `.unipay/course.json`, получает
только опубликованную преподавателем работу и спрашивает подтверждение добавления.
Повтор не сбрасывает решение. Другая редакция или конфликт требуют разбора с преподавателем;
автоматической перезаписи нет. Просмотр без записи: `python unipay.py 2 --dry-run`.
Для первой установки вариант задаётся явно: `python unipay.py 1 --variant card`.

Git status, commit, push и PR выполняйте сами. Для получения материалов не нужны
GitHub-токен, pytest или доступ к преподавательскому репозиторию. Установка pytest
нужна позже для запуска открытых тестов. Скрипт не исполняет загруженный код.
'''


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def prepared(seed, root):
    assert set(seed) == {'format', 'course', 'lab', 'variants'}
    assert seed['format'] == 1 and seed['course'] == 'unipay-oop' and seed['lab'] == 1
    assert set(seed['variants']) == set(RAW_EXPECTED)
    outputs, entries = {}, {}
    for variant, old in seed['variants'].items():
        manifest, files = old['manifest'], old['payload']
        assert manifest['variant'] == variant and manifest['lab'] == 1
        assert set(files) == set(manifest['files'])
        for name, text in files.items():
            assert digest(text.encode()) == manifest['files'][name]
        prefix, _ = files['README.md'].split('## Следующая работа', 1)
        files['README.md'] = prefix.replace('Нужен Python 3.13.', 'Поддерживаются Python 3.10 и 3.13.') + NEXT
        manifest['files']['README.md'] = digest(files['README.md'].encode())
        manifest.update(REVISION)
        document = dict(format=1, course='unipay-oop', lab=1, variant=variant, manifest=manifest, files=files)
        raw = encoded(document)
        # Полное содержимое, пути, инструкции и все хеши должны соответствовать
        # независимо подготовленному документу, не только старой метке source_commit.
        assert digest(raw) == RAW_EXPECTED[variant], (variant, digest(raw))
        data = encoded(dict(encoding='xz+base64', data=base64.b64encode(lzma.compress(raw, preset=6)).decode()))
        sha = digest(data)
        name = f'bundles/{sha}.json'
        outputs[name] = data
        entries[variant] = dict(path=name, sha256=sha, size=len(data), variant=variant, **REVISION)
    outputs['catalog.json'] = encoded(dict(format=1, course='unipay-oop', minimum_client=1, labs={'1': entries}))
    client = (root / 'unipay.py').read_bytes()
    assert blob(client) == '3fd3bfdf2046d1a3b0d4ba093ef737074a7a0f2f'
    text = client.decode().replace('import sys\n', 'import sys\nimport stat\n', 1)
    old = """        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise DeliveryError(f'Ссылки и junction не поддерживаются: {part}')
"""
    new = old + """        # Path.is_junction появился позже Python 3.10. Проверяем Windows
        # reparse-атрибут без перехода по ссылке и на старых интерпретаторах.
        try:
            attributes = getattr(part.lstat(), 'st_file_attributes', 0)
        except FileNotFoundError:
            continue
        if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise DeliveryError(f'Ссылки и reparse points не поддерживаются: {part}')
"""
    assert text.count(old) == 1
    text = text.replace(old, new).replace('sys.version_info < (3, 13)', 'sys.version_info < (3, 10)').replace('Для курса нужен Python 3.13 или новее.', 'Для курса нужен Python 3.10 или новее.')
    outputs['unipay.py'] = text.encode()
    assert blob(outputs['unipay.py']) == 'ab4c1574c9f0491f2e0a7ecdd2f8b5b06e916fa8'
    readme = (root / 'README.md').read_bytes()
    assert blob(readme) == '0381f5a0a415c849c4567104a50598c27e011358'
    outputs['README.md'] = readme.decode().replace('Нужен Python **3.13**.', 'Поддерживаются Python **3.10** и **3.13**.').replace('`py -3.13`.', '`py -3.10` или `py -3.13`.').encode()
    assert blob(outputs['README.md']) == '2902e38f7955b193c2590c927c8961f5b2799e6b'
    return outputs


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Unexpected redirect; no credentials forwarded')


def main():
    assert os.environ['GITHUB_REPOSITORY'] == REPO
    with urllib.request.urlopen(SEED, timeout=30) as response:
        data = response.read(50000)
    assert len(data) == 39888 and digest(data) == 'fddb8d86fc205ffa3c63812f266d13d9f052f644e3703c41ac984e46b9427dac'
    decoder = lzma.LZMADecompressor(memlimit=64*1024*1024)
    raw = decoder.decompress(data, 16*1024*1024)
    assert decoder.eof and not decoder.unused_data
    outputs = prepared(json.loads(raw), Path('.'))
    folder = Path('prepared'); folder.mkdir()
    records = []
    opener = urllib.request.build_opener(NoRedirect())
    # Только создание объектов в этом публичном репозитории. Ветка и каталог main
    # НЕ обновляются; финальное дерево будет выбрано отдельно после проверки артефакта.
    for name, content in sorted(outputs.items()):
        path = folder / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
        request = urllib.request.Request('https://api.github.com/repos/' + REPO + '/git/blobs',
            data=json.dumps(dict(content=base64.b64encode(content).decode(), encoding='base64')).encode(),
            headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json',
                     'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28'}, method='POST')
        with opener.open(request, timeout=30) as response:
            assert response.status == 201
            sha = json.load(response)['sha']
        assert sha == blob(content)
        records.append(dict(path=name, mode='100644', type='blob', sha=sha))
    (folder / 'objects.json').write_bytes(encoded(records))
    print('Prepared', len(records), 'verified public blob objects; no commits or refs changed.')
    print(json.dumps(records, indent=2))


if __name__ == '__main__':
    main()

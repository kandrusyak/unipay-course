"""Обновляется только памятка; предыдущие решения и Git остаются неизменными."""
import base64
import copy
import json
import lzma
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import unipay


def packaged(document):
    body = unipay.json_bytes(dict(encoding='xz+base64', data=base64.b64encode(
        lzma.compress(unipay.json_bytes(document), preset=6)).decode('ascii')))
    manifest = document['manifest']
    sha = unipay.digest(body)
    entry = dict(path=f'bundles/{sha}.json', sha256=sha, size=len(body), variant=document['variant'],
                 source_commit=manifest['source_commit'], source_tree=manifest['source_tree'])
    return body, entry


def documents(variant):
    catalog = unipay.validate_catalog(unipay.decode_json((ROOT/'catalog.json').read_bytes()))
    entry = catalog['labs']['1'][variant]
    new_manifest, files = unipay.decode_public_bundle((ROOT/entry['path']).read_bytes(), entry, 1, variant)
    old = dict(format=1,course='unipay-oop',lab=1,variant=variant,
               manifest=copy.deepcopy(new_manifest),files=copy.deepcopy(files))
    expected = old['manifest'].pop('guide_update')['previous_manifest_sha256']
    old['manifest']['files'].pop('START_HERE.pdf')
    old['files'].pop('START_HERE.pdf')
    old['files']['START_HERE.md'] = (ROOT/'checks/start-guide-before.md').read_text(encoding='utf-8')
    old['manifest']['files']['START_HERE.md'] = unipay.digest(old['files']['START_HERE.md'].encode())
    assert unipay.digest(unipay.json_bytes(old['manifest'])) == expected
    return catalog, old


def initial(variant, root):
    catalog, old = documents(variant)
    raw, entry = packaged(old)
    original_catalog = copy.deepcopy(catalog)
    original_catalog['minimum_client'] = 1
    original_catalog['labs']['1'][variant] = entry
    root.mkdir()
    (root/'.git').mkdir()
    (root/'.git/config').write_text('# retained Git config\n')
    unipay.install(1,variant,root,original_catalog,yes=True,fetch=lambda *_:raw)
    path = root/'labs/lab01/app/domain/__init__.py'
    path.write_text("# Student changes\nraise RuntimeError('Never execute while downloading')\n")
    return catalog, old


def all_bytes(root):
    return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}


def fetch(name, limit):
    return (ROOT/name).read_bytes()


@pytest.mark.parametrize('variant', unipay.ORDER)
def test_update_all_variants_preserves_solutions_git_and_receipt_history(variant,tmp_path):
    root=tmp_path/'student'
    catalog,old=initial(variant,root)
    before=all_bytes(root)
    with pytest.raises(unipay.DeliveryError,match='другой версии'):
        unipay.install(1,variant,root,catalog,yes=True,fetch=fetch)
    assert all_bytes(root)==before
    result=unipay.install(1,variant,root,catalog,dry_run=True,update_guide=True,fetch=fetch)
    assert not result['applied'] and all_bytes(root)==before
    result=unipay.install(1,variant,root,catalog,update_guide=True,fetch=fetch,ask=lambda _: 'n')
    assert not result['applied'] and all_bytes(root)==before
    result=unipay.install(1,variant,root,catalog,yes=True,update_guide=True,fetch=fetch)
    assert result['applied']
    after=all_bytes(root)
    changed={p for p in before.keys()|after.keys() if before.get(p)!=after.get(p)}
    historical='.unipay/history/lab01-'+unipay.digest(unipay.json_bytes(old['manifest']))+'.json'
    assert changed=={'START_HERE.md','START_HERE.pdf','.unipay/issues/lab01.json',historical}
    assert after[historical]==unipay.json_bytes(old['manifest'])
    assert after['START_HERE.md']==(ROOT/'START_HERE.md').read_bytes()
    assert after['START_HERE.pdf']==(ROOT/'START_HERE.pdf').read_bytes()
    assert unipay.install(1,variant,root,catalog,yes=True,update_guide=True,fetch=fetch)['already_applied']
    assert unipay.install(1,variant,root,catalog,yes=True,fetch=fetch)['already_applied']
    assert all_bytes(root)==after


@pytest.mark.parametrize('conflict', ['markdown','pdf','receipt','lock','runtime','symlink'])
def test_conflicts_refuse_before_any_write(conflict,tmp_path):
    root=tmp_path/'student'
    catalog,old=initial('card',root)
    if conflict=='markdown': (root/'START_HERE.md').write_text('My own notes')
    if conflict=='pdf': (root/'START_HERE.pdf').write_bytes(b'my unrelated PDF')
    if conflict=='receipt':
        record=root/'.unipay/issues/lab01.json'
        data=json.loads(record.read_text());data['source_tree']='1'*40
        record.write_bytes(unipay.json_bytes(data))
    if conflict=='runtime': (root/'check.py').write_text('print("changed")')
    if conflict=='lock': (root/unipay.LOCK).write_text('another installation')
    if conflict=='symlink':
        target=tmp_path/'outside';target.write_text('external content')
        (root/'START_HERE.md').unlink()
        try: (root/'START_HERE.md').symlink_to(target)
        except OSError:
            # Windows CI without symlink privilege: exercise rejected reparse attribute
            # in the existing cross-platform no_links tests; no silently skipped case.
            (root/'START_HERE.md').mkdir()
    before=all_bytes(root)
    with pytest.raises((unipay.DeliveryError,OSError)):
        unipay.install(1,'card',root,catalog,yes=True,update_guide=True,fetch=fetch)
    assert all_bytes(root)==before


def test_writing_error_rolls_back_docs_and_keeps_solutions(tmp_path,monkeypatch):
    root=tmp_path/'student';catalog,_=initial('card',root);before=all_bytes(root)
    replace=unipay.replace_guide_file
    calls=[]
    def fail_on_receipt(path,content):
        calls.append(path.name)
        if path.name=='lab01.json': raise OSError('simulated disk failure')
        return replace(path,content)
    monkeypatch.setattr(unipay,'replace_guide_file',fail_on_receipt)
    with pytest.raises(OSError,match='simulated disk'):
        unipay.install(1,'card',root,catalog,yes=True,update_guide=True,fetch=fetch)
    assert 'START_HERE.pdf' in calls
    assert all_bytes(root)==before


@pytest.mark.parametrize('kind',['code','task','runtime','source'])
def test_update_cannot_hide_a_non_document_change(kind,tmp_path):
    root=tmp_path/'student';catalog,old=initial('card',root);before=all_bytes(root)
    entry=catalog['labs']['1']['card']
    manifest,files=unipay.decode_public_bundle((ROOT/entry['path']).read_bytes(),entry,1,'card')
    if kind=='source': manifest['source_commit']='f'*40
    else:
        path={'code':'labs/lab01/app/domain/card.py','task':'labs/lab01/TASK.md','runtime':'check.py'}[kind]
        files[path]+='\n# unexpected change\n'
        manifest['files'][path]=unipay.digest(files[path].encode())
        if kind=='runtime': manifest['runtime_sha256'][path]=manifest['files'][path]
    document=dict(format=1,course='unipay-oop',lab=1,variant='card',manifest=manifest,files=files)
    raw,changed=packaged(document);catalog['labs']['1']['card']=changed
    with pytest.raises(unipay.DeliveryError):
        unipay.install(1,'card',root,catalog,yes=True,update_guide=True,fetch=lambda *_:raw)
    assert all_bytes(root)==before


@pytest.mark.parametrize('name,value',[
    ('evil.py',{'encoding':'base64','data':'cHJpbnQoMSk='}),
    ('START_HERE.pdf',{'encoding':'base64','data':'!!!'}),
    ('START_HERE.pdf',{'encoding':'base64','data':'bm90IGEgcGRm'}),
    ('START_HERE.pdf',{'encoding':'base64','data':42}),
])
def test_binary_support_is_limited_to_valid_pdf(name,value):
    with pytest.raises(unipay.DeliveryError): unipay.public_file_bytes(name,value)


def test_old_client_is_told_to_update_before_installation(monkeypatch):
    monkeypatch.setattr(unipay,'CLIENT_VERSION',1)
    with pytest.raises(unipay.DeliveryError,match='новая версия'):
        unipay.validate_catalog(unipay.decode_json((ROOT/'catalog.json').read_bytes()))

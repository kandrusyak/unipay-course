"""Проверки документов текущей публичной ЛР1.

Историческая миграция одной памятки больше не является частью опубликованной версии:
до этой редакции пакеты студентам не выдавались. Проверяем полный актуальный пакет,
PDF и безопасное повторное получение без перезаписи решения.
"""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import unipay


def catalog():
    return unipay.validate_catalog(unipay.decode_json((ROOT / 'catalog.json').read_bytes()))


def package(variant):
    current = catalog()
    entry = current['labs']['1'][variant]
    manifest, files = unipay.decode_public_bundle((ROOT / entry['path']).read_bytes(), entry, 1, variant)
    return current, manifest, files


def fetch(name, limit):
    return (ROOT / name).read_bytes()


@pytest.mark.parametrize('variant', unipay.ORDER)
def test_current_lab1_contains_guide_pdf_and_readable_documents(variant):
    _, manifest, files = package(variant)
    assert manifest['source_commit'] == 'e23cdef6077fcf7e62683a1976ecc69782840e1d'
    assert manifest['source_tree'] == '5a0d8e99269de61e65260a8bb43ee056033b88ad'
    assert 'guide_update' not in manifest
    for name in ('labs/lab01/TASK.md', 'labs/lab01/REFERENCE.md', 'labs/lab01/STARTER-STATE.md'):
        assert isinstance(files[name], str) and files[name].strip()
        assert manifest['files'][name] == unipay.digest(files[name].encode('utf-8'))
    assert files['START_HERE.md'] == (ROOT / 'START_HERE.md').read_text(encoding='utf-8')
    pdf = unipay.public_file_bytes('START_HERE.pdf', files['START_HERE.pdf'])
    assert pdf == (ROOT / 'START_HERE.pdf').read_bytes()
    assert pdf.startswith(b'%PDF-') and pdf.rstrip().endswith(b'%%EOF')


@pytest.mark.parametrize('variant', unipay.ORDER)
def test_repeat_of_current_lab1_never_restores_student_solution(variant, tmp_path):
    current = catalog()
    root = tmp_path / 'student'
    root.mkdir()
    (root / '.git').mkdir()
    unipay.install(1, variant, root, current, yes=True, fetch=fetch)
    changed = root / 'labs/lab01/app/domain/__init__.py'
    changed.write_text('# student solution\n', encoding='utf-8')
    before = changed.read_bytes()
    result = unipay.install(1, variant, root, current, yes=True, fetch=fetch)
    assert result['already_applied']
    assert changed.read_bytes() == before
    # Старый флаг обновления памятки для уже текущей версии тоже ничего не переписывает.
    result = unipay.install(1, variant, root, current, yes=True, update_guide=True, fetch=fetch)
    assert result['already_applied']
    assert changed.read_bytes() == before


@pytest.mark.parametrize('name,value',[
    ('evil.py', {'encoding':'base64','data':'cHJpbnQoMSk='}),
    ('START_HERE.pdf', {'encoding':'base64','data':'!!!'}),
    ('START_HERE.pdf', {'encoding':'base64','data':'bm90IGEgcGRm'}),
    ('START_HERE.pdf', {'encoding':'base64','data':42}),
])
def test_binary_support_is_limited_to_valid_pdf(name, value):
    with pytest.raises(unipay.DeliveryError):
        unipay.public_file_bytes(name, value)


def test_old_client_is_told_to_update_before_installation(monkeypatch):
    monkeypatch.setattr(unipay, 'CLIENT_VERSION', 1)
    with pytest.raises(unipay.DeliveryError, match='новая версия'):
        unipay.validate_catalog(unipay.decode_json((ROOT / 'catalog.json').read_bytes()))

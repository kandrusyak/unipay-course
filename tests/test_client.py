"""Самопроверка публичной выдачи. Не запускает app и не содержит эталонов."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('unipay', ROOT / 'unipay.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def local_fetch(path, limit):
    data = (ROOT / path).read_bytes()
    if len(data) > limit:
        raise client.DeliveryError('Лимит размера')
    return data


class PublicDelivery(unittest.TestCase):
    def test_catalog_and_every_published_variant(self):
        catalog = client.load_catalog(local_fetch)
        self.assertTrue(catalog['labs'])
        self.assertEqual(set(catalog['labs']), {str(n) for n in range(1, len(catalog['labs']) + 1)})
        for variant in client.VARIANT_ORDER:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / 'student'
                for number in sorted(catalog['labs'], key=int):
                    lab = int(number)
                    before = {p.relative_to(target): p.read_bytes() for p in target.rglob('*') if p.is_file()}
                    preview = client.receive(lab, variant, target, dry_run=True, fetch=local_fetch)
                    self.assertFalse(preview['applied'])
                    after = {p.relative_to(target): p.read_bytes() for p in target.rglob('*') if p.is_file()}
                    self.assertEqual(before, after)
                    result = client.receive(lab, variant, target, yes=True, fetch=local_fetch)
                    self.assertTrue(result['applied'])
                    for path, data in before.items():
                        self.assertEqual((target / path).read_bytes(), data)
                    own = target / f'labs/lab{lab:02d}/student.txt'
                    own.write_bytes(b'my solution')
                    self.assertTrue(client.receive(lab, variant, target, yes=True, fetch=local_fetch)['already_applied'])
                    self.assertEqual(own.read_bytes(), b'my solution')
                    receipt = client.read_json(target / f'.unipay/issues/lab{lab:02d}.json')
                    self.assertTrue(all(not p.startswith('labs/') or p.startswith(f'labs/lab{lab:02d}/') for p in receipt['files']))

    def test_unpublished_lab_is_absent_not_merely_hidden(self):
        catalog = client.load_catalog(local_fetch)
        referenced = {entry['path'] for entry in catalog['labs'].values()}
        actual = {p.relative_to(ROOT).as_posix() for p in (ROOT / 'releases').iterdir() if p.is_file()}
        self.assertEqual(referenced, actual)
        for number in range(1, 7):
            if str(number) not in catalog['labs']:
                with tempfile.TemporaryDirectory() as temp, self.assertRaises(client.DeliveryError):
                    client.receive(number, 'card', Path(temp) / 'repo', yes=True, fetch=local_fetch)

    def test_corrupt_package_is_not_installed(self):
        catalog = client.load_catalog(local_fetch)
        entry = catalog['labs']['1']
        data = local_fetch(entry['path'], client.MAX_DOWNLOAD)
        with self.assertRaises(client.DeliveryError):
            client.unpack_release(data[:-1], entry, 1, 'card')

    def test_remote_locations_are_restricted(self):
        with self.assertRaises(client.DeliveryError):
            client.public_url('http://example.org/package')
        with self.assertRaises(client.DeliveryError):
            client.public_url('https://raw.githubusercontent.com/other/repository/main/a')


if __name__ == '__main__':
    unittest.main()

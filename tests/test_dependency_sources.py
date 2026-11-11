import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('vendor_sources', Path(__file__).parents[1] / 'distribution/vendor_patch_sources.py')
vendor_sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vendor_sources)


class DependencySourceTest(unittest.TestCase):
    def fixture(self, root):
        vendor = root / 'vendor'; crate = vendor / 'fixture-1.0.0'; crate.mkdir(parents=True)
        source = b'public dependency source fixture'
        (crate / 'lib.rs').write_bytes(source)
        (crate / '.cargo-checksum.json').write_text(json.dumps({'package': 'a' * 64,
            'files': {'lib.rs': hashlib.sha256(source).hexdigest()}}))
        lock = root / 'Cargo.lock'
        lock.write_text('version = 4\n[[package]]\nname = "fixture"\nversion = "1.0.0"\nsource = "registry+https://github.com/rust-lang/crates.io-index"\nchecksum = "' + 'a' * 64 + '"\n')
        return vendor, lock, crate

    def test_archive_contains_checked_source_and_has_no_local_identity_or_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); vendor, lock, crate = self.fixture(root)
            first = vendor_sources.archive_sources(vendor, lock, root / 'first.tar.gz')
            second = vendor_sources.archive_sources(vendor, lock, root / 'second.tar.gz')
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first) as archive:
                self.assertEqual('dependencies.json', archive.next().name)
                self.assertEqual((crate / 'lib.rs').read_bytes(), archive.extractfile('vendor/fixture-1.0.0/lib.rs').read())
                for member in archive:
                    self.assertEqual(0, member.mtime)
                    self.assertEqual('', member.uname)

    def test_modified_dependency_is_rejected_without_creating_an_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); vendor, lock, crate = self.fixture(root)
            (crate / 'lib.rs').write_text('changed source')
            with self.assertRaisesRegex(ValueError, 'changed'):
                vendor_sources.archive_sources(vendor, lock, root / 'bad.tar.gz')
            self.assertFalse((root / 'bad.tar.gz').exists())

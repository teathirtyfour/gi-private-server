import importlib.util
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('launcher_build', Path(__file__).parents[1] / 'distribution/build_launcher.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class LauncherPackagingTest(unittest.TestCase):
    def fixture(self, root):
        source = root / 'patch-source'
        (source / 'src').mkdir(parents=True)
        for name in ['Cargo.toml', 'Cargo.lock', 'LICENSE', 'README.md', 'sdk_public_key.xml', 'server_public_key.bin', 'src/lib.rs']:
            (source / name).write_text('source fixture: ' + name)
        (source / '.git').mkdir()
        (source / '.git/config').write_text('private repository configuration')
        (source / 'account-database.json').write_text('private account fixture')
        dll = root / 'patch.dll'; dll.write_bytes(b'MZ fixture DLL')
        return source, dll

    def test_source_package_contains_corresponding_source_without_repository_or_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source, dll = self.fixture(root)
            result = builder.build(dll, builder.digest(dll), source, root / 'package')
            manifest = json.loads((result / 'package.json').read_text())
            actual = {str(p.relative_to(result)): builder.digest(p) for p in result.rglob('*')
                      if p.is_file() and p != result / 'package.json'}
            self.assertEqual(actual, manifest['files'])
            self.assertTrue((result / 'corresponding-source/patch/src/lib.rs').is_file())
            self.assertFalse(list(result.rglob('.git')))
            self.assertFalse(list(result.rglob('account-database.json')))
            patch_manifest = json.loads((result / 'patch-manifest.json').read_text())
            self.assertEqual(builder.digest(dll), patch_manifest['patch']['sha256'])

    def test_bad_checksum_and_existing_output_never_start_a_build_or_replace_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source, dll = self.fixture(root)
            with patch.object(builder.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    builder.build(dll, '0' * 64, source, root / 'new', frozen=True)
                existing = root / 'existing'; existing.mkdir()
                (existing / 'keep').write_text('preserve')
                with self.assertRaisesRegex(ValueError, 'exists'):
                    builder.build(dll, builder.digest(dll), source, existing, frozen=True)
                run.assert_not_called()
            self.assertFalse((root / 'new').exists())
            self.assertEqual('preserve', (existing / 'keep').read_text())

    def test_offline_source_preserves_compiler_flags_and_declared_source_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source, dll = self.fixture(root)
            (source / '.cargo').mkdir()
            config = source / '.cargo/config.toml'
            config.write_text('[target.x86_64-pc-windows-gnullvm]\nrustflags=["-C", "target-feature=+crt-static"]\n')
            archive = root / 'dependencies.tar.gz'
            data = json.dumps({'schema': 1, 'cargoLockSha256': builder.digest(source / 'Cargo.lock')}).encode()
            with tarfile.open(archive, 'w:gz') as target:
                entry = tarfile.TarInfo('dependencies.json'); entry.size = len(data)
                target.addfile(entry, io.BytesIO(data))
            result = builder.build(dll, builder.digest(dll), source, root / 'package', dependency_archive=archive)
            corresponding = result / 'corresponding-source/patch'
            manifest = json.loads((result / 'patch-manifest.json').read_text())
            for name, wanted in manifest['sourceFiles'].items():
                self.assertEqual(wanted, builder.digest(corresponding / name), name)
            self.assertEqual(config.read_bytes(), (corresponding / '.cargo/config.toml').read_bytes())
            self.assertIn('vendored-sources', (corresponding / '.cargo/vendor.toml').read_text())

    def test_frozen_release_requires_corresponding_dependency_source_before_building(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source, dll = self.fixture(root)
            with patch.object(builder.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'dependency source archive'):
                    builder.build(dll, builder.digest(dll), source, root / 'release', frozen=True)
                run.assert_not_called()
            self.assertFalse((root / 'release').exists())


if __name__ == '__main__':
    unittest.main()

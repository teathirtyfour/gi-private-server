import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from contextlib import nullcontext
from unittest.mock import Mock
import zipfile

spec = importlib.util.spec_from_file_location('server_manage', Path(__file__).parents[1] / 'distribution/server/manage.py')
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


class ServerManagementTest(unittest.TestCase):
    def archive(self, path, files):
        with tarfile.open(path, 'w:gz') as package:
            for name, data in files.items():
                entry = tarfile.TarInfo(name); entry.size = len(data)
                package.addfile(entry, io.BytesIO(data))

    def test_backup_hashes_cover_nested_files_even_when_they_share_the_manifest_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {'database.archive.gz': b'database fixture', 'data/backup.json': b'custom data'}
            metadata = {'schema': 1, 'serverSha256': 'fixture',
                        'files': {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}}
            files['backup.json'] = json.dumps(metadata).encode()
            self.archive(root / 'backup.tar.gz', files)
            destination = root / 'restored'; destination.mkdir()
            self.assertEqual(metadata, manage.Deployment._unpack(root / 'backup.tar.gz', destination))
            self.assertEqual(b'custom data', (destination / 'data/backup.json').read_bytes())

    def test_path_traversal_and_links_are_rejected_before_any_database_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['../outside', '/absolute', 'data/../../outside', 'data\\outside']:
                self.archive(root / 'bad.tar.gz', {name: b'must not write outside'})
                target = root / 'target'; target.mkdir(exist_ok=True)
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Unsafe'):
                    manage.Deployment._unpack(root / 'bad.tar.gz', target)
            with tarfile.open(root / 'link.tar.gz', 'w:gz') as package:
                member = tarfile.TarInfo('data/link'); member.type = tarfile.SYMTYPE; member.linkname = '/etc/passwd'
                package.addfile(member)
            with self.assertRaisesRegex(ValueError, 'link'):
                manage.Deployment._unpack(root / 'link.tar.gz', root / 'target')
            self.assertFalse((root / 'outside').exists())

    def test_corrupt_data_is_rejected_before_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {'database.archive.gz': b'altered', 'data/value': b'fixture'}
            metadata = {'schema': 1, 'serverSha256': 'fixture', 'files': {k: '0' * 64 for k in files}}
            files['backup.json'] = json.dumps(metadata).encode()
            self.archive(root / 'bad.tar.gz', files)
            target = root / 'target'; target.mkdir()
            with self.assertRaisesRegex(ValueError, 'verification'):
                manage.Deployment._unpack(root / 'bad.tar.gz', target)

    def test_restore_requires_explicit_replace_intent_before_accessing_the_deployment(self):
        deployment = manage.Deployment.__new__(manage.Deployment)
        with self.assertRaisesRegex(ValueError, '--replace'):
            deployment.restore(Path('not-opened.tar.gz'))

    def test_failed_upgrade_keeps_previous_build_and_progress_and_stops_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['server.jar', 'next.jar']:
                with zipfile.ZipFile(root / name, 'w') as package:
                    package.writestr('emu/grasscutter/auth/StrictPasswordAuthenticator.class', name)
            previous = (root / 'server.jar').read_bytes()
            deployment = manage.Deployment.__new__(manage.Deployment)
            deployment.root = root
            deployment.metadata = {'projectName': 'genshin-fixture', 'serverSha256': manage.checksum(root / 'server.jar')}
            (root / 'deployment.json').write_text(json.dumps(deployment.metadata))
            deployment.operation = nullcontext
            deployment.maintenance = Mock(return_value=nullcontext())
            deployment.compose = ['fixture-compose']
            def snapshot(destination):
                # Snapshot is taken before replacing the running build selection.
                self.assertEqual(previous, (root / 'server.jar').read_bytes())
                destination.write_bytes(b'saved progress fixture')
            deployment._snapshot = snapshot
            def run(command, **kwargs):
                if 'build' in command:
                    raise RuntimeError('image build failed')
            deployment.run = Mock(side_effect=run)
            with self.assertRaisesRegex(RuntimeError, 'image build failed'):
                deployment.change_build(root / 'next.jar', manage.checksum(root / 'next.jar'))
            history, = (root / 'build-history').iterdir()
            self.assertEqual(previous, (history / 'server.jar').read_bytes())
            self.assertEqual(b'saved progress fixture', (history / 'progress.tar.gz').read_bytes())
            self.assertIn('failed', json.loads((history / 'change.json').read_text())['state'])
            deployment.run.assert_called_with(['fixture-compose', 'stop', 'proxy', 'game'], check=False)

    def test_rollback_requires_explicit_replace_before_opening_history(self):
        deployment = manage.Deployment.__new__(manage.Deployment)
        with self.assertRaisesRegex(ValueError, '--replace'):
            deployment.rollback(Path('not-opened'), replace=False)


if __name__ == '__main__':
    unittest.main()

import hashlib
import base64
import io
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('portable_client', Path(__file__).parents[1] / 'distribution/client/client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class PortableClientTest(unittest.TestCase):
    def fixture(self, root):
        source = root / 'normal'
        source.mkdir()
        (source / 'GenshinImpact.exe').write_bytes(b'executable fixture')
        for name in client.PATCH_PATHS:
            p = source / name; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'original DLL')
        (source / 'config.ini').write_text('normal settings')
        cache = source / 'GenshinImpact_Data/SDKCaches/HoYoPass'
        cache.mkdir(parents=True); (cache / 'session').write_bytes(b'normal session')
        dll = root / 'patch.dll'; dll.write_bytes(b'private patch fixture')
        expected = {'GenshinImpact.exe': client.digest(source / 'GenshinImpact.exe')}
        return source, dll, client.digest(dll), expected

    def shared_fixture(self, root):
        source, dll, sha, expected = self.fixture(root)
        assets = source / client.SHARED_ASSETS
        assets.mkdir()
        (assets / 'bundle.blk').write_bytes(b'asset fixture')
        (assets / 'audio').mkdir()
        (assets / 'audio/voice.pck').write_bytes(b'voice fixture')
        return source, dll, sha, expected

    def test_shared_preparation_omits_asset_copy_and_detects_source_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, dll, sha, expected = self.shared_fixture(root)
            dest = client.prepare(source, root / 'private', dll, sha, expected=expected, storage='shared')
            self.assertTrue(os.path.samefile(source / client.SHARED_ASSETS / 'bundle.blk',
                                             dest / client.SHARED_ASSETS / 'bundle.blk'))
            client.verify_private(dest, sha, expected=expected)
            # Patch changes and recovery must preserve the asset mapping.
            next_patch = root / 'next.dll'; next_patch.write_bytes(b'next patch')
            client.update_patch(dest, next_patch, client.digest(next_patch), expected=expected)
            client.restore_patch(dest, expected=expected)
            client.verify_private(dest, sha, expected=expected)
            (source / client.SHARED_ASSETS / 'bundle.blk').write_bytes(b'official update')
            with self.assertRaisesRegex(ValueError, 'Shared assets changed'):
                client.verify_private(dest, sha, expected=expected)

    def test_missing_shared_protection_record_blocks_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, dll, sha, expected = self.shared_fixture(root)
            dest = client.prepare(source, root / 'private', dll, sha, expected=expected, storage='shared')
            (dest / '.private-launcher/shared-assets.json').unlink()
            with self.assertRaisesRegex(ValueError, 'protection record is missing'):
                client.verify_private(dest, sha, expected=expected)

    def test_shared_space_estimate_excludes_assets_and_keeps_persistent_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, dll, sha, expected = self.shared_fixture(root)
            persistent = source / 'GenshinImpact_Data/Persistent'
            persistent.mkdir(); (persistent / 'cache').write_bytes(b'private writable cache')
            # A sparse logical asset is much larger than the fake free space.
            with (source / client.SHARED_ASSETS / 'large.blk').open('wb') as f:
                f.truncate(100_000_000)
            usage = type('Usage', (), {'free': 1_000_000})()
            with patch.object(client.shutil, 'disk_usage', return_value=usage):
                with self.assertRaisesRegex(ValueError, 'Insufficient space'):
                    client.prepare(source, root / 'full', dll, sha, expected=expected)
                dest = client.prepare(source, root / 'private', dll, sha, expected=expected, storage='shared')
            (dest / 'GenshinImpact_Data/Persistent/cache').write_bytes(b'new private state')
            self.assertEqual(b'private writable cache', (persistent / 'cache').read_bytes())

    @unittest.skipIf(os.name == 'nt', 'Legacy Linux migration')
    def test_adopt_legacy_client_preserves_settings_and_detaches_writable_files(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, dll, sha, expected = self.shared_fixture(root)
            before = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob('*') if p.is_file()}
            dest = root / 'legacy'
            shutil.copytree(source, dest)
            for p in (dest / client.SHARED_ASSETS).rglob('*'):
                if p.is_file():
                    relative = p.relative_to(dest); p.unlink(); p.symlink_to(source / relative)
            for name in client.PATCH_PATHS:
                shutil.copyfile(dll, dest / name)
            extra = dest / 'linked-config'
            extra.symlink_to(source / 'config.ini')
            # A link pointing to a different relative source is refused first.
            with self.assertRaisesRegex(ValueError, 'selected source'):
                client.adopt_shared(source, dest, sha, expected=expected)
            extra.unlink()
            (dest / 'config.ini').unlink(); (dest / 'config.ini').symlink_to(source / 'config.ini')
            for name in client.PATCH_PATHS:
                shutil.copyfile(dll, dest / name)
            client.adopt_shared(source, dest, sha, expected=expected)
            client.verify_private(dest, sha, expected=expected)
            self.assertTrue((dest / '.private-launcher/legacy-assets/bundle.blk').is_symlink())
            self.assertFalse((dest / 'config.ini').is_symlink())
            (dest / 'config.ini').write_text('private graphics settings')
            self.assertEqual(before, {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob('*') if p.is_file()})

    @unittest.skipIf(os.name == 'nt', 'Linux read-only mount integration')
    def test_shared_linux_launch_blocks_writes_to_source(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, dll, sha, expected = self.shared_fixture(root)
            dest = client.prepare(source, root / 'private', dll, sha, expected=expected, storage='shared')
            real_run = subprocess.run
            def probe(command, **kwargs):
                sandbox = command[:command.index('--') + 1]
                code = ("from pathlib import Path; "
                        f"p=Path({str(dest / client.SHARED_ASSETS / 'bundle.blk')!r}); "
                        "assert p.read_bytes()==b'asset fixture'; "
                        f"Path({str(dest / 'writable')!r}).write_text('ok'); "
                        "p.write_bytes(b'must be denied')")
                result = real_run([*sandbox, sys.executable, '-c', code], capture_output=True)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(b'Read-only file system', result.stderr)
                return type('Result', (), {'returncode': 0})()
            with patch.object(client.subprocess, 'run', side_effect=probe):
                client.launch(dest, 'http://127.0.0.1:8088', sha, sys.executable, expected=expected)
            self.assertEqual('ok', (dest / 'writable').read_text())
            self.assertEqual(b'asset fixture', (source / client.SHARED_ASSETS / 'bundle.blk').read_bytes())

    @unittest.skipIf(os.name == 'nt' or not Path('/dev/shm').is_dir(), 'Linux cross-filesystem integration')
    def test_shared_assets_work_across_filesystems_without_copying(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as original, tempfile.TemporaryDirectory() as directory:
            source, dll, sha, expected = self.shared_fixture(Path(original))
            root = Path(directory).resolve()
            if source.stat().st_dev == root.stat().st_dev:
                self.skipTest('Two different mounted filesystems are required')
            dest = client.prepare(source, root / 'private', dll, sha, expected=expected, storage='shared')
            client.verify_private(dest, sha, expected=expected)
            self.assertTrue(os.path.samefile(source / client.SHARED_ASSETS / 'bundle.blk',
                                             dest / client.SHARED_ASSETS / 'bundle.blk'))

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing-mode integration')
    def test_shared_windows_guard_denies_writes_and_deletion_then_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, dll, sha, expected = self.shared_fixture(root)
            dest = client.prepare(source, root / 'private', dll, sha, expected=expected, storage='shared')
            path = dest / client.SHARED_ASSETS / 'bundle.blk'
            with client.protect_shared_assets(dest, expected=expected):
                self.assertEqual(b'asset fixture', path.read_bytes())
                with self.assertRaises(OSError):
                    path.write_bytes(b'blocked')
                with self.assertRaises(OSError):
                    path.unlink()
                (dest / client.SHARED_ASSETS / 'new-file').write_bytes(b'private overlay')
                self.assertFalse((source / client.SHARED_ASSETS / 'new-file').exists())
            self.assertEqual(b'asset fixture', path.read_bytes())
            path.write_bytes(b'lock released')

    def test_private_copy_has_independent_files_without_normal_login(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source, dll, sha, expected = self.fixture(root)
            before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob('*') if p.is_file()}
            dest = client.prepare(source, root / 'private', dll, sha, expected=expected)
            self.assertEqual(dest, client.verify_private(dest, sha, expected=expected))
            self.assertFalse((dest / 'GenshinImpact_Data/SDKCaches').exists())
            self.assertEqual(before, {str(p.relative_to(source)): p.read_bytes() for p in source.rglob('*') if p.is_file()})
            for name in client.PATCH_PATHS:
                self.assertEqual(b'private patch fixture', (dest / name).read_bytes())
                self.assertNotEqual((source / name).stat().st_ino, (dest / name).stat().st_ino)
            self.assertEqual(b'original DLL', (dest / '.private-launcher/originals/Astrolabe-0.dll').read_bytes())

    def test_existing_destination_and_patch_corruption_do_not_touch_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source, dll, sha, expected = self.fixture(root)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                client.prepare(source, source, dll, sha, expected=expected)
            with self.assertRaisesRegex(ValueError, 'checksum'):
                client.prepare(source, root / 'private', dll, '0' * 64, expected=expected)
            self.assertFalse((root / 'private').exists())
            self.assertEqual(b'original DLL', (source / 'Astrolabe.dll').read_bytes())

    def test_normal_install_can_lack_root_patch_and_be_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source, dll, sha, expected = self.fixture(root)
            (source / 'Astrolabe.dll').unlink()
            if os.name != 'nt':
                for path in source.rglob('*'):
                    path.chmod(0o555 if path.is_dir() else 0o444)
                source.chmod(0o555)
            destination = client.prepare(source, root / 'private', dll, sha, expected=expected)
            client.verify_private(destination, sha, expected=expected)
            originals = json.loads((destination / '.private-launcher/originals/files.json').read_text())
            self.assertIsNone(originals['Astrolabe.dll'])
            self.assertFalse((source / 'Astrolabe.dll').exists())
            if os.name != 'nt':
                self.assertFalse(source.stat().st_mode & 0o200)
                self.assertTrue(destination.stat().st_mode & 0o200)
                self.assertTrue((destination / client.PATCH_PATHS[1]).stat().st_mode & 0o200)

    def test_patch_update_and_restore_keep_game_files_login_and_original_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source, dll, sha, expected = self.fixture(root)
            destination = client.prepare(source, root / 'private', dll, sha, expected=expected)
            next_patch = root / 'next.dll'; next_patch.write_bytes(b'next private patch')
            next_sha = client.digest(next_patch)
            recovery = client.update_patch(destination, next_patch, next_sha, expected=expected)
            client.verify_private(destination, next_sha, expected=expected)
            self.assertEqual(dll.read_bytes(), (recovery / 'Astrolabe-0.dll').read_bytes())
            self.assertEqual(sha, client.restore_patch(destination, expected=expected))
            client.verify_private(destination, sha, expected=expected)
            self.assertEqual(b'original DLL', (destination / '.private-launcher/originals/Astrolabe-0.dll').read_bytes())
            self.assertEqual(b'original DLL', (source / 'Astrolabe.dll').read_bytes())

    def test_interrupted_two_file_patch_change_blocks_play_and_can_be_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source, dll, sha, expected = self.fixture(root)
            destination = client.prepare(source, root / 'private', dll, sha, expected=expected)
            next_patch = root / 'next.dll'; next_patch.write_bytes(b'next private patch')
            real_copy = client.atomic_copy
            calls = 0
            def fail_second(source, target):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError('simulated interrupted update')
                real_copy(source, target)
            with patch.object(client, 'atomic_copy', side_effect=fail_second):
                with self.assertRaisesRegex(OSError, 'simulated'):
                    client.update_patch(destination, next_patch, client.digest(next_patch), expected=expected)
            with patch.object(client.subprocess, 'run') as launch, patch.object(client.shutil, 'which', return_value='/fixture/wine'):
                with self.assertRaisesRegex(ValueError, 'interrupted'):
                    client.launch(destination, 'https://play.example.org', sha, 'wine', expected=expected)
                launch.assert_not_called()
            client.restore_patch(destination, expected=expected)
            client.verify_private(destination, sha, expected=expected)
            self.assertFalse((destination / '.private-launcher/patch-pending.json').exists())

    def test_updates_cannot_change_a_running_clients_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source, dll, sha, expected = self.fixture(root)
            destination = client.prepare(source, root / 'private', dll, sha, expected=expected)
            with client.client_lock(destination):
                with self.assertRaisesRegex(ValueError, 'already running'):
                    client.update_patch(destination, dll, sha, expected=expected)
            client.verify_private(destination, sha, expected=expected)

    @unittest.skipIf(os.name == 'nt', 'Linux frozen-library environment check')
    def test_frozen_launcher_restores_system_libraries_and_uses_its_own_wine_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source, dll, sha, expected = self.fixture(root)
            destination = client.prepare(source, root / 'private', dll, sha, expected=expected)
            inherited = {'LD_LIBRARY_PATH': '/frozen/libraries', 'LD_LIBRARY_PATH_ORIG': '/system/libraries',
                         'WINEDLLPATH': '/other/game', 'WINESERVER': '/other/wineserver',
                         'WINEARCH': 'win32', 'WINEDLLOVERRIDES': 'Astrolabe='}
            with patch.dict(client.os.environ, inherited), patch.object(client.sys, 'frozen', True, create=True), \
                 patch.object(client.shutil, 'which', return_value='/fixture/wine'), patch.object(client.subprocess, 'run') as run:
                run.return_value.returncode = 0
                client.launch(destination, 'https://play.example.org', sha, 'wine', expected=expected)
                environment = run.call_args.kwargs['env']
                self.assertEqual('/system/libraries', environment['LD_LIBRARY_PATH'])
                self.assertNotIn('LD_LIBRARY_PATH_ORIG', environment)
                self.assertNotIn('WINEDLLPATH', environment)
                self.assertNotIn('WINESERVER', environment)
                self.assertEqual('win64', environment['WINEARCH'])
                self.assertEqual('Astrolabe=n,b', environment['WINEDLLOVERRIDES'])

    def test_directory_picker_empty_folder_is_accepted_but_racing_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source, dll, sha, expected = self.fixture(root)
            destination = root / 'private'; destination.mkdir()
            client.prepare(source, destination, dll, sha, expected=expected)
            client.verify_private(destination, sha, expected=expected)
            other = root / 'other'; other.mkdir()
            def racing_writer(done, total):
                (other / 'preserve.txt').write_text('unrelated file')
            with self.assertRaisesRegex(ValueError, 'Destination changed'):
                client.prepare(source, other, dll, sha, racing_writer, expected=expected)
            self.assertEqual('unrelated file', (other / 'preserve.txt').read_text())

    def test_cancelled_copy_leaves_no_partial_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source, dll, sha, expected = self.fixture(root)
            def cancel(done, total):
                raise client.CopyCancelled('cancelled')
            with self.assertRaises(client.CopyCancelled):
                client.prepare(source, root / 'private', dll, sha, cancel, expected=expected)
            self.assertFalse((root / 'private').exists())
            self.assertFalse(list(root.glob('.private-client-*')))
            self.assertEqual(b'original DLL', (source / 'Astrolabe.dll').read_bytes())

    @unittest.skipIf(os.name == 'nt', 'Requires symlink creation privileges on Windows')
    def test_source_links_and_private_patch_hardlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source, dll, sha, expected = self.fixture(root)
            (source / 'external-link').symlink_to(dll)
            with self.assertRaisesRegex(ValueError, 'contains a link'):
                client.prepare(source, root / 'private', dll, sha, expected=expected)
            (source / 'external-link').unlink()
            dest = client.prepare(source, root / 'private', dll, sha, expected=expected)
            os.link(dest / 'Astrolabe.dll', root / 'shared-dll')
            with self.assertRaisesRegex(ValueError, 'shared'):
                client.verify_private(dest, sha, expected=expected)

    def test_unsupported_client_and_racing_update_leave_no_private_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source, dll, sha, expected = self.fixture(root)
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                client.prepare(source, root / 'private', dll, sha, expected={'GenshinImpact.exe': '0' * 64})
            changed = False
            def update_source(done, total):
                nonlocal changed
                if not changed:
                    # Simulate a mixed-version copy after the initial source check.
                    for p in root.glob('.private-client-*/client/GenshinImpact.exe'):
                        p.write_bytes(b'changed executable'); changed = True
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                client.prepare(source, root / 'private', dll, sha, update_source, expected=expected)
            self.assertFalse((root / 'private').exists())
            self.assertFalse(list(root.glob('.private-client-*')))

    def test_launch_uses_private_prefix_and_endpoint_and_resets_only_private_login(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source, dll, sha, expected = self.fixture(root)
            dest = client.prepare(source, root / 'private', dll, sha, expected=expected)
            cache = dest / 'GenshinImpact_Data/SDKCaches/HoYoPass'
            cache.mkdir(parents=True); (cache / 'session').write_bytes(b'private session')
            with patch.object(client.shutil, 'which', return_value='/fixture/wine'), patch.object(client.subprocess, 'run') as run:
                run.return_value.returncode = 0
                self.assertEqual(0, client.launch(dest, 'https://play.example.org/', sha, 'wine', expected=expected))
                self.assertEqual('https://play.example.org', run.call_args.kwargs['env']['PRIVATE_SERVER_URL'])
                self.assertEqual(dest, run.call_args.kwargs['cwd'])
                if os.name != 'nt':
                    self.assertEqual(str(dest / '.private-launcher/wine-prefix'), run.call_args.kwargs['env']['WINEPREFIX'])
                self.assertFalse(list(cache.iterdir()))
            self.assertEqual(b'normal session', (source / 'GenshinImpact_Data/SDKCaches/HoYoPass/session').read_bytes())

    @unittest.skipIf(os.name == 'nt', 'POSIX lock contention exercised here; Windows CI still required')
    def test_duplicate_launch_does_not_reset_a_live_session(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source, dll, sha, expected = self.fixture(root)
            dest = client.prepare(source, root / 'private', dll, sha, expected=expected)
            cache = dest / 'GenshinImpact_Data/SDKCaches/HoYoPass'
            cache.mkdir(parents=True); (cache / 'session').write_bytes(b'keep live session')
            with (dest / '.private-launcher/launch.lock').open('a+b') as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(client.shutil, 'which', return_value='/fixture/wine'), patch.object(client.subprocess, 'run') as run:
                    with self.assertRaisesRegex(ValueError, 'already running'):
                        client.launch(dest, 'https://play.example.org', sha, 'wine', expected=expected)
                    run.assert_not_called()
                    self.assertEqual(b'keep live session', (cache / 'session').read_bytes())

    def test_server_url_validation_matches_patch_constraints(self):
        for value in ['http://localhost:8088', 'https://play.example.org', 'https://[2001:db8::1]:8443/']:
            self.assertEqual(value.rstrip('/'), client.server_origin(value))
        for value in ['file:///tmp', 'https://user:pass@host', 'http://host/path', 'http://host?',
                      'http://host#', 'http://host\n', 'http://host\\evil', 'http://host:0',
                      'http://host:65536', 'http://host:', 'http://a..b', 'http://-bad.org']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.server_origin(value)

    def test_diagnostics_accept_matching_72_dispatch_and_reject_wrong_advertised_host(self):
        def field(number, value):
            return bytes([number * 8 + 2, len(value)]) + value
        def payload(host):
            region = field(4, (host + '/query_cur_region/os_usa').encode())
            return base64.b64encode(field(2, region) + b'\x38\x01')
        class Response(io.BytesIO):
            status = 200
        class Opener:
            def __init__(self, body): self.body = body
            def open(self, request, timeout): return Response(self.body)
        result = client.check_server('https://play.example.org', opener=Opener(payload('https://play.example.org:443')))
        self.assertEqual({'loginReachable': True, 'regions': 1, 'gameConnectionTested': False}, result)
        with self.assertRaisesRegex(ValueError, 'different login address'):
            client.check_server('https://play.example.org', opener=Opener(payload('http://127.0.0.1:8088')))
        for body in [b'not base64', base64.b64encode(b'\x12\xff'), base64.b64encode(b'\x38\x01'), b'x' * (128 * 1024 + 1)]:
            with self.subTest(body=body[:20]), self.assertRaises(ValueError):
                client.check_server('https://play.example.org', opener=Opener(body))


if __name__ == '__main__':
    unittest.main()

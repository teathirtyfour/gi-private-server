"""Opt-in real Tk checks; run under Xvfb with PRIVATE_LAUNCHER_UI_TEST=1."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('PRIVATE_LAUNCHER_UI_TEST') == '1', 'Requires Tk and a virtual display')
class LauncherUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = Path(__file__).parents[1] / 'distribution/client'
        sys.path.insert(0, str(directory))
        try:
            spec = importlib.util.spec_from_file_location('launcher_ui', directory / 'launcher.py')
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)
        finally:
            sys.path.remove(str(directory))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.patch = self.directory / 'Astrolabe.dll'
        self.patch.write_bytes(b'UI fixture patch')
        self.sha = self.module.client.digest(self.patch)
        (self.directory / 'patch-manifest.json').write_text(json.dumps({
            'schema': 1, 'gameVersion': '7.2.0',
            'patch': {'file': 'Astrolabe.dll', 'sha256': self.sha}}))

    def application(self):
        root = self.module.tk.Tk()
        root.withdraw()
        app = self.module.Launcher(root, self.patch, self.sha, self.directory / 'settings.json')
        self.addCleanup(app.close)
        errors = patch.object(self.module.messagebox, 'showerror', side_effect=AssertionError('Unexpected UI error'))
        errors.start(); self.addCleanup(errors.stop)
        return root, app

    def drain(self, root, app):
        deadline = time.monotonic() + 5
        while app.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        self.assertFalse(app.busy, 'UI worker did not finish')

    def test_release_manifest_is_checked_before_game_actions(self):
        self.assertEqual((self.patch, self.sha), self.module.load_release(self.directory))
        self.patch.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.module.load_release(self.directory)

    def test_import_and_play_bindings_never_launch_a_real_game(self):
        root, app = self.application()
        profile = self.directory / 'player-server.json'
        profile.write_text(json.dumps({'gameVersion': '7.2.0', 'url': 'https://play.example.org/'}))
        with patch.object(self.module.filedialog, 'askopenfilename', return_value=str(profile)):
            app.import_profile()
        self.assertEqual('https://play.example.org', app.url.get())
        app.folder.set(str(self.directory / 'private'))
        app.wine.set('/fixture/wine')
        app.remember.set(True)
        with patch.object(self.module.client, 'launch', return_value=0) as launch:
            app.play()
            self.assertTrue(app.busy)
            self.drain(root, app)
            launch.assert_called_once_with(str(self.directory / 'private'), 'https://play.example.org',
                                           self.sha, '/fixture/wine', True, profile=None)
        self.assertFalse(app.remember.get())
        self.assertIn('Ready to play', app.status.get())
        settings = json.loads((self.directory / 'settings.json').read_text())
        self.assertEqual('https://play.example.org', settings['server'])

    def test_prepare_progress_and_buttons_drive_real_fixture_copy(self):
        root, app = self.application()
        source = self.directory / 'normal'
        source.mkdir()
        (source / 'GenshinImpact.exe').write_bytes(b'UI executable fixture')
        assets = source / self.module.client.SHARED_ASSETS
        assets.mkdir(parents=True)
        (assets / 'bundle.blk').write_bytes(b'UI asset fixture')
        for name in self.module.client.PATCH_PATHS:
            p = source / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'original')
        app.source.set(str(source)); app.folder.set(str(self.directory / 'private'))
        expected = {'GenshinImpact.exe': self.module.client.digest(source / 'GenshinImpact.exe')}
        with patch.dict(self.module.client.INPUTS, expected, clear=True):
            app.prepare()
            self.assertTrue(app.busy)
            self.drain(root, app)
            self.assertIn('Private copy prepared', app.status.get())
            self.assertEqual(100, app.progress['value'])
            app.check()
            self.drain(root, app)
            self.assertIn('match this release', app.status.get())
            old_sha = self.sha
            self.patch.write_bytes(b'updated UI fixture patch')
            app.checksum = self.module.client.digest(self.patch)
            app.update_patch()
            self.drain(root, app)
            self.module.client.verify_private(self.directory / 'private', app.checksum)
            app.restore_patch()
            self.drain(root, app)
            self.module.client.verify_private(self.directory / 'private', old_sha)
            self.assertIn('matching older launcher', app.status.get())
        self.assertEqual(b'original', (source / 'Astrolabe.dll').read_bytes())

    def test_connection_button_checks_login_without_starting_the_game(self):
        root, app = self.application()
        app.url.set('https://play.example.org')
        result = {'loginReachable': True, 'regions': 1, 'gameConnectionTested': False}
        with patch.object(self.module.client, 'check_server', return_value=result) as check, \
             patch.object(self.module.client, 'launch') as launch:
            button = next(b for b in app.buttons if b['text'] == 'Test connection')
            button.invoke()
            self.drain(root, app)
            check.assert_called_once_with('https://play.example.org')
            launch.assert_not_called()
        self.assertIn('Login server reachable', app.status.get())
        self.assertIn('game connection is checked when you enter', app.status.get())


if __name__ == '__main__':
    unittest.main()

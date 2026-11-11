import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('portable_login', Path(__file__).parents[1] / 'distribution/client/client.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class PrivateLoginResetTest(unittest.TestCase):
    def test_only_login_cache_moves_and_can_be_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = root / 'runtime/client'
            cache = client / 'GenshinImpact_Data/SDKCaches/HoYoPass'
            cache.mkdir(parents=True)
            (cache / 'session').write_bytes(b'saved-private-session')
            settings = client / 'config.ini'
            settings.write_bytes(b'graphics-settings')
            backup = launcher.quarantine_login(client)
            self.assertEqual(b'saved-private-session', (backup / 'session').read_bytes())
            self.assertEqual([], list(cache.iterdir()))
            self.assertEqual(b'graphics-settings', settings.read_bytes())
            self.assertIsNone(launcher.quarantine_login(client))

    def test_redirected_cache_is_refused_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = root / 'runtime/client'
            cache = client / 'GenshinImpact_Data/SDKCaches/HoYoPass'
            cache.parent.mkdir(parents=True)
            outside = root / 'normal-client-cache'
            outside.mkdir()
            (outside / 'session').write_bytes(b'preserve')
            cache.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                launcher.quarantine_login(client)
            self.assertEqual(b'preserve', (outside / 'session').read_bytes())

    def test_hardlinked_cache_file_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = root / 'runtime/client'
            cache = client / 'GenshinImpact_Data/SDKCaches/HoYoPass'
            cache.mkdir(parents=True)
            outside = root / 'original-session'
            outside.write_bytes(b'preserve')
            (cache / 'session').hardlink_to(outside)
            with self.assertRaises(ValueError):
                launcher.quarantine_login(client)
            self.assertEqual(b'preserve', outside.read_bytes())


if __name__ == '__main__':
    unittest.main()

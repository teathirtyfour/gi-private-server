import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import Mock

PATH = Path(__file__).parents[1] / 'distribution/server/setup.py'
spec = importlib.util.spec_from_file_location('deployment_setup', PATH)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class DeploymentSetupTest(unittest.TestCase):
    def test_download_setup_waits_for_prepared_inputs_and_retains_only_the_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); jar = root / 'server.jar'; deployment = root / 'deployment'
            with zipfile.ZipFile(jar, 'w') as package:
                package.writestr('emu/grasscutter/auth/StrictPasswordAuthenticator.class', b'fixture')
            def downloader(output, cache):
                self.assertFalse(deployment.exists())
                for name in ['ExcelBinOutput', 'BinOutput', 'Scripts', 'TextMap']:
                    (output / name).mkdir(parents=True)
                (output / 'Scripts/verified.lua').write_text('downloaded fixture')
                return output
            setup.prepare_downloaded(deployment, 'play.example.org', 'play.example.org', jar, downloader=downloader)
            self.assertEqual('downloaded fixture', (deployment / 'resources/Scripts/verified.lua').read_text())
            self.assertFalse(list(root.glob('.downloaded-resources-*')))

    def test_failed_download_does_not_create_a_deployment_or_leave_partial_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); jar = root / 'server.jar'
            with zipfile.ZipFile(jar, 'w') as package:
                package.writestr('emu/grasscutter/auth/StrictPasswordAuthenticator.class', b'fixture')
            downloader = Mock(side_effect=OSError('network unavailable'))
            with self.assertRaisesRegex(OSError, 'network unavailable'):
                setup.prepare_downloaded(root / 'deployment', 'play.example.org', 'play.example.org', jar, downloader=downloader)
            self.assertFalse((root / 'deployment').exists())
            self.assertFalse(list(root.glob('.downloaded-resources-*')))

    def test_download_setup_rejects_bad_inputs_before_network_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); jar = root / 'server.jar'
            with zipfile.ZipFile(jar, 'w') as package:
                package.writestr('emu/grasscutter/auth/StrictPasswordAuthenticator.class', b'fixture')
            downloader = Mock()
            with self.assertRaisesRegex(ValueError, 'Invalid|hostname'):
                setup.prepare_downloaded(root / 'deployment', 'https://bad', 'bad', jar, downloader=downloader)
            (root / 'existing').mkdir()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                setup.prepare_downloaded(root / 'existing', 'play.example.org', 'play.example.org', jar, downloader=downloader)
            downloader.assert_not_called()

    def test_public_routes_match_tls_proxy_and_udp_endpoint(self):
        c = setup.configuration('Play.Example.org', 'game.example.org', 's' * 48, 22222)
        http, game = c['server']['http'], c['server']['game']
        self.assertEqual(('play.example.org', 443), (http['accessAddress'], http['accessPort']))
        self.assertEqual({'useEncryption': False, 'useInRouting': True}, http['encryption'])
        self.assertEqual(('game.example.org', 22222), (game['accessAddress'], game['accessPort']))
        self.assertFalse(c['account']['autoCreate'])
        self.assertTrue(c['account']['strictPasswordChecks'])
        self.assertFalse(http['webConsole']['enabled'])
        self.assertEqual([], c['account']['defaultPermissions'])
        for value in c['databaseInfo'].values():
            self.assertEqual('genshin', value['collection'])
            self.assertIn('@database:27017/genshin?authSource=genshin', value['connectionUri'])

    def test_domains_cannot_inject_configuration(self):
        for value in ['https://play.example.org', 'evil.org\nreverse_proxy other', '127.0.0.1',
                      'localhost', 'game.local', '-game.example.org', 'a..b', 'game.org:443',
                      'game.org/path', 'user@game.org', 'game.org{', 'game.org#comment']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                setup.public_domain(value)
        for value in [0, -1, 65536, 8088.5, True]:
            with self.subTest(port=value), self.assertRaises(ValueError):
                setup.configuration('play.example.org', 'game.example.org', 's' * 48, value)

    def test_preparation_is_isolated_and_refuses_existing_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jar = root / 'input.jar'
            with zipfile.ZipFile(jar, 'w') as package:
                package.writestr('emu/grasscutter/auth/StrictPasswordAuthenticator.class', b'fixture class, never launched')
            resources = root / 'input-resources'
            for folder in ['ExcelBinOutput', 'BinOutput', 'Scripts', 'TextMap']:
                (resources / folder).mkdir(parents=True)
            result = setup.prepare(root / 'deployment', 'play.example.org', 'game.example.org', jar, resources)
            self.assertEqual(jar.read_bytes(), (result / 'server.jar').read_bytes())
            self.assertFalse((result / 'resources').is_symlink())
            self.assertTrue((result / 'resources/ExcelBinOutput').is_dir())
            config = json.loads((result / 'config.json').read_text())
            environment = dict(line.split('=', 1) for line in (result / '.env').read_text().splitlines())
            password = environment['GAME_DB_PASSWORD']
            self.assertIn(password, config['databaseInfo']['game']['connectionUri'])
            self.assertNotEqual(password, environment['MONGO_ADMIN_PASSWORD'])
            profile = json.loads((result / 'player-server.json').read_text())
            self.assertNotIn(password, json.dumps(profile))
            self.assertNotIn(environment['MONGO_ADMIN_PASSWORD'], json.dumps(profile))
            self.assertEqual('https://play.example.org', profile['url'])
            before = {p.name: p.read_bytes() for p in result.iterdir() if p.is_file()}
            with self.assertRaisesRegex(ValueError, 'already exists'):
                setup.prepare(result, 'different.example.org', 'game.example.org', jar, resources)
            self.assertEqual(before, {p.name: p.read_bytes() for p in result.iterdir() if p.is_file()})
            self.assertEqual([], list(root.glob('.server-setup-*')))

    def test_invalid_resources_leave_no_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jar, resources = root / 'server.jar', root / 'resources'
            with zipfile.ZipFile(jar, 'w') as package:
                package.writestr('emu/grasscutter/auth/StrictPasswordAuthenticator.class', b'fixture class')
            resources.mkdir()
            with self.assertRaisesRegex(ValueError, 'missing ExcelBinOutput'):
                setup.prepare(root / 'deployment', 'play.example.org', 'game.example.org', jar, resources)
            self.assertFalse((root / 'deployment').exists())

    def test_old_server_without_strict_authentication_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jar = root / 'old.jar'
            with zipfile.ZipFile(jar, 'w') as package:
                package.writestr('emu/grasscutter/Grasscutter.class', b'old fixture')
            resources = root / 'resources'; resources.mkdir()
            with self.assertRaisesRegex(ValueError, 'lacks strict public authentication'):
                setup.prepare(root / 'deployment', 'play.example.org', 'game.example.org', jar, resources)
            self.assertFalse((root / 'deployment').exists())

    @unittest.skipIf(__import__('os').name == 'nt', 'Linux deployment overlay materialization')
    def test_resource_overlay_links_are_copied_as_independent_container_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); jar = root / 'server.jar'; resources = root / 'overlay'
            with zipfile.ZipFile(jar, 'w') as package:
                package.writestr('emu/grasscutter/auth/StrictPasswordAuthenticator.class', b'fixture')
            for folder in ['ExcelBinOutput', 'BinOutput', 'Scripts', 'TextMap']:
                (resources / folder).mkdir(parents=True)
            native = root / 'native-table.json'; native.write_bytes(b'fixture resource')
            (resources / 'ExcelBinOutput/table.json').symlink_to(native)
            result = setup.prepare(root / 'deployment', 'play.example.org', 'game.example.org', jar, resources)
            copied = result / 'resources/ExcelBinOutput/table.json'
            self.assertFalse(copied.is_symlink())
            self.assertEqual(b'fixture resource', copied.read_bytes())
            self.assertNotEqual(native.stat().st_ino, copied.stat().st_ino)


if __name__ == '__main__':
    unittest.main()

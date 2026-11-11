import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('resource_prepare', Path(__file__).parents[1] / 'distribution/server/resources.py')
resources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resources)


class ResourcePreparationTest(unittest.TestCase):
    def repository(self, root):
        root.mkdir()
        (root / 'fixture.json').write_text('{"verified":"fixture"}')
        (root / 'TextMap').mkdir()
        (root / 'TextMap/ObsoleteSplit.json').write_text('{"old":"localization"}')
        for args in [('init', '-q'), ('add', 'fixture.json', 'TextMap'),
                     ('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')]:
            subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
        return subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()

    def download_recipe(self, root):
        inputs = {}
        for name in ['base', 'current']:
            repository = root / (name + '-origin')
            inputs[name] = {'revision': self.repository(repository), 'url': repository.as_uri()}
        recipe = root / 'download-recipe.json'
        resources.write(recipe, {'schema': 1, 'gameVersion': '7.2.0', 'inputs': inputs,
                                  'currentCopies': {}, 'derivedSha256': {}})
        return recipe

    def test_downloaded_pinned_inputs_work_from_cache_without_the_origins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); recipe = self.download_recipe(root)
            base, current = resources.download_inputs(root / 'cache', recipe)
            for name in ['base', 'current']:
                (root / (name + '-origin')).rename(root / (name + '-offline'))
            cached = resources.download_inputs(root / 'cache', recipe)
            self.assertEqual((base, current), cached)
            self.assertEqual('{"verified":"fixture"}', (base / 'fixture.json').read_text())

    def test_interrupted_fetch_can_retry_without_publishing_partial_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); recipe = self.download_recipe(root)
            run = resources.subprocess.run
            def interrupted(command, **kwargs):
                if 'fetch' in command:
                    raise subprocess.CalledProcessError(1, command)
                return run(command, **kwargs)
            with patch.object(resources.subprocess, 'run', side_effect=interrupted):
                with self.assertRaises(subprocess.CalledProcessError):
                    resources.download_prepare(root / 'output', root / 'cache', recipe)
            self.assertFalse((root / 'output').exists())
            with patch.object(resources, 'derive'):
                output = resources.download_prepare(root / 'output', root / 'cache', recipe)
            self.assertTrue((output / 'resource-manifest.json').is_file())
            self.assertEqual('{"verified":"fixture"}', (output / 'fixture.json').read_text())

    def test_downloader_refuses_an_unowned_cache_without_overwriting_its_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); recipe = self.download_recipe(root)
            cache = root / 'cache' / resources.digest(recipe); cache.mkdir(parents=True)
            (cache / 'keep.txt').write_text('unrelated data')
            with self.assertRaisesRegex(ValueError, 'not owned'):
                resources.download_inputs(root / 'cache', recipe)
            self.assertEqual('unrelated data', (cache / 'keep.txt').read_text())

    def test_only_tracked_inputs_are_copied_and_originals_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); base = root / 'base'; current = root / 'current'
            base_revision = self.repository(base); current_revision = self.repository(current)
            recipe = {'schema': 1, 'gameVersion': '7.2.0',
                      'inputs': {'base': {'revision': base_revision}, 'current': {'revision': current_revision}},
                      'currentCopies': {'copied.json': 'fixture.json', 'TextMap/Current.json': 'fixture.json'}, 'derivedSha256': {}}
            path = root / 'recipe.json'; resources.write(path, recipe)
            before = (base / 'fixture.json').read_bytes()
            with patch.object(resources, 'derive'):
                output = resources.prepare(base, current, root / 'output', path)
            self.assertFalse((output / '.git').exists())
            self.assertFalse((output / 'TextMap/ObsoleteSplit.json').exists())
            self.assertTrue((output / 'TextMap/Current.json').is_file())
            self.assertEqual(before, (output / 'copied.json').read_bytes())
            self.assertEqual(before, (base / 'fixture.json').read_bytes())
            manifest = json.loads((output / 'resource-manifest.json').read_text())
            self.assertEqual(resources.digest(output / 'copied.json'), manifest['files']['copied.json'])
            self.assertEqual(output, resources.verify_prepared(output, path))
            (output / 'copied.json').write_text('unexpected runtime edit')
            with self.assertRaisesRegex(ValueError, 'Prepared resource changed'):
                resources.verify_prepared(output, path)
            (output / 'copied.json').write_bytes(before)
            (output / 'extra.json').write_text('unrecorded file')
            with self.assertRaisesRegex(ValueError, 'inventory changed'):
                resources.verify_prepared(output, path)
            (output / 'extra.json').unlink()
            (base / 'fixture.json').write_text('modified input')
            with self.assertRaisesRegex(ValueError, 'local changes'):
                resources.prepare(base, current, root / 'other', path)
            self.assertFalse((root / 'other').exists())

    def test_table_merge_preserves_base_corrections_and_rejects_ambiguous_input(self):
        old = [{'id': 1, 'value': 'base correction'}]
        new = [{'id': 1, 'value': 'upstream'}, {'id': 2, 'value': 'new'}]
        merged, additions = resources.append_rows(old, new, ('id',))
        self.assertEqual('base correction', merged[0]['value'])
        self.assertEqual([new[1]], additions)
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            resources.append_rows(old, new + [new[1]], ('id',))

    def test_redirected_and_traversal_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['../outside', '/outside', 'data/../../outside', 'data\\outside']:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    resources.member(root, name)
            if os.name != 'nt':
                (root / 'redirect').symlink_to(root.parent)
                with self.assertRaisesRegex(ValueError, 'redirected'):
                    resources.member(root, 'redirect/outside')

    def test_generated_checksum_failure_leaves_no_published_or_partial_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); base = root / 'base'; current = root / 'current'
            recipe = {'schema': 1, 'gameVersion': '7.2.0',
                      'inputs': {'base': {'revision': self.repository(base)},
                                 'current': {'revision': self.repository(current)}},
                      'currentCopies': {}, 'derivedSha256': {'fixture.json': '0' * 64}}
            path = root / 'recipe.json'; resources.write(path, recipe)
            with patch.object(resources, 'derive'), self.assertRaisesRegex(ValueError, 'differs'):
                resources.prepare(base, current, root / 'output', path)
            self.assertFalse((root / 'output').exists())
            self.assertFalse(list(root.glob('.resources-*')))
            self.assertEqual('{"verified":"fixture"}', (base / 'fixture.json').read_text())


if __name__ == '__main__':
    unittest.main()

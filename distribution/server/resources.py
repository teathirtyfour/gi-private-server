#!/usr/bin/env python3
"""Prepare pinned 7.2 server resources from automatic downloads or local checkouts."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile

RECIPE = Path(__file__).with_name('resources-recipe.json')
TABLES = {
    'Avatar': ('id',), 'AvatarSkillDepot': ('id',), 'AvatarSkill': ('id',),
    'AvatarTalent': ('talentId',), 'ProudSkill': ('proudSkillId',),
    'AvatarPromote': ('avatarPromoteId', 'promoteLevel'), 'FetterInfo': ('fetterId',),
    'Fetters': ('fetterId',), 'FetterStory': ('fetterId',),
    'FetterCharacterCard': ('avatarId', 'fetterLevel'), 'Material': ('id',),
    'Reward': ('rewardId',),
}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def member(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or not relative.parts or '..' in relative.parts or '\\' in name:
        raise ValueError('Unsafe resource path: ' + name)
    path = root.joinpath(*relative.parts)
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('Resource path is redirected: ' + name)
    return path


def verify_prepared(root, recipe_path=RECIPE):
    """One verifier for development resource reuse and deployment inputs."""
    root = Path(root).resolve(strict=True)
    manifest = read(member(root, 'resource-manifest.json'))
    recipe = read(recipe_path)
    if (manifest.get('schema') != 1 or manifest.get('recipeSha256') != digest(recipe_path)
            or manifest.get('inputs') != recipe['inputs']):
        raise ValueError('Prepared resources do not match the current pinned recipe')
    files = manifest['files']
    if not files or any(files.get(name) != wanted for name, wanted in recipe['derivedSha256'].items()):
        raise ValueError('Prepared resources do not include the current derived files')
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if actual != set(files) | {'resource-manifest.json'}:
        raise ValueError('Prepared resource inventory changed')
    for name, checksum in files.items():
        if digest(member(root, name)) != checksum:
            raise ValueError('Prepared resource changed: ' + name)
    return root


def checkout(root, revision, *, require_all=True):
    root = Path(root).resolve(strict=True)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args])
    if git('rev-parse', 'HEAD').decode().strip() != revision:
        raise ValueError('Input checkout is not at the recipe revision: ' + root.name)
    if git('status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('Input checkout has local changes: ' + root.name)
    names = git('ls-files', '-z').decode().split('\0')
    for name in filter(None, names) if require_all else []:
        path = member(root, name)
        if not path.is_file():
            raise ValueError('Resource checkout contains a missing or nonregular file: ' + name)
    return root, list(filter(None, names))


@contextmanager
def download_lock(cache):
    path = cache / '.download.lock'
    if path.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
        raise ValueError('Download lock is redirected or shared')
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if not path.stat().st_size:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise ValueError('Another resource download is using this cache') from error
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('Another resource download is using this cache') from error
        yield


def download_inputs(cache_root, recipe_path=RECIPE):
    """Fetch exact commits into a private cache; completed inputs need no network."""
    if not shutil.which('git'):
        raise ValueError('Install Git to download server resources')
    recipe = read(recipe_path)
    if recipe.get('schema') != 1 or recipe.get('gameVersion') != '7.2.0':
        raise ValueError('Unsupported resource recipe')
    cache_root = Path(cache_root).absolute()
    if cache_root.is_symlink():
        raise ValueError('Resource cache is redirected')
    cache = cache_root / digest(recipe_path)
    if cache.is_symlink():
        raise ValueError('Resource cache is redirected')
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    environment = os.environ.copy()
    environment['GIT_TERMINAL_PROMPT'] = '0'
    def git(directory, *args, check=True):
        return subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, '-C', str(directory), *args],
                              env=environment, check=check, timeout=1800)
    with download_lock(cache):
        identity = {'schema': 1, 'recipeSha256': digest(recipe_path), 'inputs': recipe['inputs']}
        marker = cache / 'cache.json'
        if marker.exists():
            if marker.is_symlink() or read(marker) != identity:
                raise ValueError('Resource cache does not match this recipe')
        else:
            if any(p.name != '.download.lock' for p in cache.iterdir()):
                raise ValueError('Cache directory is not owned by the resource downloader; choose another cache')
            with marker.open('x') as stream:
                json.dump(identity, stream, indent=2)
        result = {}
        for name in ['base', 'current']:
            selected = recipe['inputs'][name]
            directory = cache / name
            ready = cache / (name + '-ready.json')
            if ready.exists():
                checkout(directory, selected['revision'], require_all=name == 'base')
                print('Using cached ' + name + ' resources.', flush=True)
                result[name] = directory
                continue
            if directory.is_symlink():
                raise ValueError('Cached input is redirected')
            if not directory.exists():
                directory.mkdir()
                git(directory, 'init', '-q')
                git(directory, 'remote', 'add', 'origin', selected['url'])
            origin = subprocess.check_output(['git', '-C', str(directory), 'remote', 'get-url', 'origin'], env=environment, text=True).strip()
            if origin != selected['url']:
                raise ValueError('Cached input origin differs from the pinned recipe')
            git(directory, 'config', 'remote.origin.promisor', 'true')
            git(directory, 'config', 'remote.origin.partialclonefilter', 'blob:none')
            if name == 'current':
                git(directory, 'sparse-checkout', 'init', '--cone')
                git(directory, 'sparse-checkout', 'set', 'ExcelBinOutput', 'BinOutput', 'TextMap')
            print('Downloading ' + name + ' resources at ' + selected['revision'] + '…', flush=True)
            git(directory, 'fetch', '--progress', '--depth=1', '--filter=blob:none', 'origin', selected['revision'])
            print('Fetching selected file contents; the first download can take several minutes…', flush=True)
            git(directory, 'checkout', '--detach', selected['revision'])
            checkout(directory, selected['revision'], require_all=name == 'base')
            write(ready, {'revision': selected['revision']})
            result[name] = directory
        return result['base'], result['current']


def download_prepare(output, cache_root=None, recipe_path=RECIPE):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Output already exists; choose a new resource directory')
    cache_root = cache_root or output.parent / '.resource-downloads'
    base, current = download_inputs(cache_root, recipe_path)
    print('Preparing and verifying downloaded resources…', flush=True)
    return prepare(base, current, output, recipe_path)


def append_rows(old, new, fields):
    def key(row):
        return tuple(row.get(field, 0) for field in fields)
    keys = [key(row) for row in new]
    if len(set(keys)) != len(keys):
        raise ValueError('Ambiguous identity in resource table: ' + ', '.join(fields))
    existing = {key(row) for row in old}
    additions = [row for row in new if key(row) not in existing]
    return old + additions, additions


def derive(stage, current, recipe):
    for table, fields in TABLES.items():
        name = 'ExcelBinOutput/' + table + 'ExcelConfigData.json'
        merged, _ = append_rows(read(stage / name), read(current / name), fields)
        write(stage / name, merged)
    weapon_affixes, weapon_gadgets = set(), set()
    for table, fields in [('Weapon', ('id',)), ('WeaponPromote', ('weaponPromoteId', 'promoteLevel')),
                          ('EquipAffix', ('affixId',)), ('Gadget', ('id',))]:
        name = 'ExcelBinOutput/' + table + 'ExcelConfigData.json'
        old = read(stage / name)
        _, additions = append_rows(old, read(current / name), fields)
        if table == 'Weapon':
            weapon_affixes.update(a for row in additions for a in row.get('skillAffix', []) if a)
            weapon_gadgets.update(row['gadgetId'] for row in additions)
        elif table == 'EquipAffix':
            additions = [row for row in additions if row['id'] in weapon_affixes]
        elif table == 'Gadget':
            additions = [row for row in additions if row['id'] in weapon_gadgets]
        write(stage / name, old + additions)
    name = 'BinOutput/Talent/EquipTalents/ConfigAffix_Catalyst.json'
    old, new = read(stage / name), read(current / name)
    write(stage / name, old | {key: value for key, value in new.items() if key not in old})
    name = 'BinOutput/Ability/Temp/EquipAbilities/ConfigAbility_Weapon_Catalyst2.json'
    old, new = read(stage / name), read(current / name)
    existing = {row['Default']['abilityName'] for row in old}
    write(stage / name, old + [row for row in new if row['Default']['abilityName'] not in existing])
    configs = {name: read(member(current, source))[name] for name, source in recipe['gadgetConfigs'].items()}
    write(stage / 'BinOutput/Gadget/CurrentAvatarGadgets72.json', configs)
    name = 'ExcelBinOutput/GadgetExcelConfigData.json'
    old = read(stage / name); existing = {row['id'] for row in old}
    write(stage / name, old + [row for row in read(current / name)
                              if row.get('jsonName') in configs and row['id'] not in existing])
    for language in ['RU', 'TH']:
        merged = {}
        sources = sorted((current / 'TextMap').glob('TextMap' + language + '_*.json'))
        if len(sources) != 2:
            raise ValueError('Unexpected split TextMap count: ' + language)
        for source in sources:
            part = read(source)
            if any(merged[key] != part[key] for key in merged.keys() & part.keys()):
                raise ValueError('Conflicting TextMap entries: ' + language)
            merged.update(part)
        write(stage / ('TextMap/TextMap' + language + '.json'), merged)


def prepare(base, current, output, recipe_path=RECIPE):
    recipe = read(recipe_path)
    if recipe.get('schema') != 1 or recipe.get('gameVersion') != '7.2.0':
        raise ValueError('Unsupported resource recipe')
    base, base_files = checkout(base, recipe['inputs']['base']['revision'])
    current, _ = checkout(current, recipe['inputs']['current']['revision'], require_all=False)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Output already exists; choose a new resource directory')
    if any(output.resolve().is_relative_to(source) or source.is_relative_to(output.resolve())
           for source in [base, current]):
        raise ValueError('Output and input checkouts must be separate')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.resources-', dir=output.parent) as directory:
        stage = Path(directory) / 'resources'; stage.mkdir()
        for name in base_files:
            destination = member(stage, name); destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(member(base, name), destination)
        # Current localization replaces the complete base directory; retaining an
        # obsolete base split would make server lookup depend on traversal order.
        if (stage / 'TextMap').exists():
            shutil.rmtree(stage / 'TextMap')
        for name, source in recipe['currentCopies'].items():
            destination = member(stage, name); destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(member(current, source), destination)
        derive(stage, current, recipe)
        for name, expected in recipe['derivedSha256'].items():
            if digest(member(stage, name)) != expected:
                raise ValueError('Generated resource differs from verified recipe: ' + name)
        manifest = {'schema': 1, 'gameVersion': '7.2.0', 'inputs': recipe['inputs'],
                    'recipeSha256': digest(recipe_path),
                    'files': {p.relative_to(stage).as_posix(): digest(p) for p in sorted(stage.rglob('*')) if p.is_file()}}
        write(stage / 'resource-manifest.json', manifest)
        if output.exists() or output.is_symlink():
            raise ValueError('Output appeared while preparing; refusing to replace it')
        stage.rename(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path)
    parser.add_argument('--current', type=Path)
    parser.add_argument('--download', action='store_true', help='Download the pinned inputs automatically')
    parser.add_argument('--cache', type=Path, help='Persistent download cache, outside public source')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.download:
            if args.base or args.current:
                raise ValueError('Choose --download or local --base/--current inputs')
            result = download_prepare(args.output, args.cache)
        else:
            if not args.base or not args.current:
                raise ValueError('Supply --base and --current, or use --download')
            result = prepare(args.base, args.current, args.output)
        print('Prepared resources:', result)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f'Resource preparation failed: {error}\n')


if __name__ == '__main__':
    main()

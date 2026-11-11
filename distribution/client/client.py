#!/usr/bin/env python3
"""Portable private-client preparation and launch core (Windows and Linux)."""
import argparse
import base64
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

VERSION = '7.2.0'
MARKER = '.private-launcher/client.json'
INPUTS = {
    'GenshinImpact.exe': 'b3001fc110b63e0e0ce24a77386e71d0eefaffaae244ea0c72645a659cb5ea6e',
    'GenshinImpact_Data/Managed/Metadata/global-metadata.dat': '4ed10538735861ad87b027d30039e2afb92e334f1b4de4f643e17c9c79d19fd7',
    'GenshinImpact_Data/Managed/Metadata/startup-metadata.dat': '2a5fe7adbeb7cba81db69d7c62dee3536615e5b77efa0514c464e9c9e72d93c0',
}
PATCH_PATHS = ('Astrolabe.dll', 'GenshinImpact_Data/Plugins/Astrolabe.dll')
# Only the shipped asset tree is shared. Persistent contains downloaded patches
# and game-written state and must remain private, as do Plugins and Managed.
SHARED_ASSETS = 'GenshinImpact_Data/StreamingAssets'


def redirected(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def asset_inventory(root):
    """Cheap source-update detection; no multi-hundred-GB hashing on every Play."""
    result = {}
    for parent, folders, files in os.walk(root):
        for name in folders + files:
            path = Path(parent) / name
            if redirected(path):
                raise ValueError('Shared assets contain a redirected entry: ' + str(path))
            info = path.stat()
            result[path.relative_to(root).as_posix()] = (
                ['directory'] if path.is_dir() else [info.st_size, info.st_mtime_ns])
    return result


def link_assets(source, target):
    if os.name == 'nt':
        # Mirror directories privately: a junction would also redirect newly
        # created files into the official installation. Guarded file links share
        # existing bytes while new files remain in the private directory tree.
        target.mkdir()
        for parent, folders, files in os.walk(source):
            destination = target / Path(parent).relative_to(source)
            for name in folders:
                (destination / name).mkdir()
            for name in files:
                try:
                    os.link(Path(parent) / name, destination / name)
                except OSError:
                    try:
                        os.symlink(Path(parent) / name, destination / name)
                    except OSError as error:
                        raise ValueError('Windows shared mode requires a private folder on the same volume as the game, or Developer Mode for file symlinks across volumes. No assets were copied.') from error
    else:
        target.symlink_to(source, target_is_directory=True)


def shared_source(client, *, expected=None):
    client = Path(client).resolve(strict=True)
    state = client / '.private-launcher/shared-assets.json'
    if not state.exists():
        assets = client / SHARED_ASSETS
        if redirected(assets) or any(redirected(p) or (p.is_file() and p.stat().st_nlink != 1)
                                     for p in assets.rglob('*')):
            raise ValueError('Shared asset protection record is missing; refusing an unprotected launch')
        return None
    record = json.loads(checked_file(client, state.relative_to(client)).read_text())
    if record.get('schema') != 1:
        raise ValueError('Unsupported shared-assets record')
    source = verify_inputs(record['source'], expected)
    target = source / SHARED_ASSETS
    link = client / SHARED_ASSETS
    if record.get('layout') == 'files':
        if redirected(link) or not link.resolve(strict=True).is_relative_to(client):
            raise ValueError('Private asset directory was redirected')
        for name, info in record['inventory'].items():
            path = link / name
            if info == ['directory']:
                if redirected(path) or not path.is_dir():
                    raise ValueError('Private asset directory was changed: ' + name)
            elif not path.is_file() or not os.path.samefile(path, target / name):
                raise ValueError('Shared asset file was changed: ' + name)
    elif os.name == 'nt' or not redirected(link) or link.resolve(strict=True) != target:
        raise ValueError('Shared asset link was changed or uses an unsupported layout')
    if asset_inventory(target) != record['inventory']:
        raise ValueError('Shared assets changed. Restore the matching 7.2 installation before playing; do not update the source while sharing it.')
    return source


@contextmanager
def protect_shared_assets(client, *, expected=None):
    """Windows denies write/delete opens for every asset for the game lifetime.

    Linux uses a read-only mount namespace in launch instead. No source ACLs,
    permissions, attributes or bytes are changed on either platform.
    """
    source = shared_source(client, expected=expected)
    handles = []
    if source is not None and os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        class SecurityAttributes(ctypes.Structure):
            _fields_ = [('length', wintypes.DWORD), ('descriptor', wintypes.LPVOID),
                        ('inherit', wintypes.BOOL)]
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), None, True)
    try:
        if source is not None and os.name == 'nt':
            assets = source / SHARED_ASSETS
            # Pin directories too, preventing an updater from renaming the tree.
            for path in [source, source / 'GenshinImpact_Data', assets, *assets.rglob('*')]:
                handle = create(str(path), 0x80000000, 1, ctypes.byref(attributes), 3,
                                0x02000000 if path.is_dir() else 0, None)
                if handle == wintypes.HANDLE(-1).value:
                    raise OSError(ctypes.get_last_error(), 'Cannot protect shared assets; close the official game/updater: ' + str(path))
                handles.append(handle)
            shared_source(client, expected=expected)
        yield source
    finally:
        for handle in reversed(handles):
            close(handle)


class CopyCancelled(Exception):
    """Deliberately not OSError: copytree collects OSErrors and keeps copying."""


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def server_origin(value):
    if not value.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError('Server address contains whitespace or unsupported characters')
    if not value.startswith(('http://', 'https://')) or any(c in value for c in ('\\', '%')):
        raise ValueError('Use an http:// or https:// server address')
    u = urlsplit(value)
    if u.username is not None or u.password is not None or u.path not in ('', '/') or u.query or u.fragment:
        raise ValueError('Use only the server host and optional port, without credentials or a path')
    if '?' in value or '#' in value or '@' in value:
        raise ValueError('Server address cannot contain credentials, a query or a fragment')
    host, port = u.hostname, u.port
    if not host or port == 0 or value.removesuffix('/').endswith(':'):
        raise ValueError('Invalid server host or port')
    if ':' in host:
        ipaddress.IPv6Address(host)
    elif len(host) > 253 or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                               for label in host.split('.')):
        raise ValueError('Invalid server hostname')
    return value.removesuffix('/')


def wire_fields(data):
    """Read the small 7.2 dispatch response without shipping the server's protobuf runtime."""
    cursor = 0
    def varint():
        nonlocal cursor
        value = 0
        for shift in range(0, 70, 7):
            if cursor >= len(data):
                break
            byte = data[cursor]; cursor += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError('Malformed dispatch response')
    while cursor < len(data):
        tag = varint()
        field, kind = tag >> 3, tag & 7
        if not field:
            raise ValueError('Invalid dispatch field')
        if kind == 0:
            yield field, varint()
        elif kind in (1, 2, 5):
            length = varint() if kind == 2 else (8 if kind == 1 else 4)
            if length > len(data) - cursor:
                raise ValueError('Truncated dispatch response')
            value = data[cursor:cursor + length]; cursor += length
            if kind == 2:
                yield field, value
        else:
            raise ValueError('Unsupported dispatch response')


class NoDispatchRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise ValueError('Server redirected the login request. Use the address in its server profile.')


def check_server(url, *, opener=None):
    origin = server_origin(url)
    opener = opener or build_opener(NoDispatchRedirect())
    request = Request(origin + '/query_region_list?version=OSRELWin7.2.0&platform=3',
                      headers={'User-Agent': 'PrivateServerLauncher/1'})
    with opener.open(request, timeout=8) as response:
        if response.status != 200:
            raise ValueError(f'Login server returned HTTP {response.status}')
        body = response.read(128 * 1024 + 1)
    if len(body) > 128 * 1024:
        raise ValueError('Login server response is too large')
    fields = list(wire_fields(base64.b64decode(body.strip(), validate=True)))
    if any(field == 1 and value != 0 for field, value in fields):
        raise ValueError('Server rejected this client version')
    if (7, 1) not in fields:
        raise ValueError('Server has not enabled PC login')
    def origin_key(value):
        parsed = urlsplit(value)
        server_origin(parsed.scheme + '://' + parsed.netloc)
        return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)
    wanted = origin_key(origin)
    regions = []
    for field, value in fields:
        if field != 2 or not isinstance(value, bytes):
            continue
        urls = [v.decode('utf-8') for f, v in wire_fields(value) if f == 4 and isinstance(v, bytes)]
        if not urls or any(origin_key(value) != wanted for value in urls):
            raise ValueError('Server advertises a different login address. Ask its operator to check the public address.')
        regions.extend(urls)
    if not regions:
        raise ValueError('Server returned no playable regions')
    return {'loginReachable': True, 'regions': len(regions), 'gameConnectionTested': False}


def checked_file(root, relative):
    root = Path(root).resolve(strict=True)
    path = root / relative
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError(f'Missing or redirected client file: {relative}')
    return path


def verify_inputs(root, expected=None):
    root = Path(root).resolve(strict=True)
    for name, wanted in (INPUTS if expected is None else expected).items():
        if digest(checked_file(root, name)) != wanted:
            raise ValueError(f'Unsupported or changed client file: {name}. A matching {VERSION} client is required.')
    return root


def prepare(source, destination, patch, patch_sha256, progress=None, *, expected=None, storage='copy'):
    """Prepare private writable files, optionally sharing the shipped asset tree."""
    if storage not in ('copy', 'shared'):
        raise ValueError('Storage must be copy or shared')
    source = verify_inputs(source, expected)
    destination = Path(destination).absolute()
    patch = Path(patch).resolve(strict=True)
    if not re.fullmatch('[a-f0-9]{64}', patch_sha256) or digest(patch) != patch_sha256:
        raise ValueError('Patch checksum does not match the release manifest')
    if destination.is_symlink() or (destination.exists() and (
            not destination.is_dir() or any(destination.iterdir()))):
        raise ValueError('Destination already exists and contains files. Select an empty folder for the private copy.')
    if destination.resolve().is_relative_to(source) or source.is_relative_to(destination.resolve()):
        raise ValueError('Private copy and original game must be separate directories')
    if (source / MARKER).exists():
        raise ValueError('Select the normal game installation as the source, not another private copy')
    ignored = {'SDKCaches', '.private-launcher'}
    assets = source / SHARED_ASSETS
    inventory = None
    if storage == 'shared':
        if not assets.is_dir() or redirected(assets):
            raise ValueError('Shared mode requires an independent StreamingAssets directory')
        if os.name != 'nt' and not shutil.which('bwrap'):
            raise ValueError('Shared mode on Linux requires bubblewrap (bwrap) for read-only asset protection')
        inventory = asset_inventory(assets)
    total = 0
    for parent, folders, files in os.walk(source, followlinks=False):
        folders[:] = [f for f in folders if f not in ignored]
        if storage == 'shared' and Path(parent) == assets.parent:
            folders[:] = [f for f in folders if f != assets.name]
        for name in folders + files:
            p = Path(parent) / name
            if redirected(p):
                raise ValueError(f'Source contains a link; supply an independent game installation: {p.relative_to(source)}')
        total += sum((Path(parent) / f).stat().st_size for f in files)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < total + patch.stat().st_size * 2:
        raise ValueError(f'Insufficient space: the private copy needs at least {total / (1024**3):.1f} GiB')
    copied = 0
    with tempfile.TemporaryDirectory(prefix='.private-client-', dir=destination.parent) as temporary:
        stage = Path(temporary) / 'client'

        def copy_file(src, dst):
            nonlocal copied
            result = shutil.copy2(src, dst)
            Path(dst).chmod(Path(dst).stat().st_mode | stat.S_IRUSR | stat.S_IWUSR)
            copied += Path(src).stat().st_size
            if progress:
                progress(copied, total)
            return result

        def ignore(parent, names):
            excluded = ignored.intersection(names)
            if storage == 'shared' and Path(parent) == assets.parent:
                excluded = excluded | {assets.name}
            return excluded
        shutil.copytree(source, stage, copy_function=copy_file, ignore=ignore)
        for parent, folders, files in os.walk(stage):
            directory = Path(parent)
            directory.chmod(directory.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        # Recheck the copied executable/metadata before applying a patch. A source
        # updater racing the copy must not produce a silently mixed client build.
        verify_inputs(stage, expected)
        state = stage / '.private-launcher'
        (state / 'originals').mkdir(parents=True)
        originals = {}
        for index, name in enumerate(PATCH_PATHS):
            target = stage / name
            if target.exists():
                checked_file(stage.resolve(), name)
                backup = f'Astrolabe-{index}.dll'
                shutil.copy2(target, state / 'originals' / backup)
                originals[name] = backup
            else:
                originals[name] = None
                target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(patch, target)
            if digest(target) != patch_sha256:
                raise ValueError('Copied patch failed checksum verification')
        (state / 'originals/files.json').write_text(json.dumps(originals, indent=2) + '\n')
        (stage / MARKER).write_text(json.dumps({'schema': 1, 'gameVersion': VERSION,
                                              'patchSha256': patch_sha256}, indent=2) + '\n')
        if inventory is not None:
            verify_inputs(source, expected)
            if inventory != asset_inventory(assets):
                raise ValueError('Shared assets changed during preparation; close the official updater and retry')
            link_assets(assets, stage / SHARED_ASSETS)
            (state / 'shared-assets.json').write_text(json.dumps({
                'schema': 1, 'source': str(source), 'inventory': inventory,
                'layout': 'files' if os.name == 'nt' else 'directory'}, indent=2) + '\n')
        if destination.is_symlink():
            raise ValueError('Destination was redirected while copying')
        if destination.exists():
            if not destination.is_dir() or any(destination.iterdir()):
                raise ValueError('Destination changed while copying; refusing to replace its files')
            # A folder selected/created in the desktop directory picker may already
            # exist. Remove only that empty folder; rmdir also rejects a racing write.
            destination.rmdir()
        stage.rename(destination)
    return destination


def verify_private(client, patch_sha256, *, expected=None):
    client = verify_inputs(client, expected)
    shared_source(client, expected=expected)
    if (client / '.private-launcher/patch-pending.json').exists():
        raise ValueError('A patch change was interrupted. Use Restore previous patch before playing.')
    marker = json.loads(checked_file(client, MARKER).read_text())
    if marker != {'schema': 1, 'gameVersion': VERSION, 'patchSha256': patch_sha256}:
        raise ValueError('Private-client marker does not match this release')
    for name in PATCH_PATHS:
        path = checked_file(client, name)
        if path.stat().st_nlink != 1 or digest(path) != patch_sha256:
            raise ValueError(f'Patch changed or shared with another installation: {name}')
    return client


def adopt_shared(source, client, patch_sha256, *, expected=None):
    """Migrate the old Linux file-link layout without replacing settings/prefixes.

    Only links to this exact source are accepted. Writable trees become real
    private files; the previous asset link layout is retained for rollback.
    Caller must first establish that the game is stopped.
    """
    if os.name == 'nt':
        raise ValueError('Legacy file-link adoption is only supported on Linux')
    source = verify_inputs(source, expected)
    client = Path(client).resolve(strict=True)
    if client.is_relative_to(source) or source.is_relative_to(client):
        raise ValueError('Private client and source must be separate')
    if (client / MARKER).exists():
        return verify_private(client, patch_sha256, expected=expected)
    if (client / '.private-launcher').exists():
        raise ValueError('An incomplete adoption exists; inspect its recovery record before retrying')
    for relative, checksum in (INPUTS if expected is None else expected).items():
        if digest(client / relative) != checksum:
            raise ValueError('Legacy client input differs: ' + relative)
    for relative in PATCH_PATHS:
        path = checked_file(client, relative)
        if path.stat().st_nlink != 1 or digest(path) != patch_sha256:
            raise ValueError('Legacy patch differs: ' + relative)
    links = []
    assets = client / SHARED_ASSETS
    for path in client.rglob('*'):
        relative = path.relative_to(client)
        if redirected(path):
            if not path.is_file() or path.resolve(strict=True) != source / relative:
                raise ValueError('Legacy link is not a file in the selected source: ' + str(relative))
            if not path.is_relative_to(assets):
                links.append(path)
        elif path.is_file() and path.is_relative_to(assets):
            raise ValueError('Legacy assets contain private changes; preserve and inspect before migration')
    inventory = asset_inventory(source / SHARED_ASSETS)
    actual = {p.relative_to(assets).as_posix() for p in assets.rglob('*')}
    if actual != set(inventory):
        raise ValueError('Legacy asset layout does not match the source')
    needed = sum(p.stat().st_size for p in links)
    if shutil.disk_usage(client).free < needed:
        raise ValueError('Insufficient space to detach writable legacy files')
    state = client / '.private-launcher'
    state.mkdir()
    with client_lock(client):
        record = {p.relative_to(client).as_posix(): str(p.resolve()) for p in links}
        (state / 'adoption-links.json').write_text(json.dumps(record, indent=2) + '\n')
        for path in links:
            atomic_copy(path.resolve(strict=True), path)
            path.chmod(path.stat().st_mode | stat.S_IWUSR)
        assets.rename(state / 'legacy-assets')
        link_assets(source / SHARED_ASSETS, assets)
        (state / 'shared-assets.json').write_text(json.dumps({
            'schema': 1, 'source': str(source), 'inventory': inventory}, indent=2) + '\n')
        (client / MARKER).write_text(json.dumps({
            'schema': 1, 'gameVersion': VERSION, 'patchSha256': patch_sha256}, indent=2) + '\n')
        verify_private(client, patch_sha256, expected=expected)
    return client


@contextmanager
def client_lock(client):
    client = Path(client).resolve(strict=True)
    state = client / '.private-launcher'
    if state.is_symlink() or not state.is_dir() or not state.resolve().is_relative_to(client):
        raise ValueError('Private launcher state is missing or redirected')
    lock_path = state / 'launch.lock'
    if lock_path.is_symlink() or (lock_path.exists() and lock_path.stat().st_nlink != 1):
        raise ValueError('Launch lock is redirected or shared')
    with lock_path.open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise ValueError('This private client is already running or being updated') from error
        else:
            import fcntl
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('This private client is already running or being updated') from error
        yield


def atomic_copy(source, target):
    with tempfile.TemporaryDirectory(prefix='.patch-', dir=target.parent) as directory:
        next_file = Path(directory) / target.name
        shutil.copyfile(source, next_file)
        next_file.replace(target)


def patch_history(client, name):
    if not isinstance(name, str) or not re.fullmatch(r'[0-9]{8}T[0-9]{6}\.[0-9]{6}Z', name):
        raise ValueError('Invalid patch recovery directory')
    directory = client / '.private-launcher/patch-history' / name
    if directory.is_symlink() or not directory.resolve().is_relative_to(client):
        raise ValueError('Patch recovery directory is redirected')
    marker = json.loads(checked_file(directory, 'client.json').read_text())
    checksum = marker.get('patchSha256', '')
    if marker != {'schema': 1, 'gameVersion': VERSION, 'patchSha256': checksum} or not re.fullmatch('[a-f0-9]{64}', checksum):
        raise ValueError('Invalid patch recovery marker')
    for index in range(len(PATCH_PATHS)):
        if digest(checked_file(directory, f'Astrolabe-{index}.dll')) != checksum:
            raise ValueError('Patch recovery backup failed verification')
    return directory, checksum


def restore_patch(client, *, expected=None):
    client = verify_inputs(client, expected)
    with client_lock(client):
        state = client / '.private-launcher'
        pending = state / 'patch-pending.json'
        selected = pending if pending.exists() else state / 'last-patch.json'
        record = json.loads(checked_file(client, selected.relative_to(client)).read_text())
        directory, checksum = patch_history(client, record['history'])
        if not pending.exists():
            verify_private(client, record['installedSha256'], expected=expected)
            # A new journal also makes an interrupted rollback recoverable.
            with pending.open('x') as stream:
                json.dump(record, stream)
        for index, name in enumerate(PATCH_PATHS):
            target = checked_file(client, name)
            if target.stat().st_nlink != 1:
                raise ValueError('Private patch is shared with another installation')
            atomic_copy(directory / f'Astrolabe-{index}.dll', target)
        atomic_copy(directory / 'client.json', client / MARKER)
        pending.unlink()
        return checksum


def update_patch(client, patch, checksum, *, expected=None):
    client = verify_inputs(client, expected)
    patch = Path(patch).resolve(strict=True)
    if not re.fullmatch('[a-f0-9]{64}', checksum) or digest(patch) != checksum:
        raise ValueError('Patch checksum does not match the release manifest')
    with client_lock(client):
        marker = json.loads(checked_file(client, MARKER).read_text())
        verify_private(client, marker['patchSha256'], expected=expected)
        if marker['patchSha256'] == checksum:
            return None
        state = client / '.private-launcher'
        name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        directory = state / 'patch-history' / name
        if not directory.resolve().is_relative_to(client):
            raise ValueError('Patch history escapes the private copy')
        directory.mkdir(parents=True, mode=0o700)
        shutil.copyfile(client / MARKER, directory / 'client.json')
        for index, relative in enumerate(PATCH_PATHS):
            shutil.copyfile(client / relative, directory / f'Astrolabe-{index}.dll')
        patch_history(client, name)
        candidate = directory / 'next.dll'; shutil.copyfile(patch, candidate)
        if digest(candidate) != checksum:
            raise ValueError('Patch changed while copying; update cancelled')
        record = {'history': name, 'installedSha256': checksum}
        pending = state / 'patch-pending.json'
        with pending.open('x') as stream:
            json.dump(record, stream)
        # Leave the journal on failure: playing is blocked until explicit recovery.
        for relative in PATCH_PATHS:
            atomic_copy(candidate, client / relative)
        next_marker = directory / 'next-client.json'
        next_marker.write_text(json.dumps({'schema': 1, 'gameVersion': VERSION, 'patchSha256': checksum}, indent=2) + '\n')
        atomic_copy(next_marker, client / MARKER)
        record_path = directory / 'change.json'; record_path.write_text(json.dumps(record) + '\n')
        atomic_copy(record_path, state / 'last-patch.json')
        pending.unlink()
        return directory


def quarantine_login(client):
    """Caller must hold the launch lock. Only private SDK login state is moved."""
    client = Path(client).resolve(strict=True)
    cache = client / 'GenshinImpact_Data/SDKCaches/HoYoPass'
    if not cache.exists() and not cache.is_symlink():
        return None
    if cache.is_symlink() or not cache.resolve().is_relative_to(client):
        raise ValueError('Private login cache is redirected')
    for parent, folders, files in os.walk(cache):
        for name in folders + files:
            p = Path(parent) / name
            if p.is_symlink() or (p.is_file() and p.stat().st_nlink != 1):
                raise ValueError('Private login cache contains shared files')
    if not any(cache.iterdir()):
        return None
    backups = client / '.private-launcher/login-backups'
    if not backups.resolve().is_relative_to(client):
        raise ValueError('Login backups escape the private copy')
    backups.mkdir(parents=True, exist_ok=True)
    backups.chmod(0o700)
    target = backups / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    cache.rename(target)
    cache.mkdir()
    return target


def launch(client, url, patch_sha256, wine=None, remember_login=False, *, expected=None,
           profile=None, no_frame_generation=False, no_ntsync=False, dry_run=False):
    url = server_origin(url)
    client = verify_inputs(client, expected)
    state = client / '.private-launcher'
    if state.is_symlink() or not state.resolve().is_relative_to(client):
        raise ValueError('Private launcher state is redirected')
    environment = os.environ.copy()
    environment['PRIVATE_SERVER_URL'] = url
    if os.name == 'nt':
        command = [str(client / 'GenshinImpact.exe')]
    elif profile is not None:
        import linux_profile
        profile = json.loads(Path(profile).read_text())
        isolation, game, overrides = linux_profile.launch_plan(
            profile, profile['workspace'], client, no_frame_generation, no_ntsync)
        environment.update(overrides)
        command = [*isolation, '--', *game]
    else:
        if not wine:
            raise ValueError('Select your Wine executable for Linux')
        runner = shutil.which(str(wine))
        if not runner:
            raise ValueError('Wine executable was not found')
        prefix = state / 'wine-prefix'
        if prefix.is_symlink() or not prefix.resolve().is_relative_to(client):
            raise ValueError('Wine prefix escapes the private copy')
        for name in ['WINEDLLPATH', 'WINELOADER', 'WINESERVER']:
            environment.pop(name, None)
        environment.update(WINEPREFIX=str(prefix), WINEARCH='win64', WINEDLLOVERRIDES='Astrolabe=n,b')
        command = [runner, str(client / 'GenshinImpact.exe')]
    # Verify under the same lock used by patch updates; no update can race launch.
    with client_lock(client), protect_shared_assets(client, expected=expected) as shared:
        verify_private(client, patch_sha256, expected=expected)
        if shared is not None and os.name != 'nt':
            sandbox = shutil.which('bwrap')
            if not sandbox:
                raise ValueError('Shared mode requires bubblewrap; refusing an unprotected launch')
            # Bind private writable state, then explicitly protect the source
            # again in case it sits inside a writable ancestor.
            if profile is not None:
                separator = command.index('--')
                command[separator:separator] = ['--ro-bind', str(shared), str(shared)]
            else:
                private_home = state / 'home'
                private_tmp = state / 'tmp'
                private_home.mkdir(exist_ok=True)
                private_tmp.mkdir(exist_ok=True)
                for directory in (private_home, private_tmp):
                    if redirected(directory):
                        raise ValueError('Private launch directory is redirected')
                environment.update(HOME=str(private_home), TMPDIR='/tmp',
                                   XDG_CACHE_HOME=str(private_home / '.cache'),
                                   XDG_CONFIG_HOME=str(private_home / '.config'),
                                   XDG_DATA_HOME=str(private_home / '.local/share'))
                command = [sandbox, '--die-with-parent', '--ro-bind', '/', '/',
                           '--bind', str(private_tmp), '/tmp', '--bind', str(client), str(client),
                           '--dev-bind', '/dev', '/dev', '--proc', '/proc',
                           '--ro-bind', str(shared), str(shared),
                           *(['--ro-bind', '/tmp/.X11-unix', '/tmp/.X11-unix']
                             if Path('/tmp/.X11-unix').is_dir() else []), '--', *command]
        if dry_run:
            return {'command': command, 'environment': environment, 'game_launched': False}
        if not remember_login:
            quarantine_login(client)
        if getattr(sys, 'frozen', False):
            # PyInstaller's library overrides belong to the launcher, not Wine/game.
            original = environment.pop('LD_LIBRARY_PATH_ORIG', None)
            environment.pop('LD_LIBRARY_PATH', None)
            if original is not None:
                environment['LD_LIBRARY_PATH'] = original
        if os.name == 'nt' and getattr(sys, 'frozen', False):
            import ctypes
            set_directory = ctypes.windll.kernel32.SetDllDirectoryW
            set_directory.argtypes = [ctypes.c_wchar_p]
            set_directory.restype = ctypes.c_int
            if not set_directory(None):
                raise OSError('Cannot reset library paths before game launch')
            try:
                return subprocess.run(command, cwd=client, env=environment, check=False,
                                      close_fds=shared is None).returncode
            finally:
                set_directory(str(sys._MEIPASS))
        # Windows passes the read guards to the game as well, so they survive an
        # unexpected launcher exit for as long as the game process is alive.
        options = {'close_fds': False} if os.name == 'nt' and shared is not None else {}
        return subprocess.run(command, cwd=client, env=environment, check=False, **options).returncode



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prepare_parser = sub.add_parser('prepare', help='Make a separate private copy from a matching normal game')
    prepare_parser.add_argument('--source', required=True, type=Path)
    prepare_parser.add_argument('--destination', required=True, type=Path)
    prepare_parser.add_argument('--patch', required=True, type=Path)
    prepare_parser.add_argument('--patch-sha256', required=True)
    prepare_parser.add_argument('--storage', choices=['shared', 'copy'], default='shared',
                                help='Share shipped assets (default), or keep an independent full copy')
    diagnose = sub.add_parser('diagnose', help='Check login/dispatch connectivity without starting the game')
    diagnose.add_argument('--server', required=True)
    for action in ['check', 'launch']:
        p = sub.add_parser(action)
        p.add_argument('--client', required=True, type=Path)
        p.add_argument('--patch-sha256', required=True)
        if action == 'launch':
            p.add_argument('--server', required=True)
            p.add_argument('--wine')
            p.add_argument('--remember-login', action='store_true')
            p.add_argument('--profile', type=Path, help='Optional private Linux Wine/performance profile')
            p.add_argument('--dry-run', action='store_true')
    update = sub.add_parser('update-patch', help='Install this release patch with recovery backups')
    update.add_argument('--client', required=True, type=Path)
    update.add_argument('--patch', required=True, type=Path)
    update.add_argument('--patch-sha256', required=True)
    restore = sub.add_parser('restore-patch', help='Recover an interrupted update or restore the previous patch')
    restore.add_argument('--client', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            print(prepare(args.source, args.destination, args.patch, args.patch_sha256, storage=args.storage))
        elif args.action == 'update-patch':
            print('Patch updated; recovery:', update_patch(args.client, args.patch, args.patch_sha256))
        elif args.action == 'restore-patch':
            print('Patch restored:', restore_patch(args.client))
        elif args.action == 'check':
            verify_private(args.client, args.patch_sha256)
            print(f'Private client and patch match {VERSION}')
        elif args.action == 'diagnose':
            print(json.dumps(check_server(args.server)))
        else:
            result = launch(args.client, args.server, args.patch_sha256, args.wine, args.remember_login,
                            profile=args.profile, dry_run=args.dry_run)
            if args.dry_run:
                print(json.dumps(result, indent=2))
            else:
                raise SystemExit(result)
    except (OSError, ValueError, KeyError, CopyCancelled) as error:
        parser.exit(1, f'Client error: {error}\n')


if __name__ == '__main__':
    main()

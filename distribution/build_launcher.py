#!/usr/bin/env python3
"""Build a player launcher package from an explicitly selected DLL and its source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / 'distribution/client'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def build(patch, checksum, patch_source, output, frozen=False, dependency_archive=None):
    patch, patch_source, output = Path(patch).resolve(strict=True), Path(patch_source).resolve(strict=True), Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Output already exists; select a new package directory')
    if digest(patch) != checksum:
        raise ValueError('Patch checksum does not match the selected build')
    if patch.read_bytes()[:2] != b'MZ':
        raise ValueError('Patch is not a Windows DLL')
    if frozen and dependency_archive is None:
        raise ValueError('Frozen releases require the matching patch dependency source archive')
    if dependency_archive is not None:
        dependency_archive = Path(dependency_archive).resolve(strict=True)
        with tarfile.open(dependency_archive, 'r:gz') as archive:
            member = archive.next()
            if member is None or member.name != 'dependencies.json' or not member.isfile() or member.size > 65536:
                raise ValueError('Invalid patch dependency source archive')
            metadata = json.load(archive.extractfile(member))
            if metadata.get('schema') != 1 or metadata.get('cargoLockSha256') != digest(patch_source / 'Cargo.lock'):
                raise ValueError('Patch dependency source does not match Cargo.lock')
    source_names = ['Cargo.toml', 'Cargo.lock', 'LICENSE', 'README.md', 'sdk_public_key.xml', 'server_public_key.bin']
    source_names += [str(p.relative_to(patch_source)) for p in sorted((patch_source / 'src').rglob('*.rs'))]
    if not source_names[6:]:
        raise ValueError('Corresponding patch source is missing')
    if (patch_source / '.cargo/config.toml').is_file():
        source_names.append('.cargo/config.toml')
    source_hashes = {}
    for name in source_names:
        path = patch_source / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(patch_source):
            raise ValueError('Patch source is incomplete or redirected: ' + name)
        source_hashes[name] = digest(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.launcher-build-', dir=output.parent) as directory:
        work = Path(directory)
        bundle = work / 'source-package'; bundle.mkdir()
        for name in ['client.py', 'launcher.py', 'linux_profile.py']:
            shutil.copyfile(CLIENT / name, bundle / name)
        shutil.copyfile(patch, bundle / 'Astrolabe.dll')
        manifest = {'schema': 1, 'gameVersion': '7.2.0', 'patch': {'file': 'Astrolabe.dll', 'sha256': checksum},
                    'sourceFiles': source_hashes}
        (bundle / 'patch-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        source = bundle / 'corresponding-source/patch'
        for name in source_names:
            target = source / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(patch_source / name, target)
        if dependency_archive is not None:
            shutil.copyfile(dependency_archive, source / 'dependencies.tar.gz')
            (source / '.cargo').mkdir(exist_ok=True)
            (source / '.cargo/vendor.toml').write_text('[source.crates-io]\nreplace-with = "vendored-sources"\n\n[source.vendored-sources]\ndirectory = "vendor"\n')
            (source / 'SOURCE-BUILD.txt').write_text('Extract dependencies.tar.gz in this directory before an offline Cargo build.\nThe archive contains the exact locked registry dependencies and their checksums.\nBuild with the x86_64-pc-windows-gnullvm Rust target and matching LLVM-MinGW linker.\nRun cargo --config .cargo/vendor.toml build --release --locked --offline --target x86_64-pc-windows-gnullvm.\n')
        own = bundle / 'corresponding-source/launcher'; own.mkdir(parents=True)
        for name in ['client.py', 'launcher.py', 'linux_profile.py']:
            shutil.copyfile(CLIENT / name, own / name)
            target = own / 'distribution/client' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CLIENT / name, target)
        for name in ['build_launcher.py', 'collect_runtime_notices.py', 'vendor_patch_sources.py', 'requirements-build.txt']:
            shutil.copyfile(ROOT / 'distribution' / name, own / 'distribution' / name)
        notices = ROOT / 'distribution/third-party'
        if not (notices / 'patch/components.json').is_file():
            raise ValueError('Corresponding patch dependency notices are missing')
        components = json.loads((notices / 'patch/components.json').read_text())
        if frozen and components.get('cargoLockSha256') != digest(patch_source / 'Cargo.lock'):
            raise ValueError('Patch dependencies changed; refresh their third-party notices before packaging')
        shutil.copytree(notices, own / 'distribution/third-party')
        shutil.copytree(notices / 'patch', bundle / 'licenses/patch')
        shutil.copyfile(patch_source / 'LICENSE', bundle / 'LICENSE')
        readme = '''Private Server Launcher — game version 7.2.0

Use the server profile supplied by your host, or enter its address.
Choose your normal game folder and an empty destination for a private copy.
Shared preparation reuses installed assets and keeps writable files private.
Keep the source installation at version 7.2; choose full copy for independence.
Linux shared mode requires bubblewrap. Windows same-volume sharing needs no
elevation; cross-volume file symlinks require Developer Mode.
Test the connection, then press Play and sign in with your private-server account.
Keep the launcher open while playing. Login is reset on each launch unless you
select the one-launch remember option.

Windows: run PrivateServerLauncher.exe in the packaged application folder.
Linux: run PrivateServerLauncher and select your Wine executable.
Source package: Python 3.11+ with Tk, then run python launcher.py.

Game files are supplied by the player. The launcher and modified patch source are
included under corresponding-source; see LICENSE (GPL-3.0).
Third-party notices and a runtime inventory are included under licenses/.
'''
        (bundle / 'README.txt').write_text(readme, encoding='utf-8')
        if frozen:
            import tkinter
            tkinter.Tcl().eval('info patchlevel')
            command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
                       '--name', 'PrivateServerLauncher', '--distpath', str(work / 'frozen'),
                       '--workpath', str(work / 'build'), '--specpath', str(work / 'spec'),
                       '--paths', str(bundle), '--add-data', str(bundle / 'patch-manifest.json') + ':.',
                       '--add-data', str(bundle / 'Astrolabe.dll') + ':.', str(bundle / 'launcher.py')]
            subprocess.run(command, check=True)
            packaged = work / 'frozen/PrivateServerLauncher'
            shutil.copytree(bundle / 'corresponding-source', packaged / 'corresponding-source')
            for name in ['LICENSE', 'README.txt']:
                shutil.copyfile(bundle / name, packaged / name)
            shutil.copytree(bundle / 'licenses', packaged / 'licenses')
            # Collect runtime notices from the same environment that produced the
            # frozen binaries, rather than guessing which host libraries were used.
            from collect_runtime_notices import collect
            collect(packaged, packaged / 'licenses/runtime', notices / 'windows')
        else:
            packaged = bundle
        files = {str(p.relative_to(packaged)): digest(p) for p in sorted(packaged.rglob('*')) if p.is_file()}
        (packaged / 'package.json').write_text(json.dumps({'schema': 1, 'platform': platform.system(),
            'format': 'frozen' if frozen else 'source', 'patchSha256': checksum, 'files': files}, indent=2) + '\n')
        if output.exists() or output.is_symlink():
            raise ValueError('Output appeared during build; refusing to replace it')
        packaged.rename(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patch', required=True, type=Path)
    parser.add_argument('--patch-sha256', required=True)
    parser.add_argument('--patch-source', type=Path, default=ROOT / 'vendor/hk4e-patch-universal')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--frozen', action='store_true')
    parser.add_argument('--patch-dependency-archive', type=Path)
    args = parser.parse_args()
    try:
        print(build(args.patch, args.patch_sha256, args.patch_source, args.output, args.frozen, args.patch_dependency_archive))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Package build failed: {error}\n')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Collect notices from the same Python and Debian environment used to freeze a launcher."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import sysconfig


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def collect(package, output, windows_extra=None):
    package, output = Path(package), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    candidates = [Path(sys.base_prefix) / 'LICENSE.txt', Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt']
    python_license = next((path for path in candidates if path.is_file()), None)
    if python_license is None:
        raise ValueError('The build Python distribution has no LICENSE.txt')
    copy(python_license, output / 'python/LICENSE.txt')
    for name in ['pyinstaller', 'pyinstaller-hooks-contrib']:
        distribution = importlib.metadata.distribution(name)
        found = False
        for file in distribution.files or []:
            if '.dist-info/' in str(file).replace('\\', '/') and file.name.upper().startswith(('COPYING', 'LICENSE', 'NOTICE')):
                copy(distribution.locate_file(file), output / name / file.name)
                found = True
        if not found:
            raise ValueError('Build package lacks notices: ' + name)
    binaries = [path for path in (package / '_internal').iterdir()
                if path.is_file() and path.name != 'Astrolabe.dll'
                and ('.so' in path.name or path.suffix.lower() in {'.dll', '.pyd'})]
    inventory = []
    if os.name == 'nt':
        if windows_extra is None:
            raise ValueError('Windows external-library notices are required')
        if json.loads((Path(windows_extra) / 'sources.json').read_text())['pythonVersion'] != platform.python_version():
            raise ValueError('Windows notices do not match the build Python version')
        shutil.copytree(windows_extra, output / 'windows-external')
        for path in binaries:
            inventory.append({'file': path.name, 'sha256': digest(path),
                              'noticeGroup': 'python/LICENSE.txt and windows-external'})
    else:
        libraries = {}
        for line in subprocess.check_output(['ldconfig', '-p'], text=True).splitlines():
            if ' => ' in line:
                name, source = line.strip().split(' => ', 1)
                libraries.setdefault(name.split()[0], Path(source))
        for path in binaries:
            if path.name.startswith('libpython'):
                inventory.append({'file': path.name, 'sha256': digest(path), 'noticeGroup': 'python/LICENSE.txt'})
                continue
            source = libraries.get(path.name)
            if source is None:
                raise ValueError('Cannot identify bundled runtime library: ' + path.name)
            owner = None
            for candidate in [source, source.resolve(), Path('/usr' + str(source))]:
                result = subprocess.run(['dpkg-query', '-S', str(candidate)], capture_output=True, text=True)
                if result.returncode == 0:
                    owner = result.stdout.splitlines()[0].rsplit(': ', 1)[0]
                    break
            if owner is None:
                raise ValueError('No Debian package owns runtime library: ' + path.name)
            if digest(source) != digest(path):
                raise ValueError('Bundled library differs from its notice source: ' + path.name)
            name = owner.split(':', 1)[0]
            copyright_file = Path('/usr/share/doc') / name / 'copyright'
            copy(copyright_file, output / 'debian' / name / 'copyright')
            for license_name in re.findall(r'/usr/share/common-licenses/([A-Za-z0-9_.+-]+)', copyright_file.read_text()):
                license_file = Path('/usr/share/common-licenses') / license_name
                if license_file.is_file():
                    copy(license_file, output / 'debian/common-licenses' / license_name)
            version = subprocess.check_output(['dpkg-query', '-W', '-f', '${Version}', owner], text=True)
            inventory.append({'file': path.name, 'sha256': digest(path), 'package': owner,
                              'version': version, 'noticeGroup': 'debian/' + name})
    files = {path.relative_to(output).as_posix(): digest(path) for path in sorted(output.rglob('*')) if path.is_file()}
    (output / 'inventory.json').write_text(json.dumps({'schema': 1, 'platform': platform.system(),
        'pythonVersion': platform.python_version(), 'binaries': inventory, 'notices': files}, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--windows-extra', type=Path)
    args = parser.parse_args()
    collect(args.package, args.output, args.windows_extra)

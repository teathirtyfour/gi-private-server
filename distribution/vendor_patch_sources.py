#!/usr/bin/env python3
"""Archive Cargo-vendored dependency source after checking every file against Cargo checksums."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tomllib


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def archive_sources(vendor, lock, output):
    vendor, lock, output = Path(vendor).resolve(strict=True), Path(lock), Path(output)
    packages = [p for p in tomllib.loads(lock.read_text())['package'] if p.get('source', '').startswith('registry+')]
    expected = {p['name'] + '-' + p['version']: p['checksum'] for p in packages}
    if {p.name for p in vendor.iterdir()} != set(expected):
        raise ValueError('Vendor directory does not contain the exact locked dependency set')
    files = []
    for name, checksum in sorted(expected.items()):
        crate = vendor / name
        if crate.is_symlink():
            raise ValueError('Vendored dependency is redirected')
        manifest = json.loads((crate / '.cargo-checksum.json').read_text())
        if manifest['package'] != checksum:
            raise ValueError('Vendored package checksum differs from Cargo.lock: ' + name)
        actual = {p.relative_to(crate).as_posix(): digest(p) for p in crate.rglob('*')
                  if p.is_file() and p.name != '.cargo-checksum.json'}
        if actual != manifest['files'] or any(p.is_symlink() for p in crate.rglob('*')):
            raise ValueError('Vendored files changed: ' + name)
        files.extend(p for p in sorted(crate.rglob('*')) if p.is_file())
    header = json.dumps({'schema': 1, 'cargoLockSha256': digest(lock), 'packages': expected}, indent=2).encode()
    # Exclusive creation refuses replacement. Fixed timestamps/owners avoid leaking
    # local build paths or identity and make identical inputs reproducible.
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open('xb') as stream, gzip.GzipFile(fileobj=stream, mode='wb', filename='', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                member = tarfile.TarInfo('dependencies.json'); member.size = len(header); member.mode = 0o644
                archive.addfile(member, io.BytesIO(header))
                for path in files:
                    member = tarfile.TarInfo('vendor/' + path.relative_to(vendor).as_posix())
                    member.size = path.stat().st_size; member.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                    with path.open('rb') as source:
                        archive.addfile(member, source)
    except FileExistsError:
        raise
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor', required=True, type=Path)
    parser.add_argument('--lock', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(archive_sources(args.vendor, args.lock, args.output))

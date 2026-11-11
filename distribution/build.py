#!/usr/bin/env python3
"""Build source-addressed artifacts shared by development and distribution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fingerprint(repository, prefixes):
    """Include working edits and additions, rather than trusting commit IDs."""
    names = subprocess.check_output(['git', '-C', str(repository), 'ls-files', '-cz',
                                     '--others', '--exclude-standard']).decode().split('\0')
    h = hashlib.sha256()
    for name in sorted(set(filter(None, names))):
        if any(name == p or name.startswith(p + '/') for p in prefixes):
            path = repository / name
            if path.is_file():
                h.update(name.encode()); h.update(b'\0'); h.update(digest(path).encode())
    return h.hexdigest()


def environment(root):
    env = os.environ.copy()
    tools = root / 'toolchains'
    paths = []
    for name in ('rust/bin', 'llvm-mingw-20261104-ucrt-ubuntu-22.04-x86_64/bin', 'jdk-21.0.13+9/bin'):
        path = tools / name
        if path.is_dir():
            paths.append(str(path))
    env['PATH'] = os.pathsep.join([*paths, env.get('PATH', '')])
    if (tools / 'jdk-21.0.13+9').is_dir():
        env['JAVA_HOME'] = str(tools / 'jdk-21.0.13+9')
    for name, relative in [('GRADLE_USER_HOME', 'gradle-cache'), ('CARGO_HOME', 'cargo-cache')]:
        if (tools / relative).is_dir():
            env[name] = str(tools / relative)
    env['CARGO_TARGET_DIR'] = str(root / 'out/patch-build')
    env.setdefault('CARGO_TARGET_X86_64_PC_WINDOWS_GNULLVM_LINKER', 'x86_64-w64-mingw32-clang')
    return env


def build(component, root=ROOT):
    root = Path(root).resolve()
    if component not in ('server', 'patch'):
        raise ValueError('Component must be server or patch')
    repository = root / ('vendor/AstaPS' if component == 'server' else 'vendor/hk4e-patch-universal')
    prefixes = (['src', 'data', 'lib', 'gradle', 'build.gradle', 'settings.gradle', 'gradle.properties']
                if component == 'server' else ['src', '.cargo', 'Cargo.toml', 'Cargo.lock', 'sdk_public_key.xml', 'server_public_key.bin'])
    # In a public export these are directories of the root repository; -C still
    # discovers its Git root, so use paths relative to that root consistently.
    git_root = Path(subprocess.check_output(['git', '-C', str(repository), 'rev-parse', '--show-toplevel'], text=True).strip())
    relative = repository.relative_to(git_root)
    def inputs():
        source = fingerprint(git_root, [str(relative / p) for p in prefixes])
        return hashlib.sha256((source + digest(Path(__file__))).encode()).hexdigest()
    identity = inputs()
    output = root / 'out/builds' / component / identity
    manifest = output / 'artifact.json'
    if manifest.is_file():
        record = json.loads(manifest.read_text())
        if record['sourceFingerprint'] != identity or digest(Path(record['path'])) != record['sha256']:
            raise ValueError('Cached build was changed: ' + str(output))
        return record
    output.parent.mkdir(parents=True, exist_ok=True)
    env = environment(root)
    if component == 'server':
        name = 'development-' + identity[:16]
        command = ['bash', 'gradlew', 'jar', '-PskipHandbook=1', '-PjarFilename=' + name,
                   '--no-daemon', '--max-workers=1', '-Dorg.gradle.jvmargs=-Xmx1g']
        artifact = repository / (name + '.jar')
    else:
        command = ['cargo', 'build', '--release', '--locked', '--target', 'x86_64-pc-windows-gnullvm']
        artifact = Path(env['CARGO_TARGET_DIR']) / 'x86_64-pc-windows-gnullvm/release/ext.dll'
    subprocess.run(command, cwd=repository, env=env, check=True)
    if identity != inputs():
        raise ValueError('Source changed while building; retry after editing stops')
    with tempfile.TemporaryDirectory(prefix='.build-', dir=output.parent) as temporary:
        stage = Path(temporary)
        name = 'server.jar' if component == 'server' else 'Astrolabe.dll'
        shutil.copyfile(artifact, stage / name)
        record = {'schema': 1, 'component': component, 'sourceFingerprint': identity,
                  'sourceRevision': subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip(),
                  'path': str(output / name), 'sha256': digest(stage / name)}
        (stage / 'artifact.json').write_text(json.dumps(record, indent=2) + '\n')
        # Unique immutable output; don't overwrite an earlier build or selection.
        stage.rename(output)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=['server', 'patch'])
    args = parser.parse_args()
    print(json.dumps(build(args.component), indent=2))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Manage one generated deployment. Database ports and management HTTP stay private."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile


def checksum(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class Deployment:
    def __init__(self, directory, engine=None, compose_file='compose.yaml'):
        self.root = Path(directory).resolve(strict=True)
        self.metadata = json.loads((self.root / 'deployment.json').read_text())
        self.project = self.metadata.get('projectName', '')
        if not self.project.startswith('genshin-') and not self.project.startswith('gsvprobe-'):
            raise ValueError('Not a generated deployment with a stable project identity')
        if any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in self.project):
            raise ValueError('Invalid deployment project identity')
        self.engine = shutil.which(engine or ('docker' if shutil.which('docker') else 'podman'))
        if not self.engine:
            raise ValueError('Install Docker with Compose, or Podman with a Compose provider')
        file = (self.root / compose_file).resolve(strict=True)
        if not file.is_relative_to(self.root):
            raise ValueError('Compose file must be inside the deployment')
        self.compose = [self.engine, 'compose', '-p', self.project, '-f', str(file)]
        self._assert_local_database()

    def _assert_local_database(self):
        from urllib.parse import urlsplit
        config = json.loads((self.root / 'config.json').read_text())
        for database in config['databaseInfo'].values():
            uri = urlsplit(database['connectionUri'])
            if uri.hostname != 'database' or uri.port != 27017 or uri.path != '/genshin' or database['collection'] != 'genshin':
                raise ValueError('This tool supports the generated standalone database configuration only')

    def run(self, args, *, check=True, stdout=subprocess.PIPE, stdin=None, timeout=180):
        result = subprocess.run(args, cwd=self.root, stdout=stdout, stderr=subprocess.PIPE,
                                stdin=stdin, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(f'{Path(args[0]).name} failed (exit {result.returncode}). Check service status/logs.')
        return result

    @contextmanager
    def operation(self):
        import fcntl
        lock = self.root / '.operation.lock'
        if lock.is_symlink():
            raise ValueError('Operation lock is redirected')
        with lock.open('a+b') as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError('Another server-management operation is running') from error
            yield

    def container(self, service):
        result = self.run([self.engine, 'ps', '-a',
                           '--filter', 'label=com.docker.compose.project=' + self.project,
                           '--filter', 'label=com.docker.compose.service=' + service,
                           '--format', '{{.ID}}'])
        ids = result.stdout.decode().split()
        if len(ids) != 1:
            raise RuntimeError(f'Expected one {service} container; start this deployment first')
        return ids[0]

    def running(self, service):
        try:
            result = self.run([self.engine, 'inspect', '--format', '{{.State.Running}}', self.container(service)])
            return result.stdout.strip() == b'true'
        except RuntimeError:
            return False

    def _verify_volume(self, service, destination, suffix):
        mounts = json.loads(self.run([self.engine, 'inspect', '--format', '{{json .Mounts}}', self.container(service)]).stdout)
        selected = [m for m in mounts if m['Destination'] == destination]
        nested = [m for m in mounts if m['Destination'].startswith(destination + '/')]
        if len(selected) != 1 or nested or selected[0]['Type'] != 'volume' or selected[0]['Name'] != self.project + '_' + suffix:
            raise ValueError(f'{service} must use this deployment’s own {suffix} volume')

    def wait_ready(self, seconds=180):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = self.run([self.engine, 'exec', self.container('game'), 'curl', '-fsS', '--max-time', '3',
                               'http://127.0.0.1:8088/api/status'], check=False, timeout=10)
            try:
                status = json.loads(result.stdout)
                tick = status['game']['tick']
                if status.get('retcode') == 0 and tick['completedCount'] > 0 and 0 <= tick['ageMillis'] < 5000:
                    return status
            except (ValueError, KeyError, TypeError):
                pass
            time.sleep(1)
        raise RuntimeError('Game service did not become ready; inspect its logs')

    def start(self):
        with self.operation():
            if checksum(self.root / 'server.jar') != self.metadata['serverSha256']:
                raise ValueError('Server JAR changed; restore its recorded file before starting')
            self.run(self.compose + ['up', '-d', '--build'], timeout=300)
            return self.wait_ready()

    def stop(self):
        with self.operation():
            self.run(self.compose + ['stop'])

    def console(self):
        with self.operation():
            return subprocess.call([self.engine, 'attach', '--sig-proxy=false', self.container('game')], cwd=self.root)

    @contextmanager
    def maintenance(self, *, recreate=False):
        self._verify_volume('database', '/data/db', 'database')
        self._verify_volume('game', '/runtime/data', 'data')
        if not self.running('database'):
            raise RuntimeError('Database must be running for maintenance')
        restart = [service for service in ['game', 'proxy'] if self.running(service)]
        self.run(self.compose + ['stop', 'proxy', 'game'])
        if self.running('game') or self.running('proxy'):
            raise RuntimeError('Game services did not stop; maintenance aborted')
        logs = self.run([self.engine, 'logs', '--tail', '100', self.container('game')]).stdout
        if b'Database shutdown incomplete' in logs:
            raise RuntimeError('Database writers did not drain; services left stopped for inspection')
        completed = False
        try:
            yield
            completed = True
        finally:
            # A partially failed restore must stay offline. Backups set no database
            # state and handle their own restart on failure before leaving this context.
            if completed and restart:
                self.run(self.compose + ['up', '-d'] + (['--force-recreate'] if recreate else []) + restart)
                if 'game' in restart:
                    self.wait_ready()

    def _snapshot(self, destination):
        destination = Path(destination).absolute()
        if destination.exists() or destination.is_symlink():
            raise ValueError('Backup destination already exists')
        if destination.resolve().is_relative_to(self.root / 'resources'):
            raise ValueError('Keep backups outside resource inputs')
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix='.backup-', dir=destination.parent) as temporary:
            stage = Path(temporary)
            database = stage / 'database.archive.gz'
            with database.open('xb') as stream:
                self.run([self.engine, 'exec', self.container('database'), 'mongodump', '--config=/run/mongo-tools.yml',
                          '--db=genshin', '--archive', '--gzip'], stdout=stream)
            data = stage / 'data'; data.mkdir()
            self.run([self.engine, 'cp', self.container('game') + ':/runtime/data/.', str(data)])
            for item in data.rglob('*'):
                if item.is_symlink() or (not item.is_file() and not item.is_dir()):
                    raise ValueError('Data backups require regular files and directories')
            configuration = stage / 'configuration'; configuration.mkdir()
            for name in ['config.json', '.env', 'compose.yaml', 'Caddyfile', 'init-db.js', 'deployment.json', 'mongo-tools.yml']:
                shutil.copyfile(self.root / name, configuration / name)
            manifest = {'schema': 1, 'serverSha256': self.metadata['serverSha256'],
                        'created': datetime.now(timezone.utc).isoformat(),
                        'files': {str(p.relative_to(stage)): checksum(p) for p in stage.rglob('*') if p.is_file()}}
            (stage / 'backup.json').write_text(json.dumps(manifest, indent=2) + '\n')
            archive = stage / 'completed.tar.gz'
            with tarfile.open(archive, 'w:gz') as package:
                for item in sorted(stage.iterdir()):
                    if item != archive:
                        package.add(item, arcname=item.name)
            # Publish without overwriting even if another process creates the destination.
            archive.chmod(0o600)
            os.link(archive, destination)
        return destination

    def backup(self, destination):
        with self.operation():
            error = None
            with self.maintenance():
                try:
                    result = self._snapshot(destination)
                except Exception as failure:
                    # No saved data was changed by a failed snapshot. Restore the
                    # original service state before surfacing the error.
                    error = failure
            if error is not None:
                raise error
            return result

    @staticmethod
    def _unpack(archive, directory):
        with tarfile.open(archive, 'r:gz') as package:
            for member in package:
                path = PurePosixPath(member.name)
                if not path.parts or path.is_absolute() or '..' in path.parts or '\\' in member.name or path.parts[0] not in {
                    'backup.json', 'database.archive.gz', 'data', 'configuration'}:
                    raise ValueError('Unsafe backup member')
                target = directory.joinpath(*path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open('xb') as stream, package.extractfile(member) as source:
                        shutil.copyfileobj(source, stream)
                    target.chmod(0o600)
                else:
                    raise ValueError('Backup contains a link or special file')
        manifest = json.loads((directory / 'backup.json').read_text())
        if manifest.get('schema') != 1:
            raise ValueError('Unsupported backup format')
        files = {str(p.relative_to(directory)): checksum(p) for p in directory.rglob('*') if p.is_file() and p != directory / 'backup.json'}
        if files != manifest['files'] or not (directory / 'database.archive.gz').is_file() or not (directory / 'data').is_dir():
            raise ValueError('Backup content/hash verification failed')
        return manifest

    def restore(self, archive, *, replace=False, allow_build_change=False):
        if not replace:
            raise ValueError('Restore replaces saved progress. Supply --replace to request it explicitly.')
        with self.operation(), tempfile.TemporaryDirectory(prefix='.restore-', dir=self.root) as temporary:
            stage = Path(temporary)
            manifest = self._unpack(archive, stage)
            if manifest['serverSha256'] != self.metadata['serverSha256'] and not allow_build_change:
                raise ValueError('Backup is from another build; select its server build or use --allow-build-change')
            safety = self.root / 'backups' / ('before-restore-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '.tar.gz')
            with self.maintenance():
                self._snapshot(safety)
                self._restore_data(stage)
            return safety

    def _restore_data(self, stage):
        # Clear the application database, including collections created after the
        # backup. Database users live in admin and remain. Caller holds maintenance.
        drop = ("const c=new Mongo(); if(!c.getDB('admin').auth('administrator',"
                        "process.env.MONGO_INITDB_ROOT_PASSWORD).ok) quit(2); "
                        "if(!c.getDB('genshin').dropDatabase().ok) quit(3);")
        self.run([self.engine, 'exec', self.container('database'), 'mongosh', '--quiet', '--eval', drop])
        with (stage / 'database.archive.gz').open('rb') as stream:
            self.run([self.engine, 'exec', '-i', self.container('database'), 'mongorestore',
                      '--config=/run/mongo-tools.yml', '--archive', '--gzip', '--drop', '--nsInclude=genshin.*'], stdin=stream)
        self.run(self.compose + ['run', '--rm', '--no-deps', '--user', '0:0', '--entrypoint', 'sh', 'game', '-c',
                                         'find /runtime/data -mindepth 1 -delete'])
        self.run([self.engine, 'cp', str(stage / 'data') + '/.', self.container('game') + ':/runtime/data'])
        self.run(self.compose + ['run', '--rm', '--no-deps', '--user', '0:0', '--entrypoint', 'sh', 'game', '-c',
                                         'chown -R 1000:1000 /runtime/data'])

    @staticmethod
    def verify_jar(jar, expected):
        jar = Path(jar).resolve(strict=True)
        if checksum(jar) != expected:
            raise ValueError('Server JAR checksum does not match the requested build')
        with zipfile.ZipFile(jar) as package:
            if 'emu/grasscutter/auth/StrictPasswordAuthenticator.class' not in package.namelist():
                raise ValueError('Server JAR lacks strict authentication support')
        return jar

    def _install_build(self, jar, metadata):
        # Both old files are already retained in a recovery directory. Atomic
        # individual replacements ensure an interrupted change is detectable by start.
        with tempfile.TemporaryDirectory(prefix='.install-', dir=self.root) as directory:
            temporary = Path(directory) / 'server.jar'
            shutil.copyfile(jar, temporary)
            temporary.replace(self.root / 'server.jar')
            temporary = Path(directory) / 'deployment.json'
            temporary.write_text(json.dumps(metadata, indent=2) + '\n')
            temporary.replace(self.root / 'deployment.json')
        self.metadata = metadata

    def change_build(self, jar, expected, *, restore_archive=None, replace=False):
        if restore_archive is not None and not replace:
            raise ValueError('Rollback restores saved progress. Supply --replace explicitly.')
        jar = self.verify_jar(jar, expected)
        with self.operation(), tempfile.TemporaryDirectory(prefix='.build-change-', dir=self.root) as temporary:
            stage = Path(temporary)
            # Retain the exact validated input even if the external file changes.
            shutil.copyfile(jar, stage / 'next.jar')
            self.verify_jar(stage / 'next.jar', expected)
            current_sha = checksum(self.root / 'server.jar')
            if current_sha != self.metadata['serverSha256'] and restore_archive is None:
                raise ValueError('Current JAR differs from recorded build; recover it before changing builds')
            if restore_archive is not None:
                restored = stage / 'restore'; restored.mkdir()
                manifest = self._unpack(restore_archive, restored)
                if manifest['serverSha256'] != expected:
                    raise ValueError('Rollback snapshot does not match the selected server build')
            # Rollback also repairs an interruption between the JAR and descriptor
            # replacements. Its safety record must describe the actual current JAR.
            self.metadata = {**self.metadata, 'serverSha256': current_sha}
            history = self.root / 'build-history' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
            history.mkdir(parents=True, mode=0o700)
            shutil.copyfile(self.root / 'server.jar', history / 'server.jar')
            (history / 'deployment.json').write_text(json.dumps(self.metadata, indent=2) + '\n')
            receipt = {'schema': 1, 'previousSha256': self.metadata['serverSha256'],
                       'requestedSha256': expected, 'state': 'preparing'}
            receipt_path = history / 'change.json'
            receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
            try:
                with self.maintenance(recreate=True):
                    self._snapshot(history / 'progress.tar.gz')
                    receipt['state'] = 'backup-complete'
                    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
                    self._install_build(stage / 'next.jar', {**self.metadata, 'serverSha256': expected})
                    self.run(self.compose + ['build', 'game'], timeout=300)
                    if restore_archive is not None:
                        self._restore_data(restored)
                receipt['state'] = 'completed'
            except Exception:
                # Never let a failed/partially migrated build continue accepting players.
                self.run(self.compose + ['stop', 'proxy', 'game'], check=False)
                receipt['state'] = 'failed; services stopped; recover with rollback --replace'
                raise
            finally:
                receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
            return history

    def rollback(self, history, *, replace=False):
        if not replace:
            raise ValueError('Rollback restores saved progress. Supply --replace explicitly.')
        history = Path(history).resolve(strict=True)
        if not history.is_relative_to(self.root / 'build-history'):
            raise ValueError('Select a build-history directory from this deployment')
        previous = json.loads((history / 'deployment.json').read_text())
        if previous['projectName'] != self.project:
            raise ValueError('Recovery directory belongs to another deployment')
        return self.change_build(history / 'server.jar', previous['serverSha256'],
                                 restore_archive=history / 'progress.tar.gz', replace=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--engine', choices=['docker', 'podman'])
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ['start', 'stop', 'status', 'console', 'logs']:
        commands.add_parser(action)
    commands.add_parser('backup').add_argument('destination', type=Path)
    restore = commands.add_parser('restore')
    restore.add_argument('archive', type=Path)
    restore.add_argument('--replace', action='store_true')
    restore.add_argument('--allow-build-change', action='store_true')
    upgrade = commands.add_parser('upgrade')
    upgrade.add_argument('jar', type=Path)
    upgrade.add_argument('--sha256', required=True)
    rollback = commands.add_parser('rollback')
    rollback.add_argument('history', type=Path)
    rollback.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    try:
        deployment = Deployment(args.deployment, args.engine)
        if args.action == 'start':
            state = deployment.start(); print(f"Server ready; {state['game']['players']} player(s) online.")
        elif args.action == 'stop':
            deployment.stop(); print('Server services stopped; saved volumes retained.')
        elif args.action == 'status':
            result = deployment.run(deployment.compose + ['ps']); print(result.stdout.decode())
            if deployment.running('game'):
                print(json.dumps(deployment.wait_ready(10), indent=2))
        elif args.action == 'console':
            return deployment.console()
        elif args.action == 'logs':
            print(deployment.run(deployment.compose + ['logs', '--tail', '100', 'game']).stdout.decode())
        elif args.action == 'backup':
            print('Backup created:', deployment.backup(args.destination))
        elif args.action == 'upgrade':
            print('Build installed. Recovery directory:', deployment.change_build(args.jar, args.sha256))
        elif args.action == 'rollback':
            print('Build and progress restored. Safety recovery directory:', deployment.rollback(args.history, replace=args.replace))
        else:
            print('Restore completed. Safety backup:', deployment.restore(args.archive, replace=args.replace,
                  allow_build_change=args.allow_build_change))
    except (OSError, ValueError, RuntimeError, KeyError, zipfile.BadZipFile, subprocess.TimeoutExpired) as error:
        parser.exit(1, f'Management failed: {error}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

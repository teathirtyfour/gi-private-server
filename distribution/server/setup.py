#!/usr/bin/env python3
"""Prepare a fresh public-server deployment; never start services or edit a live server."""
import argparse
import hashlib
import importlib.util
import ipaddress
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import zipfile

IMAGES = {
    'database': 'docker.io/library/mongo@sha256:8bb4442761a63a8dda452744082cdac9644b429807eb977b42324a63cef59ca4',
    'java': 'docker.io/library/eclipse-temurin@sha256:0b99b3376fbb50dbf9c0af8535fdd5b8eb0d3fdfc8b1e4967f383b6919f6901a',
    'proxy': 'docker.io/library/caddy@sha256:ce773ec7a742ce15ee6bf2bb5f65882b9e2a4487b10733e6f1484f86e6b5d8bc',
}


def hostname(value):
    if not isinstance(value, str) or len(value) > 253 or not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value
    ):
        raise ValueError("Use a hostname without a scheme, path or port")
    if any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-')
           for label in value.split('.')):
        raise ValueError("Invalid hostname")
    return value.lower()


def public_domain(value):
    value = hostname(value)
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if '.' in value and not value.endswith(('.local', '.localhost', '.invalid', '.test')):
            return value
    raise ValueError("Public HTTPS requires your DNS domain, such as play.example.org")


def configuration(domain, game_host, password, game_port=22101):
    domain = public_domain(domain)
    game_host = hostname(game_host)
    if not isinstance(game_port, int) or isinstance(game_port, bool) or not 1024 <= game_port <= 65535:
        raise ValueError("Game port must be between 1024 and 65535")
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', password):
        raise ValueError("Database password must be a generated URL-safe secret")
    uri = f'mongodb://game:{password}@database:27017/genshin?authSource=genshin'
    return {
        'version': 18,
        'folderStructure': {'resources': './resources/'},
        'databaseInfo': {name: {'connectionUri': uri, 'collection': 'genshin'}
                         for name in ('server', 'game')},
        'server': {
            'http': {
                'bindAddress': '0.0.0.0', 'bindPort': 8088,
                'accessAddress': domain, 'accessPort': 443,
                'encryption': {'useEncryption': False, 'useInRouting': True},
                'webConsole': {'enabled': False},
                'policies': {'cors': {'enabled': False}},
            },
            'game': {
                'bindAddress': '0.0.0.0', 'bindPort': game_port,
                'accessAddress': game_host, 'accessPort': game_port,
                'enableConsole': True,
                'watchdog': {'enableAutoRestart': False},
                'logPackets': 'NONE', 'isShowPacketPayload': False,
                'enableScriptInBigWorld': True,
            },
            'dispatch': {'defaultName': 'Private Server', 'logRequests': 'NONE'},
        },
        'account': {'autoCreate': False, 'strictPasswordChecks': True,
                    'defaultPermissions': []},
        'language': {'language': 'en_US'},
    }


COMPOSE = '''services:
  database:
    image: mongo:7.0
    restart: unless-stopped
    command: [mongod, --wiredTigerCacheSizeGB, '0.5']
    environment:
      MONGO_INITDB_ROOT_USERNAME: administrator
      MONGO_INITDB_ROOT_PASSWORD: ${MONGO_ADMIN_PASSWORD:?Run setup.py first}
      MONGO_INITDB_DATABASE: genshin
      GAME_DB_PASSWORD: ${GAME_DB_PASSWORD:?Run setup.py first}
    volumes:
      - database:/data/db
      - ./init-db.js:/docker-entrypoint-initdb.d/01-game-user.js:ro
      - ./mongo-tools.yml:/run/mongo-tools.yml:ro
    networks: [database]
    healthcheck:
      test: [CMD, mongosh, --quiet, --eval, "const d=new Mongo().getDB('genshin'); if(!d.auth('game',process.env.GAME_DB_PASSWORD).ok) quit(1); quit(d.runCommand({ping:1}).ok ? 0 : 1)"]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 30s
    logging: &logging
      driver: json-file
      options: {max-size: 10m, max-file: '3'}
  game:
    build: .
    restart: unless-stopped
    init: true
    stop_grace_period: 60s
    depends_on:
      database: {condition: service_healthy}
    networks: [database, frontend]
    volumes:
      - ./config.json:/runtime/config.json:ro
      - ./resources:/runtime/resources:ro
      - data:/runtime/data
      - cache:/runtime/cache
      - logs:/runtime/logs
    ports:
      - '${GAME_PORT:?Run setup.py first}:${GAME_PORT:?Run setup.py first}/udp'
    stdin_open: true
    tty: true
    logging: *logging
  proxy:
    image: caddy:2
    restart: unless-stopped
    networks: [frontend]
    depends_on: [game]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - certificates:/data
      - caddy_config:/config
    ports: ['80:80/tcp', '443:443/tcp']
    logging: *logging
networks:
  database: {internal: true}
  frontend: {}
volumes:
  database: {}
  data: {}
  cache: {}
  logs: {}
  certificates: {}
  caddy_config: {}
'''

DOCKERFILE = '''FROM eclipse-temurin:21-jre
RUN mkdir -p /opt/server /runtime/data /runtime/cache /runtime/logs \\
    && chown -R 1000:1000 /runtime /opt/server
COPY --chown=1000:1000 server.jar /opt/server/server.jar
WORKDIR /runtime
USER 1000:1000
ENTRYPOINT ["java", "-Xms256m", "-Xmx3g", "-jar", "/opt/server/server.jar"]
'''

COMPOSE = COMPOSE.replace('image: mongo:7.0', 'image: ' + IMAGES['database']).replace(
    'image: caddy:2', 'image: ' + IMAGES['proxy'])
DOCKERFILE = DOCKERFILE.replace('FROM eclipse-temurin:21-jre', 'FROM ' + IMAGES['java'])


def checked_jar(jar):
    jar = Path(jar).resolve(strict=True)
    if not jar.is_file():
        raise ValueError('Supply a server JAR')
    try:
        with zipfile.ZipFile(jar) as package:
            if 'emu/grasscutter/auth/StrictPasswordAuthenticator.class' not in package.namelist():
                raise ValueError('Server JAR lacks strict public authentication; build the release server first')
    except zipfile.BadZipFile as error:
        raise ValueError('Server file is not a valid JAR') from error
    return jar


def prepare_downloaded(output, domain, game_host, jar, game_port=22101, cache=None, *, downloader=None):
    """First-time provisioning: download and verify before creating the deployment."""
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Output already exists; select a new deployment directory')
    jar = checked_jar(jar)
    configuration(domain, game_host, 'a' * 48, game_port)  # Validate before network I/O.
    if downloader is None:
        spec = importlib.util.spec_from_file_location('deployment_resources', Path(__file__).with_name('resources.py'))
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        downloader = module.download_prepare
    output.parent.mkdir(parents=True, exist_ok=True)
    cache = Path(cache) if cache is not None else output.parent / '.resource-downloads'
    with tempfile.TemporaryDirectory(prefix='.downloaded-resources-', dir=output.parent) as temporary:
        resources = downloader(Path(temporary) / 'resources', cache)
        return prepare(output, domain, game_host, jar, resources, game_port)


def prepare(output, domain, game_host, jar, resources, game_port=22101):
    """All output is staged then renamed; an existing deployment is never overwritten."""
    output, jar, resources = Path(output).absolute(), checked_jar(jar), Path(resources).resolve(strict=True)
    if output.exists() or output.is_symlink():
        raise ValueError('Output already exists; select a new deployment directory')
    if not resources.is_dir():
        raise ValueError('Supply a resource directory')
    for directory in ('ExcelBinOutput', 'BinOutput', 'Scripts', 'TextMap'):
        if not (resources / directory).is_dir():
            raise ValueError(f'Resource directory is missing {directory}')
    domain, game_host = public_domain(domain), hostname(game_host)
    game_password, administrator_password = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    project = 'genshin-' + secrets.token_hex(6)
    config = configuration(domain, game_host, game_password, game_port)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.server-setup-', dir=output.parent) as directory:
        stage = Path(directory) / 'deployment'
        stage.mkdir(mode=0o700)
        files = {
            'config.json': json.dumps(config, indent=2) + '\n',
            '.env': f'MONGO_ADMIN_PASSWORD={administrator_password}\nGAME_DB_PASSWORD={game_password}\nGAME_PORT={game_port}\n',
            'compose.yaml': 'name: ' + project + '\n' + COMPOSE,
            'Dockerfile': DOCKERFILE,
            '.dockerignore': '*\n!Dockerfile\n!server.jar\n',
            '.gitignore': '*\n',
            'Caddyfile': f'{domain} {{\n\t@management path /console /console/* /status /status/* /health /health/* /api /api/*\n\trespond @management 404\n\treverse_proxy game:8088\n}}\n',
            'init-db.js': "db.getSiblingDB('genshin').createUser({user:'game',pwd:process.env.GAME_DB_PASSWORD,roles:[{role:'readWrite',db:'genshin'}]});\n",
            'mongo-tools.yml': json.dumps({'uri': 'mongodb://game@127.0.0.1:27017/genshin?authSource=genshin',
                                          'password': game_password}) + '\n',
            'player-server.json': json.dumps({'name': 'Private Server', 'url': f'https://{domain}', 'gameVersion': '7.2.0'}, indent=2) + '\n',
        }
        for name, contents in files.items():
            path = stage / name
            path.write_text(contents, encoding='utf-8')
            # The deployment directory is owner-only. Container service users must
            # still be able to read their individual bind-mounted configuration.
            path.chmod(0o600 if name == '.env' else 0o644)
        shutil.copyfile(jar, stage / 'server.jar')
        shutil.copyfile(Path(__file__).with_name('manage.py'), stage / 'manage.py')
        # Materialize resource overlays: absolute host symlinks would otherwise be
        # broken inside a container or after moving the deployment to another host.
        # Source resources remain read-only inputs; never copy their Git/cache trees.
        shutil.copytree(resources, stage / 'resources', symlinks=False,
                        ignore=shutil.ignore_patterns('.git', '__pycache__', '.cache'))
        with jar.open('rb') as stream:
            jar_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        (stage / 'deployment.json').write_text(json.dumps({
            'serverSha256': jar_hash,
            'projectName': project,
            'images': IMAGES,
            'domain': domain, 'gameHost': game_host, 'gamePort': game_port,
            'preparation': 'Prepared; check running services with manage.py status.',
        }, indent=2) + '\n')
        # Path.rename would replace an existing empty directory on POSIX; reject it explicitly.
        if output.exists() or output.is_symlink():
            raise ValueError('Output appeared during preparation; refusing to replace it')
        stage.rename(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain', required=True, help='DNS domain for public HTTPS login/dispatch')
    parser.add_argument('--game-host', help='DNS/IP advertised for UDP; defaults to domain')
    parser.add_argument('--game-port', type=int, default=22101)
    parser.add_argument('--jar', required=True, type=Path)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--resources', type=Path)
    inputs.add_argument('--download-resources', action='store_true', help='Download and verify pinned resources during first-time setup')
    parser.add_argument('--resource-cache', type=Path, help='Persistent download cache used with --download-resources')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.download_resources:
            output = prepare_downloaded(args.output, args.domain, args.game_host or args.domain,
                                        args.jar, args.game_port, args.resource_cache)
        else:
            if args.resource_cache:
                raise ValueError('--resource-cache requires --download-resources')
            output = prepare(args.output, args.domain, args.game_host or args.domain,
                             args.jar, args.resources, args.game_port)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f'Setup failed: {error}\n')
    print(f'Prepared {output}. No services started. Review configuration before deployment.')


if __name__ == '__main__':
    main()

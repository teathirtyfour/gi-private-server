"""Optional Linux Wine/performance profile used by source and packaged launchers."""
import json
import os
from pathlib import Path
import shutil
import tomllib


def digest(path):
    import hashlib
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def set_lsfg_dll(config_file, shader_path):
    """Set the Lossless Scaling DLL in the isolated lsfg-vk TOML config."""
    lines = config_file.read_text().splitlines()
    section_start = next((i for i, line in enumerate(lines) if line.strip() == '[global]'), None)
    if section_start is None:
        first_table = next((i for i, line in enumerate(lines)
                            if line.strip().startswith('[')
                            and line.strip().endswith(']')), len(lines))
        lines[first_table:first_table] = ['[global]', f'dll = {json.dumps(str(shader_path))}', '']
    else:
        section_end = next((i for i in range(section_start + 1, len(lines))
                            if lines[i].strip().startswith('[')
                            and lines[i].strip().endswith(']')), len(lines))
        dll_line = next((i for i in range(section_start + 1, section_end)
                         if '=' in lines[i]
                         and lines[i].split('=', 1)[0].strip() == 'dll'), None)
        assignment = f'dll = {json.dumps(str(shader_path))}'
        if dll_line is None:
            lines.insert(section_start + 1, assignment)
        else:
            lines[dll_line] = assignment
    config_file.write_text('\n'.join(lines) + '\n')



def launch_plan(config, root, client, no_frame_generation=False, no_ntsync=False):
    ROOT, CLIENT = Path(root).resolve(), Path(client).resolve()
    PREFIX = Path(config['prefix']).resolve()
    if not PREFIX.is_relative_to(ROOT):
        raise ValueError('Profile prefix must be inside its private workspace')
    if Path(config['prefix']) != PREFIX or not PREFIX.is_dir() or PREFIX.is_symlink():
        raise RuntimeError('Expected copied prefix is missing or redirected')
    if (PREFIX / 'dosdevices/c:').resolve() != PREFIX / 'drive_c':
        raise RuntimeError('Copied C: mapping escapes its prefix')
    if (PREFIX / 'dosdevices/g:').resolve() != CLIENT:
        raise RuntimeError('Copied G: mapping does not target the isolated game')
    paths = {name: ROOT / 'runtime' / name for name in
             ('matched-home', 'matched-cache', 'matched-xdg-data', 'matched-tmp')}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    config_home = paths['matched-home'] / '.config'
    config_home.mkdir(exist_ok=True)
    # Overlay defaults depend on HOME/XDG_CONFIG_HOME. Preserve the normal
    # configuration in the private home rather than falling back to a missing
    # default Lossless Scaling DLL. Never replace later user edits in the copy.
    for relative in (Path('lsfg-vk/conf.toml'), Path('MangoHud/MangoHud.conf')):
        source = Path.home() / '.config' / relative
        destination = config_home / relative
        if source.is_file() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    (paths['matched-tmp'] / '.X11-unix').mkdir(exist_ok=True)
    wine = config['wine']
    runner = Path(wine).parent.parent
    libraries = [runner / p for p in ('lib', 'lib64', 'lib/wine/x86_64-unix',
                 'lib32/wine/x86_64-unix', 'lib64/wine/x86_64-unix')]
    env = {
        'HOME': str(paths['matched-home']), 'WINEPREFIX': str(PREFIX),
        'XDG_CONFIG_HOME': str(config_home),
        'WINEARCH': config['winearch'],
        'WINE_ENABLE_TIMEOUT_FIX': '1' if config['timeout_fix'] else '0',
        'XDG_CACHE_HOME': str(paths['matched-cache']), 'XDG_DATA_HOME': str(paths['matched-xdg-data']),
        'TMPDIR': '/tmp', 'DXVK_STATE_CACHE_PATH': str(paths['matched-cache']),
        '__GL_SHADER_DISK_CACHE_PATH': str(paths['matched-cache']),
        'GST_PLUGIN_PATH': '', 'LD_LIBRARY_PATH': ':'.join(map(str, libraries)),
        'WINEFSYNC': '1' if config['sync'] == 'FSync' else '0',
        'WINEESYNC': '1' if config['sync'] == 'ESync' else '0',
        'WINE_FULLSCREEN_FSR': '0', 'WINEDLLOVERRIDES': 'd3d11,dxgi=n,b;Astrolabe=n,b',
        'WINEDEBUG': '-all,err+all', 'MANGOHUD': '1' if config['hud'] == 'MangoHUD' else '0',
    }
    if no_ntsync:
        # DWProton 11.0-11 checks this in server/inproc_sync.c before
        # /dev/ntsync. WINEFSYNC=1 then selects its futex-based backend.
        if config['sync'] != 'FSync':
            raise RuntimeError('--no-ntsync comparison requires the matched FSync configuration')
        env['PROTON_NO_NTSYNC'] = '1'
    hud_config = config_home / 'MangoHud/MangoHud.conf'
    if config['hud'] == 'MangoHUD' and hud_config.is_file():
        # Explicitly select private telemetry settings, including when a caller
        # has a different MANGOHUD_CONFIGFILE in its desktop environment.
        env['MANGOHUD_CONFIGFILE'] = str(hud_config)
    compose = Path('/usr/share/X11/locale/en_US.UTF-8/Compose')
    if compose.is_file():
        env['XLOCALEDIR'] = str(compose.parent.parent)
        env['XCOMPOSEFILE'] = str(compose)
    lsfg_config = config_home / 'lsfg-vk/conf.toml'
    if no_frame_generation:
        env['DISABLE_LSFGVK'] = '1'
    elif lsfg_config.is_file():
        # A first launch may already have created a default private profile with
        # no DLL path. Keep that profile, but resolve its missing shader location
        # from the normal setup instead of searching the new empty data home.
        private = tomllib.loads(lsfg_config.read_text())
        shader = os.environ.get('LSFGVK_DLL_PATH') or private.get('global', {}).get('dll')
        normal_path = Path.home() / '.config/lsfg-vk/conf.toml'
        if not shader and normal_path.is_file():
            normal = tomllib.loads(normal_path.read_text())
            shader = normal.get('global', {}).get('dll')
        if shader:
            shader_path = Path(shader).expanduser()
            if not shader_path.is_file():
                raise RuntimeError('The configured lsfg-vk shader DLL is missing; use --no-frame-generation to launch without that layer')
            # LSFGVK_DLL_PATH is an environment-only override gated by
            # LSFGVK_ENV=1. This launch uses a profile TOML, so set `dll` there
            # and retain profile matching and the user's other LSFG settings.
            set_lsfg_dll(lsfg_config, shader_path.resolve())
        env['LSFGVK_CONFIG'] = str(lsfg_config)
    if config['winewayland']:
        if not os.environ.get('WAYLAND_DISPLAY'):
            raise RuntimeError('The normal setup uses Wayland; run this from your Wayland desktop session')
        env['DISPLAY'] = ''
    isolation = ['bwrap', '--die-with-parent', '--ro-bind', '/', '/', '--bind', str(ROOT), str(ROOT),
                 '--dev-bind', '/dev', '/dev', '--proc', '/proc',
                 '--bind', str(paths['matched-tmp']), '/tmp', '--chdir', str(CLIENT)]
    if Path('/tmp/.X11-unix').is_dir():
        isolation += ['--ro-bind', '/tmp/.X11-unix', '/tmp/.X11-unix']
    for name, value in env.items():
        isolation += ['--setenv', name, value]
    game = [wine, str(CLIENT / 'GenshinImpact.exe')]
    if config['borderless']:
        game += ['-screen-fullscreen', '0', '-popupwindow']
    if config['gamemode']:
        if not shutil.which('gamemoderun'):
            raise RuntimeError('GameMode is enabled in the normal setup but gamemoderun is missing')
        game.insert(0, 'gamemoderun')
    fps = config['fps_unlocker']
    if fps['enabled']:
        if digest(Path(fps['path'])) != fps['sha256']:
            raise RuntimeError('Copied FPS unlocker hash changed')
        script = '"$1" "$2" "$3" "$4" & unlocker_pid=$!; trap \'kill "$unlocker_pid" 2>/dev/null || :\' EXIT; shift 4; "$@"'
        command = ['bash', '-c', script, 'matched-private-client', wine, fps['path'],
                   str(fps['fps']), str(fps['interval']), *game]
    else:
        command = game
    return isolation, command, env


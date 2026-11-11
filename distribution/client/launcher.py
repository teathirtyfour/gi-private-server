#!/usr/bin/env python3
"""Desktop front end for the portable client. No game files are bundled."""
import json
import argparse
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import client


def bundle_directory():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def load_release(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / 'patch-manifest.json').read_text())
    if manifest.get('gameVersion') != client.VERSION or manifest.get('schema') != 1:
        raise ValueError('This release does not describe the supported game version')
    filename = manifest['patch']['file']
    path = directory / filename
    if path.is_symlink() or not path.resolve().is_relative_to(directory) or not path.is_file():
        raise ValueError('Bundled patch is missing or redirected')
    checksum = manifest['patch']['sha256']
    if client.digest(path) != checksum:
        raise ValueError('Bundled patch checksum failed; obtain a fresh release')
    return path, checksum


def settings_file():
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    else:
        base = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    return base / 'PrivateServerLauncher/settings.json'


class Launcher:
    def __init__(self, root, patch, checksum, settings=None):
        self.root, self.patch, self.checksum = root, patch, checksum
        self.settings = Path(settings) if settings else settings_file()
        self.events = queue.Queue()
        self.cancel_copy = threading.Event()
        self.busy = False
        self.buttons = []
        root.title('Private Server Launcher')
        root.minsize(690, 440)
        root.protocol('WM_DELETE_WINDOW', self.close)
        outer = ttk.Frame(root, padding=22)
        outer.pack(fill='both', expand=True)
        outer.columnconfigure(1, weight=1)
        ttk.Label(outer, text='Private Server', font=('', 21, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(outer, text=f'Compatible game: {client.VERSION} · Windows / Linux').grid(row=1, column=0, columnspan=3, sticky='w', pady=(3, 16))
        self.url = tk.StringVar(value='http://127.0.0.1:8088')
        self.folder = tk.StringVar(value=str(Path.home() / 'Games/GenshinPrivate'))
        self.source = tk.StringVar()
        self.wine = tk.StringVar(value='wine')
        self.linux_profile = tk.StringVar()
        self.remember = tk.BooleanVar(value=False)
        self.shared = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value='Choose a server and private game folder. First time? Prepare a private copy below.')
        self.field(outer, 2, 'Server address', self.url, 'Import profile', self.import_profile)
        self.field(outer, 3, 'Private game folder', self.folder, 'Browse', lambda: self.browse(self.folder))
        self.field(outer, 4, 'Normal game folder', self.source, 'Browse', lambda: self.browse(self.source))
        if os.name != 'nt':
            self.field(outer, 5, 'Wine executable', self.wine, 'Browse', self.browse_wine)
        ttk.Checkbutton(outer, text='Share installed assets to save space (keep the source at 7.2; Linux requires bubblewrap)',
                        variable=self.shared).grid(row=6, column=0, columnspan=3, sticky='w', pady=(10, 5))
        ttk.Checkbutton(outer, text='Remember private login for this launch', variable=self.remember).grid(
            row=7, column=0, columnspan=3, sticky='w', pady=5)
        actions = ttk.Frame(outer)
        actions.grid(row=8, column=0, columnspan=3, sticky='w', pady=12)
        for title, command in [('Prepare private copy', self.prepare), ('Check files', self.check),
                               ('Test connection', self.diagnose), ('Play', self.play)]:
            button = ttk.Button(actions, text=title, command=command)
            button.pack(side='left', padx=(0, 9)); self.buttons.append(button)
        self.cancel_button = ttk.Button(actions, text='Cancel copy', command=self.cancel_copy.set)
        self.cancel_button.pack(side='left')
        self.cancel_button.state(['disabled'])
        maintenance = ttk.Frame(outer)
        maintenance.grid(row=9, column=0, columnspan=3, sticky='w')
        for title, command in [('Update private patch', self.update_patch), ('Restore previous patch', self.restore_patch)]:
            button = ttk.Button(maintenance, text=title, command=command)
            button.pack(side='left', padx=(0, 9)); self.buttons.append(button)
        if os.name != 'nt':
            button = ttk.Button(maintenance, text='Wine/performance profile', command=self.choose_linux_profile)
            button.pack(side='left'); self.buttons.append(button)
        self.progress = ttk.Progressbar(outer, maximum=100)
        self.progress.grid(row=10, column=0, columnspan=3, sticky='ew', pady=6)
        ttk.Label(outer, textvariable=self.status, wraplength=640).grid(row=11, column=0, columnspan=3, sticky='w')
        self.restore_settings()
        self.poll_id = root.after(100, self.poll)

    def field(self, parent, row, title, variable, button_title, command):
        ttk.Label(parent, text=title).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=5)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky='ew', pady=5)
        button = ttk.Button(parent, text=button_title, command=command)
        button.grid(row=row, column=2, padx=(8, 0), pady=5); self.buttons.append(button)

    def browse(self, variable):
        directory = filedialog.askdirectory(parent=self.root, title='Choose game folder')
        if directory:
            variable.set(directory)

    def browse_wine(self):
        path = filedialog.askopenfilename(parent=self.root, title='Choose Wine executable')
        if path:
            self.wine.set(path)

    def choose_linux_profile(self):
        path = filedialog.askopenfilename(parent=self.root, title='Choose private Linux Wine/performance profile',
                                          filetypes=[('Linux profile', '*.json')])
        if path:
            self.linux_profile.set(path)
            self.status.set('Linux Wine/performance profile selected: ' + path)

    def import_profile(self):
        path = filedialog.askopenfilename(parent=self.root, title='Import player-server.json', filetypes=[('Server profile', '*.json')])
        if not path:
            return
        try:
            profile = json.loads(Path(path).read_text())
            if profile.get('gameVersion') != client.VERSION:
                raise ValueError('Server profile is for another game version')
            self.url.set(client.server_origin(profile['url']))
        except (OSError, ValueError, KeyError) as error:
            messagebox.showerror('Cannot import profile', str(error), parent=self.root)

    def restore_settings(self):
        try:
            data = json.loads(self.settings.read_text())
            self.url.set(client.server_origin(data['server']))
            self.folder.set(data['client']); self.wine.set(data.get('wine', 'wine'))
            self.linux_profile.set(data.get('linuxProfile', ''))
            self.shared.set(data.get('shared', True))
        except (OSError, ValueError, KeyError):
            pass

    def save_settings(self):
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(json.dumps({'server': client.server_origin(self.url.get()),
                                             'client': self.folder.get(), 'wine': self.wine.get(),
                                             'linuxProfile': self.linux_profile.get(),
                                             'shared': self.shared.get()}, indent=2) + '\n')

    def task(self, description, action, cancellable=False):
        if self.busy:
            return
        self.busy = True
        self.cancel_copy.clear()
        if cancellable:
            self.cancel_button.state(['!disabled'])
        for button in self.buttons:
            button.state(['disabled'])
        self.progress['value'] = 0
        self.status.set(description)
        def worker():
            try:
                self.events.put(('done', action()))
            except client.CopyCancelled as error:
                self.events.put(('cancelled', str(error)))
            except Exception as error:
                self.events.put(('error', str(error)))
        threading.Thread(target=worker, daemon=False).start()

    def prepare(self):
        source, destination = self.source.get(), self.folder.get()
        storage = 'shared' if self.shared.get() else 'copy'
        if not source or not destination:
            messagebox.showerror('Choose folders', 'Choose the normal game folder and a new private-copy location.', parent=self.root)
            return
        def action():
            def progress(done, total):
                if self.cancel_copy.is_set():
                    raise client.CopyCancelled('Copy cancelled. Your normal installation is unchanged.')
                self.events.put(('progress', 100 * done / max(total, 1)))
            client.prepare(source, destination, self.patch, self.checksum,
                           progress, storage=storage)
            return 'Private copy prepared. Choose your server and press Play.'
        self.task('Copying game files. Your normal installation remains untouched.', action, cancellable=True)

    def check(self):
        folder = self.folder.get()
        def action():
            client.verify_private(folder, self.checksum)
            return 'Game version and private patch match this release.'
        self.task('Checking game files…', action)

    def update_patch(self):
        folder = self.folder.get()
        def action():
            client.update_patch(folder, self.patch, self.checksum)
            return 'Private patch matches this launcher. Ready to play.'
        self.task('Updating the private patch and keeping recovery copies…', action)

    def restore_patch(self):
        folder = self.folder.get()
        def action():
            checksum = client.restore_patch(folder)
            if checksum == self.checksum:
                return 'Previous patch restored. Ready to play.'
            return 'Previous patch restored. Use its matching older launcher release to play.'
        self.task('Restoring the previous private patch…', action)

    def play(self):
        folder, url, wine, remember = self.folder.get(), self.url.get(), self.wine.get(), self.remember.get()
        profile = self.linux_profile.get() or None
        try:
            self.save_settings()
        except (OSError, ValueError) as error:
            messagebox.showerror('Cannot save settings', str(error), parent=self.root)
            return
        self.remember.set(False)
        def action():
            result = client.launch(folder, url, self.checksum, wine, remember, profile=profile)
            return f'Game closed (exit code {result}).' if result else 'Game closed. Ready to play again.'
        self.task('Checking files and starting the private game. Keep this launcher open while playing.', action)

    def diagnose(self):
        url = self.url.get()
        def action():
            result = client.check_server(url)
            return f"Login server reachable ({result['regions']} region(s)). The game connection is checked when you enter."
        self.task('Checking login-server connection…', action)

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'progress':
                    self.progress['value'] = value
                else:
                    self.busy = False
                    self.cancel_button.state(['disabled'])
                    for button in self.buttons:
                        button.state(['!disabled'])
                    self.status.set(value)
                    if kind == 'error':
                        messagebox.showerror('Private launcher', value, parent=self.root)
        except queue.Empty:
            pass
        self.poll_id = self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo('Operation running', 'Wait for the current operation to finish. Close the game before exiting the launcher.', parent=self.root)
        else:
            self.root.after_cancel(self.poll_id)
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    smoke = parser.add_mutually_exclusive_group()
    smoke.add_argument('--self-test', type=Path, help='Write a package-integrity report without starting a game')
    smoke.add_argument('--self-test-ui', type=Path, help='Also render the launcher UI; never start a game')
    args = parser.parse_args()
    report = args.self_test or args.self_test_ui
    if report:
        patch, checksum = load_release(bundle_directory())
        client.server_origin('https://play.example.org')
        if args.self_test_ui:
            root = tk.Tk(); root.withdraw()
            app = Launcher(root, patch, checksum, report.parent / 'unused-smoke-settings.json')
            root.update_idletasks(); root.update(); app.close()
        with report.open('x', encoding='utf-8') as stream:
            json.dump({'passed': True, 'gameVersion': client.VERSION, 'patchSha256': checksum,
                       'uiRendered': bool(args.self_test_ui), 'gameStarted': False}, stream, indent=2)
        return 0
    root = tk.Tk()
    root.withdraw()
    try:
        patch, checksum = load_release(bundle_directory())
    except (OSError, ValueError, KeyError) as error:
        messagebox.showerror('Incomplete launcher release', f'{error}\nUse a complete packaged launcher release.', parent=root)
        root.destroy()
        return 1
    Launcher(root, patch, checksum)
    root.deiconify()
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

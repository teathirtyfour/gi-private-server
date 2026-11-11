# Player launcher

This guide takes a new player from downloading the launcher to connecting to a
private server. Ask your operator for a 7.2-compatible server profile and account
before starting.

## Requirements

| Requirement | Windows | Linux |
| --- | --- | --- |
| Launcher architecture | x86-64 | x86-64 |
| Game installation | Matching 7.2.0 installation | Matching 7.2.0 Windows installation |
| Game runner | Windows | Standalone DWProton runner and configured private prefix |
| Python | Bundled in the release | Bundled in the release |
| Shared asset support | File links, with filesystem requirements below | `bubblewrap`, providing `bwrap` |
| Server access | Server profile or URL and private-server account | Server profile or URL and private-server account |

On Linux, follow [Linux setup with DWProton](linux-wine.md) for the pinned
standalone runner, private prefix, Visual C++ runtime, DXVK, and required launch
settings. The launcher does not install those components or a game installation.

For Windows shared storage, use a filesystem supporting hard links, such as
NTFS, and put both game folders on the same volume. Cross-volume sharing uses
file symlinks and requires Windows Developer Mode and symlink support on the
destination filesystem. See [Storage and versions](storage-and-versions.md)
before choosing locations.

## Download and extract

Download a launcher asset from the
[latest release](https://github.com/teathirtyfour/gi-private-server/releases/latest).
GitHub's automatically generated source archives contain source code. Select
the platform asset named below to get the runnable application.

| Platform | Release asset | Application inside the extracted folder |
| --- | --- | --- |
| Windows | `PrivateServerLauncher-windows-x86_64.zip` | `PrivateServerLauncher.exe` |
| Linux | `PrivateServerLauncher-linux-x86_64.tar.gz` | `PrivateServerLauncher` |

On Windows, extract the entire zip with **Extract All**, then open the application
inside the extracted folder. Keep `_internal`, `licenses`, and
`corresponding-source` with the executable. Choose a location you can write to,
such as a folder under your own user account.

On Linux, use your archive manager or extract from a terminal.

```bash
mkdir -p "$HOME/Applications/private-server-launcher"
tar -xzf "$HOME/Downloads/PrivateServerLauncher-linux-x86_64.tar.gz" \
  -C "$HOME/Applications/private-server-launcher"
"$HOME/Applications/private-server-launcher/PrivateServerLauncher/PrivateServerLauncher"
```

The tarball preserves executable permissions. If your file manager asks how to
open the application, choose to execute it. A filesystem mounted with `noexec`
requires moving the extracted application to a location that permits execution.

### Optional checksum verification

Download `SHA256SUMS.txt` from the same release. On Linux, put it beside the
downloaded archive and run the following command. It checks whichever of the
listed platform archives you downloaded.

```bash
cd "$HOME/Downloads"
sha256sum --ignore-missing -c SHA256SUMS.txt
```

On Windows, run the following PowerShell command and compare the displayed hash
with the Windows archive's line in `SHA256SUMS.txt`.

```powershell
Get-FileHash "$HOME\Downloads\PrivateServerLauncher-windows-x86_64.zip" -Algorithm SHA256
```

## Configure the launcher

### 1. Select the server

Press **Import profile** and select the operator's `player-server.json`. The
launcher reads its URL and checks that its game version matches this release.
You can also enter the operator's URL in **Server address**.

A public server address usually looks like `https://play.example.org`. Include
the scheme. The address must be an origin with an optional port and no extra
path, query, or fragment. For example, enter `https://play.example.org`, without
appending `/query_region_list` or a website path.

The initial `http://127.0.0.1:8088` value points to the computer running your
launcher. To join a friend on another computer, import their profile or enter
their address.

### 2. Choose the normal game folder

In **Normal game folder**, select the directory containing `GenshinImpact.exe`
and `GenshinImpact_Data`. This is the installed game's directory. A parent
directory containing only a game manager or installer is insufficient.

The launcher verifies specific executable and metadata hashes for its supported
7.2.0 build. A different version or build can fail verification even when its
folder name looks correct. Close the game and any updater before preparation.

### 3. Choose a private game folder

In **Private game folder**, select a new or empty destination. For example,
Windows users might choose `D:\Games\GenshinPrivate`, and Linux users might choose
`/home/alex/Games/GenshinPrivate`.

Keep the source and destination separate. Neither may contain the other. The
launcher refuses a destination containing existing files. Preparation creates
private writable files, patch records, and login state there.

### 4. Choose storage mode

**Share installed assets to save space** is selected by default. It reuses the
large `StreamingAssets` tree while copying other files into the private folder.
The source game installation must remain available and at version 7.2.0 while
you use that shared private client.

Clear the checkbox before preparation to create a full independent copy. This
requires space for the copied installation. Changing the checkbox after a client
has been prepared does not convert its existing storage layout. To change modes,
prepare a new empty destination with the desired setting.

Preparation checks available disk space. Shared mode still copies files outside
the shared asset tree, so it needs additional space. See
[Storage and versions](storage-and-versions.md) for the layout and update behavior.

### 5. Select a Linux runner

On Linux, browse **Wine executable** to the standalone DWProton `bin/wine`
installed by the [Linux setup guide](linux-wine.md). Use its full absolute path
so the selection consistently identifies the intended runner.

The ordinary launcher path uses the Wine prefix under
`<private game folder>/.private-launcher/wine-prefix`. After preparing your game
folder, initialize that prefix and install its runtimes using the Linux guide
before the first **Play**. The launcher does not automatically migrate a Wine
prefix from another game manager.

**Wine/performance profile** is an optional advanced import for a previously
prepared private environment. It expects a project-specific profile with a
matching private workspace, prefix, Wine runner, and optional performance tools.
Use it when your operator has prepared that environment and supplied its profile.
For ordinary setup, use **Wine executable** and leave the advanced profile unset.

## Prepare and connect

1. Press **Prepare private copy**. Keep the window open while progress advances.
   Preparation can take time because it verifies and copies files.
2. Wait for the prepared message. **Cancel copy** can cancel preparation before
   the completed private folder is published.
3. On Linux, complete the [private Wine setup](linux-wine.md#4-initialize-the-launchers-private-prefix)
   and reopen the launcher with its documented compatibility settings.
4. Press **Check files**. A successful result confirms that the supported game
   files and installed private patch match the launcher.
5. Press **Test connection**. A successful result confirms that login dispatch is
   reachable and advertises an acceptable login address.
6. Press **Play**. Sign in with the username and password your private-server
   operator supplied.
7. Keep the launcher open until you exit the game.

The account belongs to the private server. Account registration and password
resets are handled by its operator. If login succeeds but loading the world
fails, follow the UDP connection checks in [Troubleshooting](troubleshooting.md).

## Daily use and login behavior

After initial preparation, open the extracted launcher, confirm **Server address**
and **Private game folder**, and press **Play**. Preparation is a first-time step.
The launcher saves its server, private folder, Wine runner, advanced profile, and
storage checkbox settings when you press **Play**.

By default, a launch moves the private HoYoPass login cache into a timestamped
backup under `.private-launcher/login-backups`. This lets the game present its
login flow again. **Remember private login for this launch** skips that reset
once. The checkbox clears when you press **Play**, so later launches use the
default behavior again.

To change server, close the game, import the other operator's profile or change
**Server address**, and launch again. You need an account on the selected server.
Progress is stored by each server.

## Updating the launcher and patch

1. Close the game and launcher.
2. Download and extract the new launcher release to a separate application folder.
3. Open it and select the existing **Private game folder**. Saved settings may
   already point there.
4. Read that release's supported game version. For a compatible existing client,
   press **Update private patch** to install its bundled patch and keep recovery
   copies.
5. Run **Check files** and **Test connection**, then **Play**.

If a patch update fails or you need the previous patch, use **Restore previous
patch**. When the restored patch belongs to an older launcher, use the matching
older launcher release. A launcher checks the patch against its own release.

Patch updates replace the private client patch. They do not download a new game
version. A game-version change requires a matching game installation and launcher.

## Where settings are stored

| Data | Location |
| --- | --- |
| Windows launcher settings | `%LOCALAPPDATA%\PrivateServerLauncher\settings.json` |
| Linux launcher settings | `$XDG_STATE_HOME/PrivateServerLauncher/settings.json`, defaulting to `~/.local/state/PrivateServerLauncher/settings.json` |
| Per-client patch and preparation records | `<private game folder>/.private-launcher/` |
| Ordinary Linux private Wine prefix | `<private game folder>/.private-launcher/wine-prefix/` |
| Quarantined private login caches | `<private game folder>/.private-launcher/login-backups/` |

Retain `.private-launcher` when using, moving, or backing up a prepared private
client. It contains the records needed for verification and patch recovery.

# Troubleshooting

Start with the section matching the stage that failed. Record the exact error
text and the launcher or server version before changing files.

## Launcher installation

### The application is missing or the download contains only source

Download `PrivateServerLauncher-windows-x86_64.zip` or
`PrivateServerLauncher-linux-x86_64.tar.gz` from the release assets. GitHub also
offers automatic source archives, which contain source code. Follow the
[download steps](launcher.md#download-and-extract).

### Missing bundled patch or an incomplete release error

Extract the entire platform archive again into a new folder. Keep the executable,
`_internal`, `licenses`, and `corresponding-source` together. Compare the downloaded
archive against the release's `SHA256SUMS.txt` if corruption is suspected.

An installed patch or bundled patch checksum failure means the checked bytes
differ from the release record. Use a fresh release archive. An antivirus or
other tool may also have removed a file. Inspect that tool's reported action
before deciding how to resolve it.

### Linux reports permission denied when opening the executable

Check the executable's permissions and the mount options of its location. The
Linux tarball preserves executable permissions. If your extraction tool removed
them, restore the bit on the launcher in the extracted application directory.

```bash
chmod u+x ./PrivateServerLauncher
./PrivateServerLauncher
```

A `noexec` mount still blocks execution. Move the application to a location that
permits execution. Run it from a terminal to capture the exact loader error if
it exits immediately.

## Preparing game files

### Unsupported or changed client file

Confirm **Normal game folder** contains the actual `GenshinImpact.exe` and
`GenshinImpact_Data`, and confirm the game matches the launcher's verified 7.2.0
build. A 7.3 installation cannot be prepared by this 7.2 launcher. Another 7.2
build may also differ in the checked files.

Close official updaters and the game, then retry with complete matching inputs.
If you previously shared assets and updated the source, restore matching assets
or supply another matching installation. See [Storage and versions](storage-and-versions.md).

### Destination contains files or overlaps the source

Choose a new or empty **Private game folder** outside the normal installation.
The source cannot contain the destination, and the destination cannot contain
the source. Keep an existing prepared client intact while choosing another
destination for a new preparation.

### Source contains a link or shared mode requires independent assets

Preparation refuses redirected source files and asset directories. Some existing
game-manager layouts use symlinks. Select an independent installation containing
the actual files, and verify the path you selected. The generic preparation
workflow does not automatically adopt every pre-existing linked client layout.

### Insufficient space

Check free space on the destination volume. Shared mode still copies everything
outside `StreamingAssets`, plus private patch and state files. Full-copy mode
needs room for the copied installation. Use the amount in the preparation error
to choose a destination with enough available storage.

### Windows cannot create shared file links

Place source and destination on the same hard-link-capable volume, typically
NTFS. For cross-volume file symlinks, enable Windows Developer Mode and use
filesystems that support them. If linking remains unavailable, clear sharing
and prepare a full copy in a new empty destination.

### Windows cannot protect shared assets

Close the official game, official updater, and other programs modifying the
source game tree. The read guards need to open the source assets and private
file links with compatible sharing permissions. Retry after those handles are
released. Check that both the source and destination remain accessible.

### Linux requires bubblewrap, or `bwrap` reports permission denied

Install your distribution's bubblewrap package and check that the `bwrap`
executable is available. This small command tests basic namespace access and
reads the filesystem through a read-only bind.

```bash
command -v bwrap
bwrap --ro-bind / / -- /usr/bin/true
```

If the second command reports a user-namespace, UID-map, or policy error, the
host's sandbox policy is blocking that operation. Have the host administrator
check the distribution's namespace policy. Ordinary full-copy mode avoids the
shared-assets sandbox requirement. Prepare a new independent copy with sharing
cleared. An imported advanced Wine profile may still use bubblewrap itself.

## Login and world connection

### Import profile fails

Confirm the file is the operator's `player-server.json` for game version 7.2.0.
It needs a valid `url` origin and matching `gameVersion`. Ask the operator to
resend the generated profile if it was modified or belongs to another release.

### Test connection cannot reach the server

1. Verify **Server address**, including `https://` for a generated public server.
2. If it still says `http://127.0.0.1:8088`, import the remote operator's profile.
   That default points to your own computer.
3. Ask the operator to check DNS, certificate provisioning, TCP 80/443, and proxy
   status.
4. Compare results from another network if a local router may lack NAT loopback.
5. Capture the exact error, including any certificate or HTTP failure.

A normal browser page at the hostname may differ from the launcher's dispatch
request. Use **Test connection** to check the route the launcher expects.

### Server advertises a different login address

The launcher compares the returned login origins with the selected address.
Ask the operator to check the generated `config.json` access address and routing
settings against the profile's URL. Use the hostname for which the deployment
was prepared. An IP-based URL or unrelated hostname can differ from the server's
advertised HTTPS origin.

### Login succeeds but the world does not load

The operator should check the following in order.

1. `python3 manage.py status` reports a running game and a healthy tick.
2. The public UDP game port is allowed by both host and provider firewalls.
3. Home-router forwarding maps the advertised UDP port to the server host.
4. The advertised `gameHost` and `gamePort` in `deployment.json` match the intended
   reachable address and the configuration supplied to players.
5. The supplied JAR, prepared resources, client build, and launcher patch support
   the same protocol and game version.
6. Game logs show what happened during the player's connection attempt.

HTTPS-only forwarding covers login. Gameplay requires the UDP route too.
**Test connection** reports login-dispatch reachability and does not test world
entry or guarantee that quests and combat work.

### Invalid account or password

Use the account supplied by this private-server operator. The generated server
has automatic account creation disabled. The operator should verify account
creation in the server console and reset the password when necessary. Instructions
are in [Server operations](server-operations.md#accounts).

### Game skips login and starts loading immediately

Close the game, clear **Remember private login for this launch**, and press
**Play** again through the launcher. The default launch quarantines the private
HoYoPass login cache before starting. Directly opening the game executable
bypasses that launcher step.

If it still happens, record the selected private folder and server URL and ask
the operator to inspect the launcher's reported error or private preparation
records. A redirected or shared login cache is rejected by the launcher.

## Patch and Linux runner recovery

### Installed private patch differs from the launcher

Close the game. Confirm the launcher release supports the installed game version,
then press **Update private patch**. Run **Check files** afterward. If recovering
an interrupted update or reverting a bad one, use **Restore previous patch** and
follow its result message about the matching older launcher.

### Client is already running or being updated

Close the active game or wait for the other launcher's file operation. The client
lock prevents patch updates from racing a running game. Retry after that process
has finished. Retain the client's `.private-launcher` records while investigating.

### Wine executable was not found

Set **Wine executable** to the standalone DWProton binary installed by the
[Linux setup guide](linux-wine.md). Check that exact path in a terminal.

```bash
"$HOME/.local/share/private-server-runners/wine-dwproton-wow64-11.0-11/bin/wine" --version
```

Browse to that executable in the launcher. A working game-manager installation
can depend on a configured prefix, graphics libraries, or environment that a
fresh private prefix has not inherited. Follow the private-prefix, Visual C++,
DXVK, and launch-environment steps in the Linux guide, or use an operator-supplied
advanced profile prepared for this private client.

### Advanced Wine/performance profile fails

Confirm the JSON is the profile intended for this private workspace. The profile
checks its prefix directory and its C: and G: mappings. Its Wine executable,
optional GameMode, overlay configuration, and optional FPS unlocker must also
exist as recorded. Moving the workspace can invalidate those paths. Ask whoever
prepared the environment to update it using the actual private locations.

## Server setup and maintenance

### Setup rejects a domain or IP

`--domain` requires a public DNS or DDNS hostname without scheme, path, or port.
Raw IP HTTPS deployment is unsupported by this generator. A raw IPv4 game
address can be supplied separately using `--game-host`.

### Setup rejects the JAR

Supply a valid compatible Java JAR with strict authentication support. Setup
checks for `StrictPasswordAuthenticator.class`. Ask the server maintainer for
the build intended for these tools. Renaming another file to `.jar` does not
change its format or authentication support.

### Resource preparation fails or the download is interrupted

Read the reported failing input or generated file. Keep the pinned recipe and
use the same download cache when retrying. A checksum failure indicates the
generated output differs from the recipe. Record the tool revision and error
for the maintainer. Choose a fresh deployment destination on retry because
setup refuses to overwrite an existing directory.

### Start fails or times out

From the generated deployment directory, inspect the service state and logs.

```bash
python3 manage.py status
python3 manage.py logs
docker compose logs --tail 100 database
docker compose logs --tail 100 proxy
```

Use `podman compose` for the last two commands when operating with Podman. Check
the engine, image-download failures, available memory and disk, port conflicts,
resource compatibility, and the supplied JAR's internal readiness endpoint.
The management readiness check expects a progressing game tick.

### Public `/api/status` returns 404

The generated Caddy configuration blocks management routes from public access.
Use `python3 manage.py status` on the server host to inspect internal readiness.

### Another management operation is running

Detach the console or wait for the existing backup, restore, upgrade, or other
management command. Each deployment has an operation lock. If a process failed,
check whether it is still running and inspect logs before retrying recovery.

### JAR differs from the recorded build

Management compares `server.jar` with `deployment.json`. Use the documented
`upgrade` command for build changes. For an interrupted upgrade, inspect the
corresponding `build-history` receipt and use its valid rollback snapshot. Keep
the recovery files while diagnosing the mismatch.

### Restore or rollback fails

Retain the selected archive, safety backup, history directory, and exact error.
The tools can leave game and proxy services stopped after partial restoration
to prevent players from entering inconsistent state. Verify database availability,
archive hashes, selected JAR, and history ownership before proceeding with
recovery. [Server operations](server-operations.md) explains what each command
restores and where safety backups are stored.

## Reporting a problem

Include the following information so the maintainer can reproduce the stage
that failed.

- Launcher release and game version.
- Windows or Linux, including the Wine runner and version on Linux.
- Shared or full-copy storage and whether source/destination use different volumes.
- The button or server command used and its exact error text.
- Whether **Check files**, **Test connection**, account login, and world entry
  each succeed.
- For operators, the Java build hash, tool revision, and relevant service logs
  around the failed attempt.

Remove passwords, database connection strings, authentication tokens, and private
account data from public issue reports. The generated `.env`, `config.json`,
`mongo-tools.yml`, and progress backups contain sensitive operator or player data.

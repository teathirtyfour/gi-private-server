# Documentation

These guides cover the Windows and Linux launcher for game version **7.2.0** and
the Linux deployment tools in this repository.

## Choose a guide

| Task | Guide |
| --- | --- |
| Download the launcher, prepare your game, and connect | [Player launcher](launcher.md) |
| Install DWProton, Visual C++, and DXVK in a private Linux prefix | [Linux Wine setup](linux-wine.md) |
| Prepare a Linux host and start a public server | [Server setup](server-setup.md) |
| Create accounts, inspect logs, back up, restore, and update | [Server operations](server-operations.md) |
| Choose shared storage or full copy and preserve game 7.2 | [Storage and versions](storage-and-versions.md) |
| Diagnose installation, connection, or recovery failures | [Troubleshooting](troubleshooting.md) |
| Compile the patch or build launcher executables | [Launcher build guide](../distribution/launcher-build.md) |

## What you need

Players download a complete launcher archive and supply a matching 7.2.0 game
installation. The server operator supplies a server address or `player-server.json`
and a private-server account. The launcher bundles its Python runtime and client
patch. A player can use the release without installing Python or compiling code.

Server operators need a Linux host, Python 3.11 or newer, Git, and Docker with
Compose or Podman with a Compose provider. Operators also supply a compatible
7.2 server JAR and either prepared server resources or inputs obtained through
the optional pinned resource downloader. The public repository and launcher
release contain the launcher, patch source, and deployment tools. Obtain the
Java server JAR separately from whoever maintains your server implementation.

The public deployment generator requires a DNS or DDNS hostname for HTTPS login.
It accepts a separate hostname or IPv4 address for the UDP game connection.
[Server setup](server-setup.md) explains the required network configuration.

## First-time paths

### Joining a friend's server

1. Get the server profile and account details from your operator.
2. Download the appropriate archive from the
   [latest release](https://github.com/teathirtyfour/gi-private-server/releases/latest).
3. Follow [Player launcher](launcher.md), including the platform requirements.
4. Use **Check files**, **Test connection**, and then **Play**.

### Hosting a server

1. Obtain a compatible server JAR and prepare your host and hostname.
2. Follow [Server setup](server-setup.md) to generate and start a deployment.
3. Create player accounts through the server console.
4. Give each player their account and the generated `player-server.json`.
5. Use [Server operations](server-operations.md) for ongoing administration.

## Supported scope

Release `launcher-v0.1.0` targets x86-64 Windows and Linux and game version 7.2.0.
Its native Windows and Linux test suites, patch build, and frozen GUI checks passed.
The published release links to its exact build results.

**Test connection** verifies login dispatch and the advertised login address.
Entering the world also requires a reachable UDP game service, valid account, and
compatible Java server. Quest, combat, and other gameplay behavior depend on that
server implementation. Launcher verification covers launcher behavior and file
handling. Full gameplay acceptance still requires playing with the chosen server.

## Reading the examples

Shell commands in the server guides run on the Linux server host. PowerShell
commands are labeled for Windows. Replace example domains, paths, and account
passwords with your own values. `play.example.org` is an example hostname.

The guides use `~/gi-private-server` for the source checkout and
`~/private-server-deployment` for generated deployment files. Run management
commands from the deployment directory unless a command includes `--deployment`.

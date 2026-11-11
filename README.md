# Launcher and deployment tools for 7.2.0

An unofficial, independently maintained launcher for compatible Genshin Impact
7.2.0 installations, with Linux deployment tools for community-run private
servers. The launcher applies a client patch to a separate private installation.

This project is unaffiliated with HoYoverse and COGNOSPHERE. Game and company
names identify compatibility. The repository contains launcher and patch source,
deployment tools, and third-party notices. Players supply the game installation.
Operators supply a compatible server JAR and prepared resources. Game files,
artwork, audio, and the server JAR are outside this distribution.

## Play

Download the launcher for your platform, open it, and import the operator's
`player-server.json`. Select the folder containing `GenshinImpact.exe` and an
empty folder for your private client. Prepare the client, test the connection,
and press **Play**. Sign in with the account your operator supplies.

Shared storage reuses the installed assets. Keep the source installation at
7.2.0 while sharing it. Choose full copy to keep an independent installation.
Linux requires Wine and bubblewrap for shared mode. Windows uses file links on
the same volume and requires Developer Mode for links across volumes. Keep the
launcher open while playing.

## Deploy

Use Python 3.11 or newer, Git, Docker with Compose or Podman with a Compose
provider, and a DNS or DDNS hostname. Allow TCP 80/443 and UDP 22101. Supply a
7.2 server JAR with strict authentication and a prepared resource directory you
have permission to use.

```bash
python3 distribution/server/setup.py --domain play.example.org --jar /path/to/server.jar --resources /path/to/prepared-resources --output ../deployment
cd ../deployment
python3 manage.py start
python3 manage.py console
```

Setup prepares a local snapshot from the supplied resources. Review the source
and its terms before using the optional resource downloader. The proxy handles
HTTPS. MongoDB and management endpoints stay private. The console
accepts `account create friend --password=CHOOSE_A_UNIQUE_PASSWORD`. Give players
their account and `player-server.json`. Run `python3 manage.py --help` for backup,
restore, upgrade, and rollback commands.

## Build the launcher

Build both packages with GitHub Actions or follow the local commands for the
patch, dependency archive, and platform package in the
[launcher build guide](distribution/launcher-build.md).

Launcher checks cover file preparation, UI behavior, and login dispatch. Gameplay
compatibility depends on the server build.

## Licenses and rights

See the project license and component notices for source attribution and license
terms. Third-party materials remain subject to their own terms. [Project license](LICENSE)

Review the game terms that apply to your installation before using or sharing a
patched client or running a server. [HoYoPlay Terms of Service](https://fastcdn.hoyoverse.com/static-resource-v2/2025/08/22/2f9bf59de8378a2c58833b4b21c799ed_699432398795830104.pdf)

For copyright or trademark concerns, contact the repository owner with the
relevant URL or file path so it can be reviewed.

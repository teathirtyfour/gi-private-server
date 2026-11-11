# Server setup

This guide generates a Linux deployment containing a Java game service, MongoDB,
and a Caddy HTTPS proxy. The setup tool writes deployment files and resources.
You start the services explicitly after preparation.

## Required inputs

| Input | What to prepare |
| --- | --- |
| Linux host | Persistent storage and permission to run your container engine |
| Python | Python 3.11 or newer on the host |
| Git | Used to obtain this repository and by the resource downloader |
| Container engine | Docker with Compose, or Podman with a working Compose provider |
| Public hostname | A DNS or DDNS name resolving to your reachable host |
| Game server | A compatible 7.2 Java server JAR with strict password authentication |
| Server resources | A prepared resource directory or the optional pinned downloader |
| Network | Inbound TCP 80 and 443, and UDP 22101 by default |

The generated Java container runs Java 21 with a 3 GiB maximum heap. MongoDB,
the proxy, the host, and filesystem cache also use memory. Size the host above
the Java heap and monitor actual usage with your player count. The repository
does not establish a guaranteed capacity or minimum RAM figure for gameplay.

Disk use includes the JAR, container images, downloaded resource inputs and
cache, the prepared deployment resources, database volumes, and backups. Existing
resource directories are copied into the deployment. Choose locations with
enough room for those copies.

### Obtain the server JAR

The public repository supplies launcher and deployment tools. Obtain a compatible
Java server build separately from your server maintainer. The setup tool checks
that the JAR contains
`emu/grasscutter/auth/StrictPasswordAuthenticator.class`. Passing this check
identifies authentication support. Compatibility with the protocol, resource
recipe, and management endpoints still depends on the supplied server build.

The management tool expects the game's internal `/api/status` response to contain
a healthy game-tick record. A different implementation may require integration
changes even if it passes the JAR check. Ask the server maintainer for the JAR
and prepared resources intended for these deployment tools.

## Configure DNS and networking

### DNS or DDNS

Choose a hostname such as `play.example.org` or a hostname from your DDNS
provider. Point its A record at the public IPv4 address of the server. Publish
an AAAA record only when the host is also reachable through that IPv6 address.
For a changing residential address, keep your DDNS updater running so the
hostname follows the current address.

The `--domain` argument takes the hostname alone, without `https://`, a path, or
a port. Caddy serves HTTPS for that hostname. The current generator requires a
DNS or DDNS hostname for HTTPS login and rejects a raw IP as `--domain`.

Use `--game-host` when the UDP address differs from the HTTPS hostname. It accepts
a hostname or IPv4 address. Omitting it advertises the same hostname for both
login and gameplay. The present hostname validator does not accept IPv6 literals
as `--game-host`.

### Ports and firewalls

| Port | Protocol | Purpose | Published by the generated deployment |
| --- | --- | --- | --- |
| 80 | TCP | HTTP and certificate provisioning/redirects | Caddy proxy |
| 443 | TCP | HTTPS login and dispatch | Caddy proxy |
| 22101 | UDP | Game connection | Java game service |
| 8088 | TCP | Internal HTTP service | Container network |
| 27017 | TCP | Internal MongoDB | Container network |

Allow the public ports in the host firewall and any hosting-provider firewall.
On a home network, forward them from the router to the server machine's LAN
address. The default game-port forwarding must preserve UDP 22101. If you select
another port, allow and forward that port instead.

An HTTP-only reverse proxy handles the HTTPS connection. Players must also reach
the UDP game port. A successful website or login test therefore covers only part
of the route to the game.

Hosting behind carrier-grade NAT requires a separately provided inbound route
for these ports. A DDNS hostname tracks an address but does not create inbound
reachability. Test from another network when confirming a public deployment.
Some home routers also need NAT loopback to let local players use the public
hostname from inside the same network.

If the host already uses ports 80 or 443, resolve that conflict before starting
the generated proxy. Rootless container engines also need host support for
publishing these low-numbered ports. Verify your engine's networking configuration
before choosing it for the deployment.

## Obtain and inspect the tools

Run these commands on the Linux host. The examples use your home directory.

```bash
cd "$HOME"
git clone --branch production --single-branch \
  https://github.com/teathirtyfour/gi-private-server.git
cd "$HOME/gi-private-server"
python3 --version
git --version
python3 distribution/server/setup.py --help
```

For Docker, check the installed engine and Compose plugin.

```bash
docker version
docker compose version
```

For Podman, check its engine and Compose provider.

```bash
podman version
podman compose version
```

Use an engine the current Linux user can operate. Docker is selected by default
when its executable is available, otherwise management uses Podman. You can
explicitly select an engine in each management command.

## Generate a deployment

Choose a new deployment directory outside the source checkout. The examples use
`$HOME/private-server-deployment`. Setup refuses an existing output directory.
For another attempt, inspect the earlier output and choose a new destination.

### Option A — use prepared resources

Use this path when your server maintainer supplies a compatible prepared resource
directory. Replace the JAR and resource paths with their actual locations.

```bash
cd "$HOME/gi-private-server"
python3 distribution/server/setup.py \
  --domain play.example.org \
  --jar /path/to/server.jar \
  --resources /path/to/prepared-resources \
  --output "$HOME/private-server-deployment"
```

The resource directory must contain `ExcelBinOutput`, `BinOutput`, `Scripts`,
and `TextMap`. Setup copies resources into the deployment and materializes
symlinks. It excludes Git and cache directories. The supplied resources must
match the chosen server and game version.

### Option B — download resources during setup

The optional downloader obtains the repositories pinned in
`distribution/server/resources-recipe.json`, prepares the resource layout, and
verifies generated outputs. Use inputs you have permission to obtain and use.
The server JAR is still supplied separately.

```bash
cd "$HOME/gi-private-server"
python3 distribution/server/setup.py \
  --domain play.example.org \
  --jar /path/to/server.jar \
  --download-resources \
  --resource-cache "$HOME/private-server-resource-cache" \
  --output "$HOME/private-server-deployment"
```

This downloads resources during initial provisioning. It happens before the
deployment is published and before services start. The running game service does
not download missing resources on demand. First provisioning can take several
minutes or longer depending on the resource inputs, connection, and storage.

Retain the resource cache to reuse downloaded inputs for another preparation.
With `--download-resources`, omitting `--resource-cache` uses `.resource-downloads`
beside the deployment output. A directory chosen as the cache must be empty or
already owned by this downloader. An interrupted fetch can be retried using the
same cache and a fresh output destination.

### Separate game address or port

For HTTPS on a DDNS hostname and gameplay on a separate IPv4 address, add the
following options to either setup command.

```text
--game-host 203.0.113.10 --game-port 22101
```

`203.0.113.10` is an example address. Use the reachable address of your game host.
The chosen game port must be between 1024 and 65535. The generator uses it for
both the published UDP port and advertised client port.

## Inspect the generated files

| File or directory | Purpose |
| --- | --- |
| `server.jar` | Copied Java server build |
| `resources/` | Deployment-local server resources |
| `compose.yaml` | Services, ports, volumes, and stable project identity |
| `Dockerfile` | Java container image definition |
| `config.json` | Server addresses, database connection, and authentication settings |
| `.env` | Generated database credentials and selected game port |
| `init-db.js` | Initializes the application's database user |
| `mongo-tools.yml` | Credentials used by database backup and restore tools |
| `Caddyfile` | HTTPS proxy and blocked public management routes |
| `deployment.json` | Server hash, project name, and selected host settings |
| `manage.py` | Lifecycle, maintenance, and recovery commands |
| `player-server.json` | Public profile to give players |

Setup generates unique database credentials, disables automatic account creation,
enables strict password checks, and gives new accounts no default administrative
permissions. The deployment directory is owner-only. Keep credentials,
configuration, and backups under the operator's control. `player-server.json`
contains the public server URL and game version and is intended for players.

The deployment has a stable Compose project name. Retain it when operating or
recovering that deployment so management locates the same containers and volumes.

## Start and check the server

```bash
cd "$HOME/private-server-deployment"
python3 manage.py start
python3 manage.py status
python3 manage.py logs
```

The first start pulls pinned database, proxy, and Java base images and builds
the game image. It starts detached containers. A successful `start` reports the
server ready after checking its internal HTTP status and game tick. That check
does not establish external DNS, HTTPS certificate, firewall, or UDP reachability.

The generated containers use `restart: unless-stopped`. Keep your container
engine available after reboot according to its installation method. A rootless
Podman setup may need user-service configuration for unattended operation.

To explicitly select Podman, put `--engine podman` before the management action.

```bash
python3 manage.py --engine podman start
python3 manage.py --engine podman status
```

## Create accounts and invite players

Open the console from the deployment directory.

```bash
python3 manage.py console
```

For the matching server implementation, enter the following at the **server
console**, replacing the username and password. This is a game-server command.

```text
account create friend --password=CHOOSE_A_UNIQUE_PASSWORD
```

Wait for the account-created response. Repeat for each player with a distinct
account. Automatic account registration is disabled in the generated setup.
For other compatible JAR implementations, confirm their account command syntax
with the server maintainer.

The console attaches to the running game container. With Docker's default attach
keys, press **Ctrl+P**, release, then **Ctrl+Q** to detach and leave it running.
Podman users should use their configured detach sequence. Use `manage.py stop`
when you intend to stop the deployment.

Give each player the following information.

1. The [launcher download](https://github.com/teathirtyfour/gi-private-server/releases/latest)
   and [player guide](launcher.md).
2. The generated `player-server.json`.
3. Their private-server username and password.
4. Confirmation that their installation must match game version 7.2.0.

Use **Test connection** from a player machine, then actually enter the world to
verify UDP reachability and server compatibility. Once this works, create the
first backup and follow [Server operations](server-operations.md).

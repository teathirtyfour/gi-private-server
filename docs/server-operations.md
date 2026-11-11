# Server operations

These commands manage a deployment produced by `distribution/server/setup.py`.
Run them on the Linux host from the generated deployment directory.

```bash
cd "$HOME/private-server-deployment"
python3 manage.py --help
```

Use the same container engine and Linux user that created and started the
deployment. Put global options before the action. For example, use
`python3 manage.py --engine podman status` to select Podman explicitly. From
another directory, the equivalent form is the following.

```bash
python3 "$HOME/private-server-deployment/manage.py" \
  --deployment "$HOME/private-server-deployment" status
```

## Start, inspect, and stop

| Command | Behavior |
| --- | --- |
| `python3 manage.py start` | Verifies the recorded JAR hash, builds/starts services, and waits for a healthy game tick |
| `python3 manage.py status` | Prints Compose service state and internal readiness data when the game is running |
| `python3 manage.py logs` | Prints the last 100 lines of game-service Compose logs |
| `python3 manage.py console` | Attaches to the game console |
| `python3 manage.py stop` | Stops services and retains saved volumes |

To restart normally, stop and start the deployment.

```bash
python3 manage.py stop
python3 manage.py start
```

`start` performs a readiness check with a timeout. If it fails, inspect status
and logs before retrying. The containers may have been created even when
readiness failed. Starting an existing deployment reuses its generated project
identity and saved volumes.

### Additional service logs

For Docker, inspect the proxy or database with Compose from the deployment
directory. Use the analogous `podman compose` commands for Podman.

```bash
docker compose logs --tail 100 proxy
docker compose logs --tail 100 database
docker compose ps
```

The proxy logs help diagnose HTTPS and certificate problems. Database logs help
diagnose authentication or startup failures. Management endpoints are restricted
to the container network. The public proxy deliberately blocks paths such as
`/api/status`, `/console`, and `/health`.

## Accounts

Open the console with `python3 manage.py console`. The commands below describe
the server implementation intended for this toolset. A separately supplied JAR
may expose different console commands.

### Create an account

```text
account create friend --password=CHOOSE_A_UNIQUE_PASSWORD
```

Wait for confirmation before giving the player their account. The generated
configuration has automatic account creation disabled and strict password checks
enabled. New accounts receive the configured default permissions, which are
empty in the generated deployment.

### Reset a password

For the matching implementation, the UID selector is explicit. Substitute the
account's actual numeric UID after `@`.

```text
account resetpassword @10001 CHOOSE_A_NEW_UNIQUE_PASSWORD
```

That implementation revokes existing login tokens and disconnects a currently
connected player when the password changes. The player then signs in again.
Verify the target account's UID before running the command.

### Detach from the console

With Docker's default attach keys, press **Ctrl+P**, release, then **Ctrl+Q**.
For Podman, use the configured detach keys. Keep the console session separate
from scheduled maintenance. The management tool holds its operation lock while
attached, so detach before running another maintenance command.

## Saved data and deployment files

| Storage | Contents or purpose |
| --- | --- |
| Compose `database` volume | MongoDB account and player records |
| Compose `data` volume | Server-managed persistent files |
| Compose `cache` volume | Server cache |
| Compose `logs` volume | Server log files |
| Compose `certificates` and `caddy_config` volumes | Proxy certificate and proxy state |
| Deployment directory | Selected JAR, resource copy, configuration, credentials, and management script |
| `build-history/` | Saved JARs, configuration descriptors, progress snapshots, and build-change receipts |

Container recreation preserves named volumes. Volume deletion removes their
contents. Keep the generated Compose project name and use the management tool
for ordinary maintenance. A Git checkout of the public source or a copy of the
server JAR does not contain the accounts and saved progress in those volumes.

## Back up accounts and progress

Create backups while the database is running and the deployment's containers
exist. Each backup needs a new filename. The tool refuses to overwrite one.

```bash
python3 manage.py backup "$HOME/private-server-backups/before-maintenance.tar.gz"
```

The backup operation stops the game and proxy, verifies the deployment's volume
layout, exports the application database, copies the persistent `data` volume,
and records configuration and hashes. It restarts services that had been running
after a successful snapshot. Players experience downtime during this process.

If the game and proxy were already stopped but the database remained running,
they remain stopped afterward. A fully stopped database must be started before
backup. A normal `manage.py start` starts the complete deployment.

The archive contains the following components.

| Archive member | Contents |
| --- | --- |
| `backup.json` | Server-build hash, creation time, and file hashes |
| `database.archive.gz` | Application database dump |
| `data/` | Persistent server files |
| `configuration/` | Snapshot of configuration, deployment metadata, and database credentials |

Treat backups as sensitive because they include account data and deployment
credentials. Copy verified backups to storage outside the server host for host
failure recovery. Retain the matching server JAR and resource version alongside
your recovery records. The progress archive does not bundle the JAR, full
resource directory, or all container volumes.

### Inspect an archive

You can list its members without restoring it.

```bash
tar -tzf "$HOME/private-server-backups/before-maintenance.tar.gz"
```

The restore tool verifies the archive's file hashes before replacing progress.
Listing an archive establishes its contents, while a tested restore into an
isolated compatible deployment establishes recovery behavior for your setup.

## Restore a progress backup

Restoring replaces the current application's database and persistent server data
with the snapshot. Progress made after the snapshot is lost from the active
deployment. Inform players and select the intended archive before running it.

```bash
python3 manage.py restore \
  "$HOME/private-server-backups/before-maintenance.tar.gz" --replace
```

The database must be running. The tool verifies the archive, creates a safety
backup under the deployment's `backups/`, stops the game and proxy, and restores
the database and `data` files. It restarts previously running services after a
successful restore. A partially failed restore leaves game access stopped for
inspection and recovery.

The normal restore expects a snapshot from the selected server-build hash.
`--allow-build-change` permits a different hash, but it does not convert database
schemas or select a matching JAR. Use it only when the server maintainer has
confirmed that the current build can read that snapshot. A typical recovery
uses the matching build or the rollback mechanism below.

The archive's `configuration/` files are retained for reference. This restore
operation restores application data and keeps the deployment's active
configuration. Changes to hostnames, secrets, container networking, or resource
inputs need their own reviewed configuration recovery.

## Upgrade the Java server

An upgrade selects another compatible JAR. It keeps a recovery copy of the
current JAR and a progress snapshot before installing the new build.

1. Obtain the intended JAR and its checksum from your server maintainer.
2. Confirm it supports the current resource inputs and game version.
3. Create a separate backup you can retain outside the deployment.
4. Schedule player downtime.
5. Run the upgrade and record the printed recovery directory.

You can calculate the local file hash on the host.

```bash
sha256sum /path/to/new-server.jar
```

Pass the full expected hash to the management command.

```bash
python3 manage.py upgrade /path/to/new-server.jar \
  --sha256 REPLACE_WITH_THE_FULL_64_CHARACTER_SHA256
```

The tool verifies the hash and strict authentication support, preserves recovery
files in `build-history/`, takes a progress snapshot, installs the build, rebuilds
the game container, and checks readiness. A successful result prints the
recovery directory. A failed change leaves game and proxy services stopped and
records the state in that directory's `change.json`.

An upgrade command replaces the JAR. It does not update resources, the launcher,
the game installation, or the protocol recipe. Coordinate those separately with
the server maintainer. Avoid manually replacing `server.jar`, because `start`
checks it against `deployment.json`.

## Roll back a Java build and its progress

Select the exact history directory printed by the corresponding upgrade. List
the available history names and inspect their receipts first.

```bash
ls build-history
cat build-history/REPLACE_WITH_HISTORY_DIRECTORY/change.json
python3 manage.py rollback \
  build-history/REPLACE_WITH_HISTORY_DIRECTORY --replace
```

Rollback restores both the saved JAR and the progress snapshot associated with
that history entry. It replaces progress made after that snapshot. It also
creates a new safety recovery directory for the state being replaced.

The selected history must belong to this deployment and contain its matching
JAR and progress snapshot. If an earlier change failed before producing a
snapshot, inspect its receipt and available backups before selecting recovery.

## Updating the administration tools

The generated deployment contains a copy of `manage.py`. Updating the source
checkout does not update that copy automatically. Review release changes before
replacing administration code in an existing deployment. Keep a copy of the
previous script with your deployment recovery files.

For a new deployment, pull the desired public revision and run setup into a new
directory. Re-running setup over a live deployment is refused. Moving an
existing installation to another host also requires transferring or restoring
its database and persistent data, matching server/resources, and appropriate
configuration. These tools do not provide a single-command cross-host migration.

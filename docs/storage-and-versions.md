# Storage and game versions

The launcher supports shared assets and a full private copy. Both modes prepare
private executable patches, settings, and login state. Choose the mode when
preparing a new private game folder.

## Choose a storage mode

| Property | Shared assets | Full copy |
| --- | --- | --- |
| Launcher setting | Select **Share installed assets to save space** | Clear that checkbox |
| Large `StreamingAssets` tree | Linked to installed assets | Copied into the private client |
| Other game files | Copied | Copied |
| Additional disk space | Copied non-shared files, patch records, and private state | Approximately the copied installation plus private state |
| Depends on original asset location | Yes | No |
| Can original installation advance to 7.3 while private copy stays 7.2 | Source must remain at the matching 7.2 assets | Yes, after independent 7.2 preparation completes |
| Linux filesystem requirement | Symlinks and a working bubblewrap mount namespace | Normal read/write storage for the copy |
| Windows filesystem requirement | Hard links on one volume, or supported file symlinks across volumes | Normal read/write storage for the copy |

Shared mode saves the space occupied by the linked asset tree. It still copies
other game files. The launcher's preparation check calculates the required free
space for the chosen input and mode. The exact amount depends on the installation.

## What is shared

The shared tree is `GenshinImpact_Data/StreamingAssets`. On Linux the private
client links to the source tree. During shared play, bubblewrap exposes the
source installation through a read-only mount and gives the game writable access
to its private folder and private state.

On Windows the launcher creates private directories with file hard links on the
same volume. If a hard link cannot be created, it tries a file symlink. During
play it holds read-only sharing handles for the source assets and private link
names. Those handles prevent writes and deletion through the guarded links while
the game is running. The Windows launcher passes its guards to the game process.

Shared assets must stay stable while playing. Close the private game before
running an official updater or modifying the source installation. The launcher
records an asset inventory and checks it again before play. A changed inventory
requires restoring compatible assets or preparing another matching client.

The launcher patches the private copies of its supported patch files. It keeps
per-client verification and recovery records under `.private-launcher`. Private
login caches are excluded from the initial copy and managed in the private
installation afterward.

## Filesystems and locations

### Linux

Symlink sharing works across mounted filesystems, including common ext4 and XFS
setups. The source and destination do not need to be on the same volume. The
source must be readable, the destination writable, and the chosen filesystem
must support the required links and executable files.

Keep the source mounted at the path used during preparation. An external drive
that changes mount paths can break the stored links or source record. The
launcher also requires the source `StreamingAssets` directory and checked input
files to be independent regular inputs. Redirected source trees are rejected.

Shared play requires `bwrap` and permission to create its mount namespace.
Installing bubblewrap alone may be insufficient on a host whose sandbox policy
blocks unprivileged namespaces. See [Troubleshooting](troubleshooting.md) for a
small probe and the full-copy option.

### Windows

Put the source game and private folder on the same NTFS volume for hard-link
sharing. Separate drive letters normally indicate separate volumes, although
mount arrangements can differ. Hard links require filesystem support and cannot
span volumes.

Cross-volume sharing requires file symlinks, Windows Developer Mode, and suitable
filesystem support. The launcher reports an error if both link methods fail.
Choose full-copy mode in a new empty destination if those requirements cannot
be met. FAT-style removable storage is unsuitable for the hard-link layout.

### Moving a private client

A shared client depends on its stored source path and asset links. Moving it or
the source to a new drive can invalidate that relationship. Prepare a new private
client from the matching installation at its intended final location.

A full copy has independent game files. Preserve its `.private-launcher` records
when moving it, select its new location in the launcher, and run **Check files**.
Linux users with an advanced Wine/performance profile must also update or
recreate that profile's workspace and prefix mappings for the new location.

## Keep game 7.2 while the official game advances

The launcher release targets verified 7.2.0 input files and its bundled patch.
The official game's update schedule does not update the private server or
launcher automatically.

For shared mode, preserve a matching 7.2 source installation at its existing
location. Updating that source to 7.3 changes the assets used by the private
client. Keeping the private executable at 7.2 does not preserve assets that an
updater replaces in the shared source.

If you want to update your official installation independently, prepare a full
private 7.2 copy first, using a new empty destination with sharing cleared. Run
**Check files** and confirm that this copy works with your server before changing
the source installation. This requires enough storage for an independent copy.

A newer launcher release can change its bundled patch while still targeting
7.2. Read the release's compatibility information before applying it. A future
7.3 private-server migration requires matching game files, patch, server build,
and resources. It is a coordinated server upgrade.

## Server resource storage

Server resources are structured data used by the Java service. They are separate
from a player's large graphical game assets. A server host does not need a full
player game installation for this deployment workflow.

The optional server downloader uses pinned resource repository revisions and a
preparation recipe. Its cache avoids repeated downloads for future preparations.
Setup then copies prepared resources into the generated deployment so the
container has a usable local resource tree.

That process can hold both cached inputs and a deployment copy on disk. Runtime
services read the deployment resource copy. Remove or relocate a cache only when
you understand which preparation work depends on it. Preserve the selected
deployment's resources for operation and recovery.

## What belongs in backups

| Component | Recovery purpose |
| --- | --- |
| Player's private folder and `.private-launcher` | Local client layout, patch records, and local settings |
| Shared source installation | Matching assets required by a shared client |
| Launcher release archive | Executable and patch matching the selected client |
| Server progress archive | Accounts, database progress, persistent server data, and configuration snapshot |
| Matching Java JAR and prepared resources | Server version needed to interpret the saved progress |
| Deployment directory and recovery records | Active configuration, project identity, and build rollback context |

Server progress lives on the operator's server. Reinstalling the player launcher
does not restore or change that database. Ask the operator about missing account
progress or server-side rollback.

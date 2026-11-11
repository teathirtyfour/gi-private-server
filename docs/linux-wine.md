# Linux setup with DWProton

Use the standalone **wine-dwproton WoW64 11.0-11** runner for the baseline in
this guide. The development configuration uses that runner with **DXVK 3.0**
and a dedicated 64-bit private prefix. Keep those versions fixed while testing
your first connection. Compatibility with another runner, newer version, or
different GPU driver needs its own playtest.

The standalone runner is distributed through Spritz-Wine. Its archive contains
`bin/wine`, `bin/wineboot`, and `bin/wineserver`, which fit this launcher's
**Wine executable** interface. The upstream project documents direct execution
with a selected prefix. [Spritz-Wine](https://github.com/NelloKudo/spritz-wine)

## 1. Prepare the Linux host

You need an x86-64 Linux desktop, a working Vulkan driver for your GPU, and tools
for downloading and extracting archives. Shared asset mode also needs bubblewrap.
Winetricks and cabextract are used for the Visual C++ runtime step below.

For Debian/Ubuntu-based systems, install the supporting tools through the system
package manager.

```bash
sudo apt update
sudo apt install bubblewrap curl xz-utils winetricks cabextract vulkan-tools
```

For Arch-based systems, the corresponding command is the following.

```bash
sudo pacman -S --needed bubblewrap curl xz winetricks cabextract vulkan-tools
```

These tools can depend on distribution Wine packages. The launcher will use the
specific DWProton executable selected later. Install GPU/Vulkan drivers using
your distribution's instructions for your AMD, Intel, or NVIDIA hardware, then
check that Vulkan detects the intended GPU.

```bash
vulkaninfo --summary
```

Resolve a missing Vulkan device or driver error before installing DXVK. When
asking for help, include the GPU name, driver version, and the output of this
command. CPU-only software rendering is unsuitable for the intended game setup.

## 2. Download the standalone DWProton runner

These commands download the pinned standalone runner and verify its published
GitHub asset SHA-256 before extraction. The versioned archive is available on
the upstream release page. [wine-dwproton 11.0-11 release](https://github.com/NelloKudo/spritz-wine/releases/tag/wine-dwproton-11.0-11)

```bash
mkdir -p "$HOME/Downloads/private-server-runtime"
cd "$HOME/Downloads/private-server-runtime"
curl -fL --retry 3 \
  https://github.com/NelloKudo/spritz-wine/releases/download/wine-dwproton-11.0-11/wine-dwproton-wow64-11.0-11-x86_64.tar.xz \
  -o wine-dwproton-wow64-11.0-11-x86_64.tar.xz
printf '%s  %s\n' \
  8505928aced5229f82da54912b23df4a495723b984adb5e0ea460da8bd3353c9 \
  wine-dwproton-wow64-11.0-11-x86_64.tar.xz | sha256sum -c -
mkdir -p "$HOME/.local/share/private-server-runners"
tar -xJf wine-dwproton-wow64-11.0-11-x86_64.tar.xz \
  -C "$HOME/.local/share/private-server-runners"
```

Set a shell variable to the extracted runner and check its version.

```bash
runner_dir="$HOME/.local/share/private-server-runners/wine-dwproton-wow64-11.0-11"
"$runner_dir/bin/wine" --version
```

The version output should identify Wine 11.0 and DWProton. If you downloaded a
different version, adjust the directory and retain that exact version in your
support report. A normal full Proton archive contains a Proton launcher script
and its own runtime conventions. This guide uses the standalone Wine build to
match the application's direct Wine launch path.

## 3. Prepare the private game folder

Download and extract the Linux launcher as described in
[Player launcher](launcher.md#download-and-extract). Select the matching normal
game installation, choose shared storage or full copy, and press **Prepare
private copy**. Use a location you own, such as `$HOME/Games/GenshinPrivate`.

In **Wine executable**, browse to the runner installed above. With a username
of `alex`, the example absolute executable path would be
`/home/alex/.local/share/private-server-runners/wine-dwproton-wow64-11.0-11/bin/wine`.
Use your actual home directory. The file picker or field expects an executable
path, so paste the expanded absolute path.

Close the launcher before configuring the prefix below. The commands assume
preparation completed in the example private folder. Change `private_client`
to the exact folder you selected if it differs.

## 4. Initialize the launcher's private prefix

A Wine prefix holds its Windows registry, drive mappings, runtime DLLs, and
application state. The ordinary launcher path uses the prefix at
`<private game folder>/.private-launcher/wine-prefix`. Configure that exact path
so your runtime installation is used by **Play**.

Run the following in a terminal. Keep the variable assignments when continuing
to the following steps. Re-enter them if you open another terminal.

```bash
runner_dir="$HOME/.local/share/private-server-runners/wine-dwproton-wow64-11.0-11"
private_client="$HOME/Games/GenshinPrivate"
private_prefix="$private_client/.private-launcher/wine-prefix"
test -f "$private_client/GenshinImpact.exe"
test -d "$private_client/.private-launcher"
test -x "$runner_dir/bin/wineboot"
WINEPREFIX="$private_prefix" WINEARCH=win64 \
  "$runner_dir/bin/wineboot" -u
WINEPREFIX="$private_prefix" "$runner_dir/bin/wineserver" -w
```

Stop and correct the paths if a `test` command fails. Only continue after the
prefix initialization completes successfully. Wine may show first-time setup
dialogs. Use your normal desktop user for runner and prefix commands.

These variables target a prefix inside the prepared private client. An existing
official-game prefix can have different drive mappings or launcher integration.
Use an independent private prefix so that game-runtime changes are scoped to
the prepared private installation.

## 5. Install the Visual C++ runtime

The local reference prefix has native Visual C++ runtime overrides. For a fresh
private prefix, use Winetricks to install the 2015–2022 runtime with the selected
runner. Winetricks downloads the runtime from its configured original sources.
It may require network access and extraction tools. [Winetricks](https://github.com/Winetricks/winetricks)

```bash
WINE="$runner_dir/bin/wine" \
  WINESERVER="$runner_dir/bin/wineserver" \
  WINEPREFIX="$private_prefix" WINEARCH=win64 \
  winetricks -q vcrun2022
WINEPREFIX="$private_prefix" "$runner_dir/bin/wineserver" -w
```

If Winetricks reports a download or checksum failure, retain the message and
check the installed Winetricks version and upstream instructions. Continue only
after the runtime operation succeeds. Use the same private prefix for the next
step.

## 6. Install DXVK 3.0 into the private prefix

DXVK supplies the Direct3D-to-Vulkan DLLs. This standalone runner setup needs
those DLLs in its selected prefix. The pinned archive is the Windows DLL package
named `dxvk-3.0.tar.gz`. [DXVK 3.0 release](https://github.com/doitsujin/dxvk/releases/tag/v3.0)

```bash
cd "$HOME/Downloads/private-server-runtime"
curl -fL --retry 3 \
  https://github.com/doitsujin/dxvk/releases/download/v3.0/dxvk-3.0.tar.gz \
  -o dxvk-3.0.tar.gz
printf '%s  %s\n' \
  7dd3243fe1b260a0e9b0b9e49d672ae32e3398bee18c97e7e8569d0ef0eca92d \
  dxvk-3.0.tar.gz | sha256sum -c -
tar -xzf dxvk-3.0.tar.gz
dxvk_dir="$HOME/Downloads/private-server-runtime/dxvk-3.0"
cp "$dxvk_dir/x64/"*.dll "$private_prefix/drive_c/windows/system32/"
cp "$dxvk_dir/x32/"*.dll "$private_prefix/drive_c/windows/syswow64/"
```

In a 64-bit prefix, `system32` holds 64-bit DLLs and `syswow64` holds 32-bit DLLs.
Set the Direct3D 11 and DXGI native overrides in that prefix. This follows DXVK's
prefix installation method for its D3D11 components.
[DXVK installation instructions](https://github.com/doitsujin/dxvk#how-to-use)

```bash
WINEPREFIX="$private_prefix" "$runner_dir/bin/wine" reg add \
  'HKCU\Software\Wine\DllOverrides' /v d3d11 /t REG_SZ /d native /f
WINEPREFIX="$private_prefix" "$runner_dir/bin/wine" reg add \
  'HKCU\Software\Wine\DllOverrides' /v dxgi /t REG_SZ /d native /f
WINEPREFIX="$private_prefix" "$runner_dir/bin/wineserver" -w
```

The launcher supplies its own `Astrolabe` override for the private client patch
when starting the game. Keep the graphics DLLs and their registry configuration
in the selected prefix.

## 7. Start the launcher with the compatibility settings

The development configuration enables the DWProton timeout fix and passes
`WINEFSYNC=1`. Set those variables on the launcher process so its child game receives them.
Adjust the application path if you extracted the launcher elsewhere. Upstream
documents the timeout-fix variable for affected game launches.
[Spritz-Wine runtime options](https://github.com/NelloKudo/spritz-wine#useful-environmental-variables)

```bash
WINE_ENABLE_TIMEOUT_FIX=1 WINEFSYNC=1 \
  "$HOME/Applications/private-server-launcher/PrivateServerLauncher/PrivateServerLauncher"
```

Confirm the selected **Wine executable** still points to DWProton. Import the
server profile, select the prepared private folder, and run **Check files** and
**Test connection** before **Play**.

The launcher saves the runner path when **Play** is pressed. Its ordinary settings
file does not save arbitrary shell environment variables. Start it with the
command above each time, or create a desktop entry with an equivalent `Exec`
line. For example, an entry for a user named `alex` can contain the following.

```ini
[Desktop Entry]
Type=Application
Name=Private Server Launcher
Exec=env WINE_ENABLE_TIMEOUT_FIX=1 WINEFSYNC=1 /home/alex/Applications/private-server-launcher/PrivateServerLauncher/PrivateServerLauncher
Terminal=false
Categories=Game;
```

Use your actual absolute application path in `Exec`. Store the entry as
`~/.local/share/applications/private-server-launcher.desktop`. Paths containing
spaces need desktop-entry quoting around the executable path.

## 8. Verify graphics during your playtest

For a temporary graphics overlay, start the launcher with a DXVK HUD request.
Its device and version display helps confirm which graphics stack the game uses.
[DXVK HUD options](https://github.com/doitsujin/dxvk#hud)

```bash
WINE_ENABLE_TIMEOUT_FIX=1 WINEFSYNC=1 DXVK_HUD=devinfo,version,fps \
  "$HOME/Applications/private-server-launcher/PrivateServerLauncher/PrivateServerLauncher"
```

If the game exits, record its exit code from the launcher and the runner version.
Check Vulkan availability, prefix location, installed runtime, and DXVK overrides
before changing server settings. A successful login-dispatch test does not test
the GPU stack.

## Advanced existing profiles

The optional **Wine/performance profile** can select an existing private prefix,
runner, drive mappings, Wayland mode, synchronization, and performance tools.
When selected, its prefix and runner take precedence over the ordinary prefix
path and **Wine executable** setting. Follow the profile supplier's exact paths.

A profile importing GameMode, MangoHUD, frame generation, or an FPS unlocker
also requires those tools and its recorded files. The baseline steps above focus
on DWProton, the private prefix, Visual C++, and DXVK. Add optional performance
features after confirming that the game connects and renders.

## Verification limits

The baseline versions match the project's local runner and DXVK configuration.
Fresh prefix initialization, pinned DXVK download, DLL installation, and registry
overrides were checked in an isolated test directory. The Visual C++ installer
step follows Winetricks' runtime recipe and was not re-executed in that check. Driver
support and complete first-time game startup depend on the player's machine.
The launcher does not automatically install the runner, runtime, or DXVK, and
these instructions do not establish a full-game test on every Linux setup.

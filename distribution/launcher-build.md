# Build the launcher

Keep checkout file bytes unchanged on Windows. The public checkout includes
`.gitattributes` to preserve the checksums used across both build platforms.

The launcher build takes two inputs, `Astrolabe.dll` and the corresponding
`dependencies.tar.gz`. Build the patch once and use those same inputs for both
platforms. PyInstaller packages the Linux executable on Linux and the Windows
executable on Windows.

Player downloads are available from the
[latest release](https://github.com/teathirtyfour/gi-private-server/releases/latest).
Extract the whole platform archive before opening the launcher.

## Build both packages with GitHub Actions

Open the **Source checks** workflow for the commit you want to package. A push
starts it automatically. You can also select **Run workflow**. After the run
succeeds, download `launcher-ubuntu-22.04` or `launcher-windows-2022` from the run's
artifacts. Extract the download and keep the entire launcher folder together,
including `_internal`, `licenses`, and `corresponding-source`. Open
`PrivateServerLauncher` on Linux or `PrivateServerLauncher.exe` on Windows.

To package one platform yourself from a workflow build, download
`patch-and-dependency-source` and extract its two files to `out/launcher-inputs/`
in a checkout of the same commit. Then follow the Linux or Windows steps below.

## Build the inputs from local source

Run these commands from the repository root on Linux x86-64 with Git, Python
3.11 or newer, and Rustup. Use Rust 1.99.1 and LLVM-MinGW 20261104 UCRT x86-64.
Put the LLVM-MinGW `bin` directory on `PATH` so `x86_64-w64-mingw32-clang` is
available. The `patch` job in `.github/workflows/ci.yml` pins the compiler archive
URL and checksum.

```bash
rustup toolchain install 1.99.1 --profile minimal --target x86_64-pc-windows-gnullvm
export RUSTUP_TOOLCHAIN=1.99.1
mkdir -p out/launcher-inputs
python3 distribution/build.py patch > out/launcher-inputs/patch.json
launcher_dll=$(python3 -c 'import json; print(json.load(open("out/launcher-inputs/patch.json"))["path"])')
cp -- "$launcher_dll" out/launcher-inputs/Astrolabe.dll
cargo vendor --manifest-path vendor/hk4e-patch-universal/Cargo.toml \
  --locked --versioned-dirs out/launcher-inputs/vendor
python3 distribution/vendor_patch_sources.py \
  --vendor out/launcher-inputs/vendor \
  --lock vendor/hk4e-patch-universal/Cargo.lock \
  --output out/launcher-inputs/dependencies.tar.gz
```

Use a fresh input directory for a subsequent build if the dependency archive
already exists. The archive builder checks the vendored files against
`Cargo.lock`. For Windows packaging, transfer `Astrolabe.dll` and
`dependencies.tar.gz` to `out/launcher-inputs/` on the Windows checkout.

## Package and verify on Linux

Use a Debian or Ubuntu build environment with CPython 3.14.10, working Tk,
`binutils`, and `xvfb`. The packaging script uses the build environment's
`dpkg` metadata to collect notices for the libraries it bundles. The build Python
must include its `LICENSE.txt`. From the repository root, using that environment:

```bash
python3.14 -m venv out/launcher-venv
out/launcher-venv/bin/python --version
out/launcher-venv/bin/python -m pip install -r distribution/requirements-build.txt
out/launcher-venv/bin/python -c 'import tkinter; print(tkinter.Tcl().eval("info patchlevel"))'
launcher_sha=$(sha256sum out/launcher-inputs/Astrolabe.dll | cut -d ' ' -f 1)
out/launcher-venv/bin/python distribution/build_launcher.py \
  --patch out/launcher-inputs/Astrolabe.dll --patch-sha256 "$launcher_sha" \
  --patch-dependency-archive out/launcher-inputs/dependencies.tar.gz \
  --output out/launcher-linux --frozen
xvfb-run -a out/launcher-linux/PrivateServerLauncher --self-test-ui out/launcher-linux-ui.json
```

The distributable is the whole `out/launcher-linux/` folder.

## Package and verify on Windows

Install 64-bit CPython 3.14.10 with Tcl/Tk and pip. From the repository root in
PowerShell, with the two input files in `out/launcher-inputs/`:

```powershell
py -3.14 --version
py -3.14 -m pip install -r distribution/requirements-build.txt
py -3.14 -c "import tkinter; print(tkinter.Tcl().eval('info patchlevel'))"
$launcherSha = (Get-FileHash out/launcher-inputs/Astrolabe.dll -Algorithm SHA256).Hash.ToLowerInvariant()
py -3.14 distribution/build_launcher.py --patch out/launcher-inputs/Astrolabe.dll `
  --patch-sha256 $launcherSha --patch-dependency-archive out/launcher-inputs/dependencies.tar.gz `
  --output out/launcher-windows --frozen
if ($LASTEXITCODE -ne 0) { throw 'Launcher packaging failed' }
$launcher = Start-Process out/launcher-windows/PrivateServerLauncher.exe `
  -ArgumentList '--self-test-ui', 'out/launcher-windows-ui.json' -Wait -PassThru
if ($launcher.ExitCode -ne 0) { throw 'Launcher self-test failed' }
Get-Content out/launcher-windows-ui.json
```

The distributable is the whole `out/launcher-windows/` folder. Each self-test
writes a report with `passed` and `uiRendered` set to `true`. It checks the
package and renders the launcher window without starting the game. Choose a
fresh package directory and report filename when rebuilding.

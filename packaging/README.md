# Windows DEV packaging

Use Windows x64 Python 3.13 in a dedicated build virtual environment. Install requirements-desktop-dev.lock. Provide matching Playwright headless Chromium, an FFmpeg executable with its shared DLLs, and the reviewed jarvis-mobile source checkout.

    python scripts/build_dev_package.py --help
    python scripts/build_dev_package.py --ffmpeg <ffmpeg.exe> --browsers <browser-cache> --mobile <mobile-checkout>
    python scripts/check_frozen_dev_package.py --help

The builder emits an onedir ZIP, full package manifest and SHA256SUMS.txt under ignored .packaging-dev/. It copies only allowlisted mobile assets and explicitly selected runtime tools. Account credentials, local models, build caches and user settings are excluded. The archive contains third-party license notices and dependency licenses.

check_frozen_dev_package.py validates relocation, an empty user profile, GUI creation, Chromium/FFmpeg, per-user installation and gateway pairing. check_dev_sandbox.ps1 is only for Windows Sandbox (guarded by WDAGUtilityAccount). Map the bundle read-only as C:\JARVIS-DEV and a dedicated writable report directory containing that script as C:\JARVIS-Test; disable networking and clipboard. Run the script inside the guest. It shuts down only the Sandbox guest after writing result.json. Never run it on the host.

Installed versions live in %LOCALAPPDATA%\JARVIS-DEV-Station\versions; current.json selects the version. User data lives in data; protected ChatGPT credentials continue using the existing shared account store. No production paths or startup entries are modified.

The optional bundled phone gateway uses --gateway --phone-host <name.ts.net:8443> --tv-host <LAN-IPv4>, default HTTP port 10817 and relay 10818. It needs explicitly configured external routing. Installing the package does not change Tailscale or existing phone gateways.

## Single-file Windows installer

`packaging/jarvis-dev.iss` wraps the already verified v1.4.3 payload without rebuilding or changing it. Installation is per-user with a unique DEV AppId, an owned Start menu shortcut and a Windows uninstall entry. The existing versioned deployment helper verifies the complete payload before selecting it. The installer rejects a different destination, reparse points and an open desktop instance; it never terminates the desktop or phone services. Uninstall removes only installer-owned files and explicit launch metadata, preserving user data, shared account credentials and separately installed older versions.

Build on Windows with Windows Sandbox enabled. Obtain `innosetup-7.1.1-x64.exe` from the official source in `compiler-provenance.json`, verify its Authenticode publisher and SHA256, then prepare an isolated build:

    python scripts/build_windows_installer.py --bundle <verified-v1.4.3-bundle> --compiler <official-compiler.exe> --previous-bundle <verified-v1.4.2-bundle>

The script emits `.packaging-dev/latest-installer-build.json` with the WSB configuration path. Open that configuration in Windows Sandbox. Only the allowlisted build inputs and payloads are mapped read-only; the dedicated reports/output directory is writable. Networking and clipboard are disabled, and no account stores are mapped. The guest installs the compiler, builds Setup.exe and checks installation, all installed hashes, GUI, Chromium/FFmpeg, repeated installation, active-instance/path guards, uninstall, upgrade from v1.4.2 and rollback. It writes result.json and shuts down only the guarded Sandbox guest. A successful result is required before delivering the setup executable. No compiler is installed on the host.

The JARVIS setup executable is unsigned; the compiler's signature does not sign generated installers. User instructions: docs/INSTALL_WINDOWS_RU.md.

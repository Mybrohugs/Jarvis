# Windows DEV packaging

Use Windows x64 Python 3.13 in a dedicated build virtual environment. Install requirements-desktop-dev.lock. Provide matching Playwright headless Chromium, an FFmpeg executable with its shared DLLs, and the reviewed jarvis-mobile source checkout.

    python scripts/build_dev_package.py --help
    python scripts/build_dev_package.py --ffmpeg <ffmpeg.exe> --browsers <browser-cache> --mobile <mobile-checkout>
    python scripts/check_frozen_dev_package.py --help

The builder emits an onedir ZIP, full package manifest and SHA256SUMS.txt under ignored .packaging-dev/. It copies only allowlisted mobile assets and explicitly selected runtime tools. Account credentials, local models, build caches and user settings are excluded. The archive contains third-party license notices and dependency licenses.

check_frozen_dev_package.py validates relocation, an empty user profile, GUI creation, Chromium/FFmpeg, per-user installation and gateway pairing. check_dev_sandbox.ps1 is only for Windows Sandbox (guarded by WDAGUtilityAccount). Map the bundle read-only as C:\JARVIS-DEV and a dedicated writable report directory containing that script as C:\JARVIS-Test; disable networking and clipboard. Run the script inside the guest. It shuts down only the Sandbox guest after writing result.json. Never run it on the host.

Installed versions live in %LOCALAPPDATA%\JARVIS-DEV-Station\versions; current.json selects the version. User data lives in data; protected ChatGPT credentials continue using the existing shared account store. No production paths or startup entries are modified.

The optional bundled phone gateway uses --gateway --phone-host <name.ts.net:8443> --tv-host <LAN-IPv4>, default HTTP port 10817 and relay 10818. It needs explicitly configured external routing. Installing the package does not change Tailscale or existing phone gateways.

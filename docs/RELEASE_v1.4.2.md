# JARVIS DEV Station v1.4.2

Standalone Windows x64 DEV package: text chat through the existing ChatGPT sign-in bridge and explicit video-to-TV commands through the existing queue/router. Includes Python, Chromium, FFmpeg and mobile gateway assets. The legacy voice HUD and full desktop automation remain separate.

Extract the entire ZIP, then run Install-DEV.cmd. Open **JARVIS DEV Station** from the Start menu. Run-DEV.cmd also supports portable use. Installation is per-user, versioned, and creates no autorun entry. Rollback-DEV.cmd switches to a previously installed version when one exists; user data is preserved.

ChatGPT uses the existing protected Windows session or official sign-in. Optional Home Assistant settings use http://127.0.0.1:8123 and media_player.bravia_kd_55x81j. YouTube TV linking is optional. Credentials are stored locally and excluded from the package. Existing production and phone services are not reconfigured.

Validation of the final ZIP: 1,754 manifest entries; SHA256 73fda9f3e5e4428b57505a2c30841062e76f918de8cf81ba66fa88b5ce5c627f. Clean Windows Sandbox with networking disabled passed dependency checks, GUI creation, actual Chromium/FFmpeg execution, per-user installation and installed GUI. A relocated bundle with empty application profiles passed the same offline checks plus gateway pairing. A real ChatGPT request from the final executable passed separately with the existing account session.

Source checks: 10 packaging tests, 26 ChatGPT session tests, 18 TV/HTTP tests; broad Python suite 254 tests with 14 optional skips. No new mobile source changes are required. This is an unsigned DEV build, supplied as a draft prerelease. No main merge or original Cast PR #2 changes.

# JARVIS DEV Station v1.4.3

Fixes YouTube delivery to an already paired TV. The previous route opened the native app through Home Assistant, then tried a separate Cast receiver before using the paired channel. On the owner's KD-55X81J the installed v1.4.2 route failed with the native YouTube channel unavailable. Opening the native app and using its saved pairing delivered the requested video successfully.

For a paired YouTube TV, the router now opens the native app and sends the video through the existing encrypted YouTube link without launching another Cast receiver. Unpaired YouTube still uses Cast. Other public video sites keep the existing PC relay. A paired-channel failure remains a failure; it never becomes an automatic receiver switch or a false success.

Playback observation now waits within the existing bounded timeout when the initial event is starting/buffering. Only the requested video reported as playing is confirmed. Starting at timeout, paused, another video and missing status remain unconfirmed. No playback command is replayed for status verification.

Validation: 69 targeted tests passed; broad regression 256 tests run, 14 optional skips, no failures. A real request through the phone DEV HTTP gateway completed through youtube-link, with video 5-wdT_xbVDQ confirmed playing. Existing device registrations were preserved and the temporary test device was revoked. The same route serves the desktop station and phone; no new remote permissions or mobile source changes.

Windows x64 package includes Python, Chromium and FFmpeg. Install-DEV.cmd installs per user; Start menu entry: JARVIS DEV Station. Version 1.4.2 stays installed as the rollback target. Local encrypted Home Assistant/YouTube settings were copied into the owner's separate station without changing the original stores. Account credentials and user settings are never included in the ZIP.

Production, main branches, original Cast PR #2 and Tailscale routing are unchanged. This is an unsigned DEV draft prerelease. Legacy voice HUD remains separate. Build and validation instructions: packaging/README.md.

Final ZIP validation: clean Windows Sandbox (network disabled) passed doctor, GUI, actual Chromium/FFmpeg, installation and installed GUI. Relocation into an empty user profile passed, including gateway pairing. The installed v1.4.3 EXE delivered video 5-wdT_xbVDQ with playing confirmed and answered a real ChatGPT request.

ZIP SHA256: `55570b3eaaae6db55220745f9becc50e1aaf168b0aface7effe31e7b0f98fd99`; 1,754 package manifest entries.

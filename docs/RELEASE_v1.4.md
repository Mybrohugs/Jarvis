# v1.4 — ChatGPT and video to TV (DEV)

- Mobile chat now uses official Sign in with ChatGPT on the paired PC; no Claude/OpenAI key on the phone.
- OAuth implementation is included; account secrets remain local in Windows DPAPI. Reconnect with `python -m developer_core.jarvis_local.chatgpt_plan_engine --connect`.
- Phone pairing persists across reloads. Closed/expired pairing is explained; API responses are not cached.
- Video uses the existing queue and Home Assistant/TV integration. Unsupported pages fall back to an isolated Chromium, including embedded players requiring Play.
- Closing the desktop no longer triggers repeated reopening by default; typed UI commands wake the DEV shell (patch included).
- No production rollout or merge of existing PR #2. This is a DEV prerelease, not an installer.

Verified locally: live ChatGPT response and weather tool call; real stream extraction and HLS preparation; user confirmed phone-to-TV playback. Unit/browser regression results are recorded separately. OpenAI voice is not used: transcription is on the PC and speech on the phone. DRM/login-required players are not guaranteed.

## Run from this repository

Install existing core requirements plus requirements-v1.4-dev.txt in an isolated environment. Install the matching Playwright Chromium with `python -m playwright install chromium`. Configure the existing local Home Assistant credentials and pair the phone. Run:

```
python -m developer_core.jarvis_local.tv_phone_dev --mobile /path/to/jarvis-mobile --host your-pc.your-network.ts.net:8443 --port 10797
```

Use your private HTTPS proxy to port 10797. Do not replace an existing production route. The account, Tailscale setup, FFmpeg and TV must be configured locally; credentials and local models are excluded from GitHub.

## Verification

PC export: 254 regression tests (14 optional tests skipped), 17 TV tests and 6 ChatGPT tests passed. Mobile: 24 Node tests passed. User confirmed phone-to-TV playback. Production and PR #2 remain unchanged.

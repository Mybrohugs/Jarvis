# v1.4.1 — recovery of the shared ChatGPT session (DEV)

Desktop and the phone gateway now serialize credential reads/refreshes/writes with an OS file lock shared by every process using the same DPAPI credential path. The replacement token is reloaded after acquiring the lock. The lock has a bounded wait, is released after exceptions/process exit and contains no credentials.

Transient refresh failures preserve credentials and a still-valid access token. A short shared cooldown prevents repeated refresh attempts. Terminal refresh errors stop token reuse while retaining the issued client/host registration for reauthorization. Unknown 401 responses trigger one confirmation refresh without replaying inference. A new sign-in is requested only when required, not for network failures, limits, account policy restrictions or client configuration problems.

Desktop status now checks the session/model endpoint rather than only the presence of a credential file. The phone gateway returns safe error categories, needs_auth/retryable flags and appropriate HTTP status. Raw provider error bodies are not exposed. The phone displays distinct labels for network, usage limit, sign-in and service problems. Mobile cache cleanup preserves other apps' caches.

## Verification

- 26 session/ChatGPT tests: real independent Windows processes using FAKE credentials refreshed once, reused the rotated token after restart, released the lock after abrupt process exit and respected the lock timeout. Network/limit/revocation and recovery cases are simulated; the user's credentials were not invalidated for testing.
- 18 TV/DEV HTTP regression tests, including typed failure responses and preservation of the paired-device token.
- 28 mobile Node tests passed.
- Playwright 390x844: real test HTTP/auth gateway with model replies mocked; network/limit/auth labels and return to ready verified, no JavaScript errors.
- Two actual simultaneous text requests succeeded through the desktop bridge and running private phone HTTP gateway. Temporary test-device access was revoked after the check.
- The desktop DEV window was gracefully restarted and reconnected; the isolated phone gateway returned HTTP 200. Production, main and PR #2 were not changed. TV playback was not triggered by these checks.

## Compatibility and rollback

No new dependencies or account permission scopes. Both desktop and gateway must use the updated bridge to serialize refreshes across processes. A deployment may copy the included chatgpt_plan_engine.py to the existing desktop DEV automation directory; do not copy credentials between directories.

Keep the v1.4 source artifacts unchanged. This patch is a separate DEV branch. Local backups of the exact deployed files and SHA-256 values were created before applying; restore only those files and restart only the DEV processes for rollback.

## Sources

- https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions
- https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery

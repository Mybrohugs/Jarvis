# Transfer scope

PC source: current DEV jarvis_local modules, their regression tests, Home Assistant connector, official OAuth implementation and isolated phone gateway. Mobile source is in Mybrohugs/jarvis-mobile, matching DEV release v1.4. Desktop UI changes are provided as an exact patch because the existing desktop shell is installed separately.

Production files, PR #2, personal access URLs, pairing codes, account credentials, runtime queues, logs, browser/model binaries and temporary evidence are excluded. The public repository package has explicit portability adjustments for the OAuth module and installed Playwright Chromium.

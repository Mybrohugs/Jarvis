# Desktop DEV patch

The existing desktop shell is an external application. This release carries the exact main.py/ui.py patch for waking on typed commands. It is not an independent desktop installer.

Apply only to a DEV copy matching sha256.json. Check the patch first with `git apply --check text-wake.patch`, then apply it in that DEV copy. Do not apply to production. scripts/jarvis_boot.ps1 separately fixes unwanted restarts after closing the desktop window.

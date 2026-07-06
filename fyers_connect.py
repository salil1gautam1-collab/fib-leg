"""fyers_connect — ONE command to connect Fyers for good (owner ask 2026-07-06).

Run once per client. It collects the credentials privately, proves the UNATTENDED
daily login works, and (if `gh` is authenticated) pushes them into GitHub's
encrypted secrets so the cloud logs in / does 2FA / refreshes the daily token BY
ITSELF, forever. After this you never touch Fyers, PowerShell, or GitHub again.

    python fyers_connect.py

Nothing you type is echoed or printed back. Secrets go only to ~/.fibleg/ (local)
and GitHub Actions secrets (encrypted) — never to the repo or this screen.
"""
import json
import subprocess
import sys
from getpass import getpass
from pathlib import Path

CFG = Path.home() / ".fibleg"
CREDS = CFG / "fyers.json"
REPO = "salil1gautam1-collab/fib-leg"
GH = (r"C:\Users\Admin\AppData\Local\Microsoft\WinGet\Packages"
      r"\GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\bin\gh.exe")

FIELDS = [
    ("app_id", "App ID (e.g. 21KUP94D7D-100)", True),
    ("secret_id", "Secret ID", True),
    ("redirect_uri", "Redirect URL [https://127.0.0.1/]", False),
    ("fy_id", "Fyers login / client id (e.g. XS08800)", True),
    ("totp_key", "TOTP secret key (the authenticator manual-entry key)", True),
    ("pin", "4-digit trading PIN", True),
]


def ask(label, required):
    while True:
        v = getpass(label + ": ").strip()
        if v:
            return v
        if not required:
            return "https://127.0.0.1/"
        print("  (required)")


def main():
    print("\n=== Connect Fyers — one-time setup ===")
    print("Type/paste each value; the screen stays blank on purpose.\n")
    data = {k: ask(lbl, req) for k, lbl, req in FIELDS}
    CFG.mkdir(parents=True, exist_ok=True)
    CREDS.write_text(json.dumps(data, indent=2))
    try:
        CREDS.chmod(0o600)
    except Exception:  # noqa: BLE001
        pass
    print("\n[1/3] Saved locally. Testing the UNATTENDED login (this is the one that")
    print("      runs in the cloud every morning — 2FA and all)…")
    try:
        from fibleg.data import fyers_feed
        token = fyers_feed.auto_login()
        client = fyers_feed.get_client()
        bars = fyers_feed.fyers_series(client, "RELIANCE.NS", "5m", days=2)
        assert bars, "login ok but no data came back"
        print(f"      ✓ unattended login works · pulled {len(bars)} live bars "
              f"(token …{token[-6:]})")
    except Exception as e:  # noqa: BLE001
        print(f"      ✗ unattended login FAILED: {e}")
        print("      The credentials are saved locally so interactive use still works,")
        print("      but the cloud can't self-login yet. Most common cause: the TOTP")
        print("      secret is wrong — re-generate it in Fyers → Security → TOTP and")
        print("      re-run this. (Nothing was pushed to GitHub.)")
        return 1

    print("\n[2/3] Pushing to GitHub encrypted secrets (cloud will use these)…")
    secret_names = {"app_id": "FYERS_APP_ID", "secret_id": "FYERS_SECRET_ID",
                    "redirect_uri": "FYERS_REDIRECT_URI", "fy_id": "FYERS_FY_ID",
                    "totp_key": "FYERS_TOTP_KEY", "pin": "FYERS_PIN"}
    gh = GH if Path(GH).exists() else "gh"
    ok = True
    for k, name in secret_names.items():
        try:
            r = subprocess.run([gh, "secret", "set", name, "--repo", REPO],
                               input=data[k], text=True, capture_output=True)
            if r.returncode != 0:
                ok = False
                print(f"      ✗ {name}: {r.stderr.strip()[:80]}")
            else:
                print(f"      ✓ {name} set")
        except FileNotFoundError:
            print("      ✗ gh CLI not found — skipping GitHub secrets. Install/auth gh,")
            print("        or set the six FYERS_* secrets manually in repo Settings.")
            ok = False
            break

    if not ok:
        print("\n[3/3] Local login works; finish GitHub secrets, then flip SCAN_SOURCE.")
        return 1
    print("\n[3/3] Done. Every FYERS_* secret is set and the unattended login is proven.")
    print("      Last step (tell Claude, or run once): flip SCAN_SOURCE to 'fyers' in")
    print("      .github/workflows/scan.yml and push — then the cloud is on Fyers,")
    print("      self-logging-in daily. You never touch this again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

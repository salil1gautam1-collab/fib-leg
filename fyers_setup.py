"""Interactive, private Fyers credential setup — writes ~/.fibleg/fyers.json.

Nothing is echoed; nothing is printed back; nothing leaves this machine.

    python fyers_setup.py

REQUIRED (for a one-time browser login — the simple path):
  app_id        the App ID          e.g. 21KUP94D7D-100
  secret_id     the Secret ID       (dashboard, next to the App ID)
  redirect_uri  the Redirect URL    press Enter to accept https://127.0.0.1/

OPTIONAL (only for later UNATTENDED cloud login — press Enter to skip all three):
  fy_id, totp_key, pin
"""
import json
from getpass import getpass
from pathlib import Path

CFG = Path.home() / ".fibleg"
DEST = CFG / "fyers.json"


def ask(label, default=""):
    val = getpass(f"{label}{f' [{default}]' if default else ''}: ").strip()
    return val or default


print("Fyers setup — values are hidden as you type/paste; nothing is shown or logged.")
print("Fill the first three. Press Enter to skip the optional three.\n")
data = {
    "app_id": ask("App ID (e.g. 21KUP94D7D-100)"),
    "secret_id": ask("Secret ID"),
    "redirect_uri": ask("Redirect URL", default="https://127.0.0.1/"),
}
print("\n-- optional (unattended cloud login) — Enter to skip --")
for k, label in (("fy_id", "Fyers login / client id"),
                 ("totp_key", "TOTP secret key"),
                 ("pin", "4-digit PIN")):
    v = ask(label)
    if v:
        data[k] = v

miss = [k for k in ("app_id", "secret_id", "redirect_uri") if not data.get(k)]
if miss:
    print("\nMissing required:", ", ".join(miss), "— nothing written. Re-run.")
    raise SystemExit(1)

CFG.mkdir(parents=True, exist_ok=True)
DEST.write_text(json.dumps(data, indent=2))
try:
    DEST.chmod(0o600)
except Exception:  # noqa: BLE001
    pass
print(f"\nWrote {DEST}")
print("Fields set:", ", ".join(f"{k}({len(str(v))} chars)" for k, v in data.items()))
print("Valid JSON ✓  ·  headless fields:",
      "yes" if all(k in data for k in ("fy_id", "totp_key", "pin")) else "skipped (fine for now)")

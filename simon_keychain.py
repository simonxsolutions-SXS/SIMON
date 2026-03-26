"""
simon_keychain.py — macOS Keychain integration for S.I.M.O.N.
=============================================================
Simon-X Solutions | [OWNER_NAME]

Stores sensitive API keys in macOS Keychain instead of plaintext config.json.
Falls back to config.json values if a key isn't in Keychain yet.

Usage (in jarvis.py or any plugin):
    from simon_keychain import get_secret, set_secret

    key = get_secret("ollama_cloud_key")   # reads from Keychain
    set_secret("ollama_cloud_key", "abc")  # writes to Keychain

CLI (one-time migration):
    python3.11 simon_keychain.py --migrate   # moves keys from config.json to Keychain
    python3.11 simon_keychain.py --list      # show what's stored
    python3.11 simon_keychain.py --verify    # confirm all keys are readable
"""

import json
import subprocess
import sys
from pathlib import Path

KEYCHAIN_SERVICE = "simon-assistant"
CONFIG_PATH = Path(__file__).parent / "config.json"

# Keys to manage in Keychain — name in config.json → human label
MANAGED_KEYS = {
    "ollama_cloud_key": "Ollama Cloud API Key",
    "hq_api_key":       "simon-hq API Key",
}


def get_secret(key_name: str, fallback: str = "") -> str:
    """Read a secret from macOS Keychain. Falls back to config.json if not found."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-a", KEYCHAIN_SERVICE,
             "-s", key_name,
             "-w"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            value = result.stdout.strip()
            if value:
                return value
    except Exception:
        pass
    # Fall back to config.json
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        return cfg.get(key_name, fallback)
    except Exception:
        return fallback


def set_secret(key_name: str, value: str) -> bool:
    """Store a secret in macOS Keychain. Updates if it already exists."""
    # Delete existing entry first (update = delete + add)
    subprocess.run(
        ["security", "delete-generic-password",
         "-a", KEYCHAIN_SERVICE, "-s", key_name],
        capture_output=True
    )
    result = subprocess.run(
        ["security", "add-generic-password",
         "-a", KEYCHAIN_SERVICE,
         "-s", key_name,
         "-w", value,
         "-U"],
        capture_output=True, text=True, timeout=5
    )
    return result.returncode == 0


def migrate_from_config() -> dict:
    """One-time migration: move keys from config.json to Keychain."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except Exception as e:
        return {"error": f"Cannot read config.json: {e}"}

    results = {}
    for key_name, label in MANAGED_KEYS.items():
        value = cfg.get(key_name, "")
        if not value or value.startswith("YOUR_") or value == "simon-hq-key-changeme":
            results[key_name] = "skipped (empty or placeholder)"
            continue
        if set_secret(key_name, value):
            results[key_name] = f"✅ migrated to Keychain ({label})"
        else:
            results[key_name] = f"❌ failed to store in Keychain"

    return results


def verify_keys() -> dict:
    """Check that all managed keys are readable from Keychain."""
    results = {}
    for key_name, label in MANAGED_KEYS.items():
        value = get_secret(key_name)
        if value and not value.startswith("YOUR_"):
            results[key_name] = f"✅ found ({len(value)} chars) — {label}"
        else:
            results[key_name] = f"⚠️  not in Keychain — using config.json value"
    return results


def list_keychain_keys() -> list:
    """List all SIMON keys stored in Keychain."""
    result = subprocess.run(
        ["security", "dump-keychain"],
        capture_output=True, text=True
    )
    lines = result.stdout.splitlines()
    keys = []
    for i, line in enumerate(lines):
        if f'"svce"<blob>="{KEYCHAIN_SERVICE}"' in line or f'svce"<blob>="{KEYCHAIN_SERVICE}' in line:
            # Find the account line nearby
            for j in range(max(0, i-5), min(len(lines), i+5)):
                if '"acct"' in lines[j]:
                    acct = lines[j].split("=")[-1].strip().strip('"')
                    keys.append(acct)
                    break
    return keys


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="S.I.M.O.N. Keychain Manager")
    parser.add_argument("--migrate", action="store_true", help="Migrate keys from config.json to Keychain")
    parser.add_argument("--verify",  action="store_true", help="Verify all keys are readable")
    parser.add_argument("--list",    action="store_true", help="List keys stored in Keychain")
    parser.add_argument("--get",     metavar="KEY",       help="Read a specific key value")
    parser.add_argument("--set",     nargs=2, metavar=("KEY", "VALUE"), help="Store a key value")
    args = parser.parse_args()

    if args.migrate:
        print("\nMigrating keys from config.json to macOS Keychain...\n")
        results = migrate_from_config()
        for k, v in results.items():
            print(f"  {k}: {v}")
        print("\nDone. Run --verify to confirm.\n")

    elif args.verify:
        print("\nVerifying Keychain keys...\n")
        results = verify_keys()
        for k, v in results.items():
            print(f"  {v}")
        print()

    elif args.list:
        keys = list_keychain_keys()
        print(f"\nSIMON keys in Keychain: {keys}\n")

    elif args.get:
        val = get_secret(args.get)
        print(f"{args.get} = {val[:8]}...{val[-4:]}" if len(val) > 12 else f"{args.get} = {val}")

    elif args.set:
        ok = set_secret(args.set[0], args.set[1])
        print("✅ Stored" if ok else "❌ Failed")

    else:
        parser.print_help()

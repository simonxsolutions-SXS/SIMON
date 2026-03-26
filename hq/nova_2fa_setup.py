#!/usr/bin/env python3
"""
nova_2fa_setup.py — Simon-X Solutions / NOVA
First-run 2FA setup wizard.

Run this ONCE on simon-hq to:
  1. Generate a TOTP secret
  2. Display QR code for Google Authenticator
  3. Verify you scanned it correctly with a live token test
  4. Save the secret to nova_config.json

Usage:
    python3 nova_2fa_setup.py

Options:
    --reset     Overwrite existing secret (re-enroll Google Authenticator)
    --status    Show current 2FA configuration status
    --test      Just test a token against the existing secret
"""

import sys
import os
import json
import argparse
from pathlib import Path

# Allow running from any directory
sys.path.insert(0, str(Path(__file__).parent))

from nova_2fa import TwoFactorAuth, get_2fa_status, ADMIN_ACTIONS

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║          NOVA Admin 2FA Setup — Simon-X Solutions           ║
║          TOTP / Google Authenticator enrollment             ║
╚══════════════════════════════════════════════════════════════╝
"""


def print_status():
    status = get_2fa_status()
    print(BANNER)
    print("  Current 2FA Status:")
    print(f"    Configured     : {'✅ YES' if status['configured'] else '❌ NOT SET UP'}")
    print(f"    Algorithm      : {status['algorithm']}")
    print(f"    Token period   : {status['period_seconds']}s")
    print(f"    Digits         : {status['digits']}")
    print(f"    Admin actions  : {status['admin_actions_protected']} protected")
    print(f"    Compatible     : {', '.join(status['compatible_apps'])}")
    print()
    if status['configured']:
        print("  Protected admin operations:")
        for action in sorted(ADMIN_ACTIONS):
            print(f"    • {action}")
    print()


def run_setup(reset: bool = False):
    print(BANNER)
    auth = TwoFactorAuth()

    if auth.is_configured() and not reset:
        print("  ✅ 2FA is already configured.")
        print("     Use --reset to generate a new secret (requires re-scanning QR).")
        print("     Use --test to verify your current setup.")
        print("     Use --status to see protected actions.")
        return

    if reset and auth.is_configured():
        confirm = input("  ⚠️  This will REPLACE your existing secret. You must re-scan QR in Google Authenticator.\n  Type 'yes' to continue: ").strip().lower()
        if confirm != "yes":
            print("  Aborted.")
            return

    print("  Generating new TOTP secret...")
    secret = auth.generate_secret()
    print(f"  ✅ Secret generated.\n")

    # Display setup info
    auth.print_setup_instructions()

    # Install qrcode if missing
    try:
        import qrcode
    except ImportError:
        print("  💡 For QR display in terminal, install: pip install qrcode --break-system-packages")
        print("     Or paste the OTPAuth URI above into: https://qr.io\n")

    # Verification loop
    print("  Now open Google Authenticator, scan the QR (or enter the key manually),")
    print("  then enter the 6-digit code to confirm enrollment.\n")

    attempts = 0
    while attempts < 5:
        try:
            token = input("  Enter 6-digit code from Google Authenticator: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n  Setup cancelled.")
            return

        if auth.verify(token):
            print("\n  ✅ Token verified! 2FA is now active.")
            print("     NOVA will require Google Authenticator for all admin operations.")
            print(f"\n  Secret backed up in: nova_config.json ({os.environ.get('NOVA_CONFIG', '/home/simon-hq/simon-hq/nova_config.json')})")
            print("  Keep this file secure — chmod 600 is enforced automatically.\n")
            _print_next_steps()
            return
        else:
            attempts += 1
            remaining = 5 - attempts
            print(f"  ❌ Invalid token. {remaining} attempt(s) remaining. Wait for the code to refresh if it just changed.")

    print("\n  ❌ Too many failed attempts. Run the script again to retry.")


def run_test():
    print(BANNER)
    auth = TwoFactorAuth()

    if not auth.is_configured():
        print("  ❌ 2FA is not configured. Run nova_2fa_setup.py first.")
        return

    print("  Testing your current 2FA configuration.")
    print("  Enter the 6-digit code from Google Authenticator:\n")

    try:
        token = input("  Code: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled.")
        return

    if auth.verify(token):
        print("  ✅ Token valid — 2FA is working correctly.")
    else:
        print("  ❌ Token invalid. Possible causes:")
        print("     • Wrong account selected in Google Authenticator")
        print("     • System clock drift on simon-hq (run: sudo ntpdate pool.ntp.org)")
        print("     • Token expired — try again immediately after refresh")


def _print_next_steps():
    print("  ─── Next steps ───────────────────────────────────────────────")
    print("  1. Restart nova-mcpo:  sudo systemctl restart nova-mcpo")
    print("  2. Admin MCP tools now require a 'totp_token' parameter")
    print("  3. In NOVA chat, admin commands will prompt: 'Enter 2FA code:'")
    print("  4. HUD admin panel will show a 2FA modal before sensitive actions")
    print("  ──────────────────────────────────────────────────────────────\n")


def main():
    parser = argparse.ArgumentParser(
        description="NOVA 2FA Setup — Google Authenticator enrollment for admin operations"
    )
    parser.add_argument("--reset", action="store_true", help="Replace existing secret")
    parser.add_argument("--status", action="store_true", help="Show 2FA status")
    parser.add_argument("--test", action="store_true", help="Test a token against existing secret")
    args = parser.parse_args()

    if args.status:
        print_status()
    elif args.test:
        run_test()
    else:
        run_setup(reset=args.reset)


if __name__ == "__main__":
    main()

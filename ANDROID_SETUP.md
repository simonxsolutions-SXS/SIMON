# S.I.M.O.N. Android Setup Guide
**Simon-X Solutions | [OWNER_NAME]**
**One-time setup — takes about 5 minutes**

---

## What You'll Get After Setup

SIMON will be able to do all of this by voice:

- "Simon, read my text messages"
- "Simon, send a text to Mom saying I'll be there at 6"
- "Simon, what notifications do I have on my phone?"
- "Simon, show me my missed calls"
- "Simon, call 555-867-5309"
- "Simon, open YouTube on my phone"
- "Simon, take a screenshot of my phone"
- "Simon, where is my phone?"
- "Simon, what's my phone battery?"
- "Simon, search my contacts for John"

---

## Step 1 — Enable Developer Options on Your Android

1. Open **Settings**
2. Tap **About Phone**
3. Find **Build Number** (usually at the bottom)
4. Tap it **7 times rapidly**
5. You'll see: *"You are now a developer!"*

---

## Step 2 — Enable Wireless Debugging

1. Go back to **Settings → Developer Options** (now visible)
2. Scroll down and turn on **USB Debugging**
3. Scroll down and turn on **Wireless Debugging**
4. Tap **Wireless Debugging** to open it
5. Note the **IP address and port** shown (e.g. `192.168.1.45:39427`)
   - This IP address is what you need for config.json
   - **This port is the pairing port** — different from 5555

---

## Step 3 — Pair Your Phone with Mac (One Time Only)

On your phone, inside Wireless Debugging, tap **"Pair device with pairing code"**.
You'll see an IP:PORT and a 6-digit code.

On your Mac, open Terminal and run:
```bash
adb pair <PAIRING_IP>:<PAIRING_PORT>
# Example: adb pair 192.168.1.45:39427
```
Enter the 6-digit code when prompted.

> **Note:** If `adb` is not installed, run: `brew install android-platform-tools`

---

## Step 4 — Update config.json

Open `config.json` in your jarvis folder and fill in the `adb_host`:

```json
"android": {
  "enabled": true,
  "adb_host": "192.168.1.45",   ← put your phone's IP here (without port)
  "adb_port": 5555,
  "device_name": "Your Phone"
}
```

Use the **IP address from Step 2** (without the `:PORT` part — just the IP).

---

## Step 5 — Connect and Test

Restart SIMON (`./restart_simon.sh`), then say:

> "Simon, connect to my phone"

You should hear: *"Connected to Your Phone at 192.168.1.45:5555"*

Then test:
> "Simon, what's my phone battery?"
> "Simon, read my last 5 text messages"

---

## Staying Connected

- **Same WiFi = automatic.** As long as your phone and Mac are on the same network, SIMON reconnects automatically.
- **Different networks:** SIMON will report "Cannot reach device" and the Android tools won't work until you're back on the same WiFi.
- **After phone restart:** Wireless Debugging may need to be re-enabled. This is an Android security feature. Consider disabling screen lock timeout in Developer Options if you want it always available.

---

## Troubleshooting

**"adb: command not found"**
```bash
brew install android-platform-tools
```

**"Cannot connect" even on same WiFi**
- Open Settings → Developer Options → Wireless Debugging — make sure it's still ON
- Your phone's IP may have changed. Check it in Settings → About Phone → Status → IP Address
- Update config.json with the new IP and say "Simon, connect to my phone"

**"Device unauthorized"**
- On your phone, look for an ADB authorization dialog and tap "Allow"
- If you don't see it, run: `adb disconnect && adb connect <IP>:5555`

**SMS send not working**
- The `service call isms` method requires some permissions. If it fails, SIMON falls back to opening the SMS compose screen on your phone for you to tap Send.

---

## Privacy Note

All ADB communication happens **locally on your network** — no data leaves your home/office.
SMS content is never logged to disk by SIMON unless you explicitly ask it to remember something.
The android_screenshot tool saves screenshots to your jarvis folder (you can delete them anytime).

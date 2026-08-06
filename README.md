# Pi 5 WiFi Impairment Device

A Raspberry Pi 5 that sits between a real internet connection and a test WiFi network and lets you inject controllable network impairment — latency, jitter, packet loss, bandwidth caps, reordering, and corruption — from a web UI or REST API.

**Use it to test how client devices and apps behave on degraded WiFi** — satellite links, congested public hotspots, 3G-class connections, or lossy environments — without leaving your desk or faking it in software.

---

## How It Works

The Pi bridges your real internet connection to a dedicated test WiFi network. All traffic between the test devices and the internet passes through the Pi, where Linux's `tc` (traffic control) subsystem applies impairment in both directions before forwarding it.

```
┌──────────────┐        ┌──────────────────────────────────────────┐
│              │        │  Raspberry Pi 5                          │
│   Internet   │──eth0──│                                          │
│  (upstream   │        │  NAT (nftables)                          │
│   router)    │        │  ↕ tc/netem shaping  ←── web UI / API   │
│              │        │                                          │
└──────────────┘        └──────────────────┬───────────────────────┘
                                           │ wlan0 (AP mode)
                                   ┌───────┴───────┐
                                   │  Test devices │
                                   │  (phone, laptop, etc.)
                                   └───────────────┘
```

**Traffic shaping is bidirectional and asymmetric:**
- **Download** (internet → test device): shaped on `wlan0` egress
- **Upload** (test device → internet): shaped on an `ifb0` virtual interface via ingress redirect

This lets you simulate real-world asymmetric conditions like satellite (high latency both ways, more bandwidth down than up) or congested public WiFi (bursty loss in both directions, rate-limited upload).

### What gets shaped

All impairments can be set independently and combined:

| Parameter | What it does |
|---|---|
| **Latency** | Fixed delay added to every packet (ms) |
| **Jitter** | Variation in that delay, distributed normally (ms) |
| **Packet loss** | Random packet drop rate (%) |
| **Duplication** | Percentage of packets duplicated |
| **Corruption** | Percentage of packets with a random bit flipped |
| **Reorder** | Percentage of packets sent out of order (requires latency > 0 to be observable) |
| **Download cap** | Max bandwidth from internet to test devices (kbps; 0 = unlimited) |
| **Upload cap** | Max bandwidth from test devices to internet (kbps; 0 = unlimited) |

All parameters can be stacked simultaneously: e.g. 200ms latency + 2% loss + 5 Mbps cap all at once.

---

## Hardware Requirements

| Component | Notes |
|---|---|
| Raspberry Pi 5 | Pi 4 also works, but Pi 5 is recommended |
| Pi OS Bookworm (64-bit) | Lite image is sufficient; Desktop image also works |
| Ethernet uplink | Built-in RJ45 or a USB-C → Ethernet adapter for `eth0` |
| Onboard WiFi | Used as the AP (`wlan0`); 2.4GHz is the safe default |
| PCIe M.2 WiFi HAT (optional) | e.g. ZDE ZP590A — auto-detected and used as the AP when present |

**Important:** The Pi 5's onboard WiFi chip (Broadcom, `brcmfmac`) cannot act as both an AP and a WiFi client at the same time. Your internet uplink **must** be Ethernet (`eth0`), not WiFi. If you later need WiFi as the uplink too, a second USB WiFi dongle with an AP-capable driver is required.

5GHz AP mode support on `brcmfmac` varies by board revision. If you want to try it, see [Switching to 5GHz](#switching-to-5ghz). The default setup uses 2.4GHz, which works on all Pi 5 units.

### PCIe M.2 WiFi HAT (optional, better AP)

If you fit a PCIe→M.2 E-key HAT such as the **ZDE ZP590A** with an M.2 WiFi module (Intel BE200 WiFi 7, AX210 WiFi 6E, AX200 WiFi 6, or MediaTek MT7922), `setup.sh` will detect it and use it as the access point automatically. This gives you a stronger, dual/tri-band AP than the onboard Broadcom radio. See [Using a PCIe M.2 WiFi HAT](#using-a-pcie-m2-wifi-hat) below.

> **AP-mode caveat:** MediaTek MT7922 has solid AP support. Intel cards (AX200/AX210/BE200) frequently expose **no usable AP mode** in `iwlwifi` — setup detects this and keeps the onboard WiFi rather than configuring an AP that won't start. For a reliable dedicated AP, the MediaTek MT7922 is the safest choice.

---

## Setup

### 1. Flash and boot Pi OS

Download **Pi OS Bookworm Lite (64-bit)** from the official Pi imager. Flash to an SD card, boot the Pi, and enable SSH:

```bash
sudo raspi-config
# → Interface Options → SSH → Enable
```

Also set your country code in `raspi-config → Localisation Options → WLAN Country`. This is required for WiFi to work legally on the correct channels.

### 2. Connect Ethernet

Plug the Pi's `eth0` into a port on your regular router. The Pi will get an IP via DHCP from your router — use that IP to SSH in for the rest of setup.

```bash
ssh pi@<eth0-ip>
```

### 3. Clone the repo

```bash
git clone https://github.com/p0liX69/pi-wifi-impairment-tool.git
cd pi-wifi-impairment-tool
```

### 4. Create and edit `config.env`

```bash
cp config.env.example config.env
nano config.env
```

Minimum required settings:

```bash
WIFI_SSID="MyTestNet"         # SSID for your test WiFi network
WIFI_PASSPHRASE="changeme123" # WPA2 passphrase (min 8 characters)
WIFI_COUNTRY_CODE="US"        # Your 2-letter country code
```

Everything else has sensible defaults (`eth0` as WAN, `wlan0` as AP, `192.168.50.1/24` subnet, port `8080` for the web UI). Change them only if you have a reason to.

`config.env` is gitignored and never committed — it stays only on the Pi.

### 5. Run setup

```bash
sudo bash setup.sh
```

This script is **idempotent** — safe to run multiple times. It:

- Installs `hostapd`, `dnsmasq`, `nftables`, `iproute2`, `python3`
- Detects a PCIe M.2 WiFi HAT (e.g. ZDE ZP590A) and, if present and AP-capable, uses it as the AP — see [Using a PCIe M.2 WiFi HAT](#using-a-pcie-m2-wifi-hat)
- Enables IPv4 forwarding (`net.ipv4.ip_forward=1`)
- Persists the `ifb` kernel module (needed for upload shaping)
- Tells NetworkManager to leave `wlan0` alone
- Configures `wlan0` with a static IP via systemd-networkd
- Renders `hostapd.conf`, `dnsmasq.conf`, and `nftables.conf` from templates using your `config.env` values
- Creates a `impair` system user to run the web app unprivileged
- Installs the root helper to `/usr/local/sbin/impair-helper` with a narrow `sudo` entry
- Installs the Flask app to `/opt/wifi-impair/` in a virtualenv
- Enables and starts all systemd services

When it finishes it prints a summary:

```
================================================================
 Setup complete!

  WiFi SSID  : MyTestNet
  AP IP      : 192.168.50.1
  Web UI     : http://192.168.50.1:8080

  Connect a device to 'MyTestNet' and browse to:
  http://192.168.50.1:8080
================================================================
```

### 6. Connect and open the web UI

Connect any device to your new WiFi network (using the SSID and passphrase from `config.env`). Open a browser and go to:

```
http://192.168.50.1:8080
```

You can access the web UI from any device on the test WiFi — including the devices you're testing.

---

## Using the Web UI

The UI is a single page designed to work well on both desktop and mobile browsers.

**Profiles row** — one-click presets across the top. Clicking a profile loads all its values into the sliders below. The selected profile is highlighted.

**Active Impairment** — shows what's currently in effect. Updates automatically every 5 seconds.

**Parameter sliders:**
- Latency (0–2000 ms)
- Jitter (0–500 ms, normal distribution)
- Packet Loss (0–100%)
- Duplicate (0–100%)
- Corrupt (0–100%)
- Reorder (0–100%; only meaningful when latency > 0)

**Bandwidth caps** — numeric fields for download and upload rate in kbps. `0` means unlimited.

**Apply** — sends the current slider/field values to the Pi. Takes effect immediately.

**Clear** — removes all impairment and returns to full pass-through.

### Tip: test from the device you're impairing

Since the web UI is accessible from the test WiFi subnet, you can open it on the same phone or laptop you're testing. Apply impairment, switch to your app, notice the degraded behavior, switch back to the UI and clear it — all from the device under test.

---

## Built-in Profiles

| Profile | Latency | Jitter | Loss | Rate (↓/↑) | Simulates |
|---|---|---|---|---|---|
| **Clean** | 0 ms | 0 ms | 0% | unlimited | Full pass-through |
| **Congested WiFi** | 40 ms | 10 ms | 1% | 10/2 Mbps | Busy coffee shop or airport WiFi |
| **3G Mobile** | 150 ms | 30 ms | 2% | 1.5/0.5 Mbps | Older cellular, poor indoor signal |
| **Satellite** | 600 ms | 20 ms | 0.5% | 25/3 Mbps | Geostationary satellite link |
| **Lossy WiFi** | 20 ms | 50 ms | 7% | 5/1 Mbps | Weak signal, marginal coverage |

---

## Adding Custom Profiles

**Option 1 — API (from the web UI device):**

```bash
curl -X POST http://192.168.50.1:8080/profiles \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "flaky_hotel",
    "label": "Flaky Hotel WiFi",
    "description": "The WiFi at that conference hotel",
    "latency_ms": 60,
    "jitter_ms": 80,
    "loss_pct": 4.0,
    "rate_down_kbps": 3000,
    "rate_up_kbps": 500
  }'
```

Saved profiles appear immediately in the web UI profile row and are stored in `custom_profiles.json` on the Pi (gitignored).

**Option 2 — Edit `profiles.json` directly:**

Add an entry to `profiles.json` on the Pi and restart the service:

```bash
sudo systemctl restart wifi-impair
```

---

## Changing SSID or Password

1. Edit `config.env` on the Pi.
2. Re-run `sudo bash setup.sh` — it regenerates the configs and restarts services.
3. Reconnect test devices with the new credentials.

---

## Switching to 5GHz

5GHz AP mode on the Pi 5's onboard WiFi works on some board revisions but not all. To try it:

1. Edit `/etc/hostapd/hostapd.conf`:
   ```
   hw_mode=a
   channel=36       # or 40, 44, 48 — check what's clear in your environment
   ieee80211ac=1
   ieee80211n=1
   ```
2. Restart hostapd: `sudo systemctl restart hostapd`
3. If the AP doesn't come up (check `journalctl -u hostapd`), revert to `hw_mode=g` / `channel=6`.

If 5GHz proves unreliable, a USB WiFi dongle with proper AP-mode support (e.g. one with the `mt76` or `ath9k_htc` driver) is the reliable path.

---

## Using a PCIe M.2 WiFi HAT

A PCIe→M.2 E-key adapter HAT (such as the **ZDE ZP590A**) lets you plug a modern M.2 WiFi module into the Pi 5's PCIe port and use it as the access point instead of the onboard Broadcom radio — giving you WiFi 6/6E/7, more concurrent clients, and better throughput.

**Supported modules** (auto-detected by chipset):

| Module | Driver | Firmware package | AP mode |
|---|---|---|---|
| MediaTek MT7922 | `mt7921e` | `firmware-mediatek` / `firmware-misc-nonfree` | Good — recommended |
| Intel AX200 / AX210 / BE200 | `iwlwifi` | `firmware-iwlwifi` | Often unsupported |

### How it works

With `PCIE_WIFI="auto"` (the default) in `config.env`, `setup.sh` will:

1. Enable the Pi 5 PCIe port by adding `dtparam=pciex1` to `config.txt` (needs a reboot the first time).
2. Detect a wireless card on the PCIe bus via `lspci`.
3. Install the matching firmware package for the chipset.
4. Confirm the card advertises **AP mode** (`iw phy … info`).
5. If all of the above pass, use the card's interface (e.g. `wlan1`) as the AP — otherwise fall back to the onboard `wlan0`.

### First run (enabling PCIe)

On a fresh Pi where PCIe has never been enabled, the card won't be visible until after a reboot. The first `sudo bash setup.sh` enables PCIe and prints:

```
NOTE: The Pi 5 PCIe port was just enabled. Reboot and re-run
      setup.sh so the M.2 WiFi card is detected and used as the AP.
```

Reboot, then run `sudo bash setup.sh` again. This time the card is detected and configured as the AP. Setup prints the interface it chose:

```
[OK]    Using PCIe WiFi card 'wlan1' (MediaTek (mt7921e)) as the access point
```

### Options in `config.env`

```bash
PCIE_WIFI="auto"    # auto = detect & use the M.2 card; off = always use onboard WiFi
PCIE_WIFI_GEN=2     # PCIe link gen: 2 (safe) or 3 (faster, uncertified — try if the card is flaky at Gen2)
```

### Troubleshooting

- **Card not detected after reboot:** confirm it enumerated with `lspci` — you should see a `Network controller`. If not, reseat the module and check the HAT's PCIe FPC cable orientation.
- **Detected but no interface:** the driver couldn't load firmware. Check `dmesg | grep -Ei 'iwlwifi|mt7921|firmware'`. The BE200 in particular needs a recent kernel (6.6+) and up-to-date `firmware-iwlwifi`.
- **`does not advertise AP mode`:** the card's driver has no AP support (common on Intel). Use a MediaTek MT7922, or set `PCIE_WIFI="off"` to stay on the onboard radio.

---

## Service Management

```bash
# Flask web app
sudo systemctl status wifi-impair
sudo systemctl restart wifi-impair
sudo journalctl -u wifi-impair -f

# WiFi access point
sudo systemctl status hostapd
sudo journalctl -u hostapd -f

# DHCP server
sudo systemctl status dnsmasq
sudo journalctl -u dnsmasq -f

# NAT / firewall
sudo systemctl status nftables
sudo nft list ruleset
```

---

## Troubleshooting

**AP not broadcasting / devices can't see the SSID**

Check hostapd:
```bash
sudo journalctl -u hostapd --no-pager | tail -30
```
Common causes: wrong `country_code` in `config.env`, 5GHz channel not supported by your board, or the `wlan0` interface not fully up before hostapd starts (try `sudo systemctl restart hostapd`).

**Devices connect but have no internet**

Check nftables rules and IP forwarding:
```bash
sudo nft list ruleset
cat /proc/sys/net/ipv4/ip_forward   # should be 1
sudo systemctl restart nftables
```

**Upload shaping not working after reboot**

The `ifb` module may not have loaded:
```bash
lsmod | grep ifb
sudo modprobe ifb numifbs=1
```

If it's missing from lsmod, check `/etc/modules-load.d/wifi-impair.conf` contains `ifb` — setup.sh creates this, but some Pi OS images have a non-standard modules path.

**Web UI not reachable**

Confirm the service is running and listening:
```bash
sudo systemctl status wifi-impair
ss -tlnp | grep 8080
```

Also confirm nftables is allowing port 8080 from `wlan0`:
```bash
sudo nft list chain ip filter input
```

**Stuck tc state / weird impairment after restart**

The Flask service resets state to clean on startup, but if tc qdiscs were left over from a previous session:
```bash
sudo /usr/local/sbin/impair-helper clear
```

---

## Emergency Manual Reset

If everything breaks and you need to clear tc state by hand:

```bash
sudo tc qdisc del dev wlan0 root 2>/dev/null
sudo tc qdisc del dev wlan0 ingress 2>/dev/null
sudo tc qdisc del dev ifb0 root 2>/dev/null
sudo ip link set ifb0 down 2>/dev/null
```

---

## API Reference

All responses are JSON with shape `{ "ok": true, "data": ... }` or `{ "ok": false, "error": "..." }`.

### `GET /status`

Current impairment state.

```json
{
  "ok": true,
  "data": {
    "active_profile": "congested_wifi",
    "is_clean": false,
    "summary": "40ms latency ±10ms · 1.0% loss · 10000k↓ / 2000k↑ kbps",
    "params": {
      "latency_ms": 40,
      "jitter_ms": 10,
      "loss_pct": 1.0,
      "duplicate_pct": 0.0,
      "corrupt_pct": 0.0,
      "reorder_pct": 0.0,
      "rate_down_kbps": 10000,
      "rate_up_kbps": 2000
    },
    "tc_qdiscs": {
      "wlan0": "qdisc htb 1: root ...",
      "ifb0": "qdisc htb 1: root ..."
    }
  }
}
```

### `GET /profiles`

List all built-in and saved custom profiles.

### `POST /apply`

Apply impairment. All fields optional, default to 0.

```json
{
  "latency_ms": 150,
  "jitter_ms": 30,
  "loss_pct": 2.0,
  "duplicate_pct": 0.0,
  "corrupt_pct": 0.0,
  "reorder_pct": 0.0,
  "rate_down_kbps": 1500,
  "rate_up_kbps": 500,
  "profile_name": "3g_mobile"
}
```

- All numeric values are validated and clamped server-side.
- If all values are 0, this is equivalent to `/clear`.
- `profile_name` is optional metadata for the status display.

### `POST /clear`

Remove all impairment, return to clean pass-through. No request body needed.

### `POST /profiles`

Save a custom profile. Required fields: `name` (alphanumeric + underscores/hyphens), `label`. All impairment fields are optional.

### `GET /clients`

List devices currently connected to the AP, parsed from dnsmasq's lease file.

```json
{
  "ok": true,
  "data": [
    { "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.50.101", "hostname": "iphone", "expires": 1722800000 }
  ]
}
```

---

## Architecture & Security

### Why a separate root helper?

The Flask web app runs as the unprivileged `impair` system user. `tc qdisc` commands require root. Rather than running Flask as root, a small helper script (`/usr/local/sbin/impair-helper`) is owned by root and invoked via a narrow `sudo` entry:

```
impair ALL=(root) NOPASSWD: /usr/local/sbin/impair-helper
```

The helper only accepts specific subcommands (`apply`, `clear`) with named numeric arguments — no raw shell strings are ever passed. Flask validates and clamps all inputs before calling the helper, and the helper validates again as defense-in-depth.

### Security posture

- Flask web app: runs as non-root `impair` user
- `tc`/`ip` commands: only via `/usr/local/sbin/impair-helper` via sudo
- Input validation: validator clamps all values to safe ranges before the helper is called
- Network access: nftables blocks all inbound from the AP subnet except port 8080; SSH is only reachable via `eth0`
- Secrets: `config.env` (WiFi passphrase) is gitignored and never committed; generated configs that contain the passphrase (`/etc/hostapd/hostapd.conf`) are chmod 600
- Web UI: no authentication — assume trusted test network. If you ever expose this beyond the local subnet, add auth

### File map

```
.
├── setup.sh                      # Idempotent provisioning script
├── config.env.example            # Copy to config.env and fill in
├── profiles.json                 # Built-in preset profiles
│
├── app.py                        # Flask REST API (runs as 'impair' user)
├── impair/
│   └── validator.py              # Input validation — frozen dataclass, ValidationError
├── helper/
│   └── impair_helper.py          # Root helper — tc/ip commands (installed to /usr/local/sbin/)
│
├── templates/
│   └── index.html                # Single-page web UI (vanilla JS, no build step)
│
├── config/
│   ├── hostapd.conf.template     # AP config (rendered by setup.sh)
│   ├── dnsmasq.conf              # DHCP config template
│   └── nftables.conf             # NAT + filter rules
│
├── systemd/
│   └── wifi-impair.service       # Flask app service unit
│
└── tests/
    ├── test_validator.py         # Unit tests for input validation
    └── test_app.py               # Flask route tests (subprocess mocked)
```

---

## Development & Testing

Tests can run on any machine — no Pi hardware required. Subprocess calls to the helper are mocked.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v
```

To run the Flask app locally for UI development (impairment won't actually apply without the helper):

```bash
source venv/bin/activate
python app.py
# → open http://localhost:8080
```

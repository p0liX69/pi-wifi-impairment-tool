# Pi 5 WiFi Impairment Device

A Raspberry Pi 5 that sits between a real internet connection and a WiFi test network, letting you inject controllable network impairment — latency, jitter, packet loss, bandwidth caps, reordering, corruption — via a web UI.

```
[Internet] --Ethernet--> [Pi 5 eth0]
                               |
                    NAT + tc/netem shaping
                               |
                         [Pi 5 wlan0 AP]
                               |
                        [Test devices via WiFi]
```

## Hardware

- Raspberry Pi 5
- Pi OS Bookworm (64-bit Lite recommended)
- Ethernet uplink on `eth0` (built-in or USB-C adapter)
- Onboard WiFi `wlan0` in AP mode (2.4GHz; 5GHz may work depending on board revision)

## First-Boot Setup

1. **Flash Pi OS Bookworm Lite** and boot the Pi. Enable SSH via `raspi-config`.

2. **Clone this repo on the Pi:**
   ```bash
   git clone https://github.com/toddshipway/pi-wifi-impairment-tool.git
   cd pi-wifi-impairment-tool
   ```

3. **Create `config.env`** from the example and edit it:
   ```bash
   cp config.env.example config.env
   nano config.env
   ```
   Set your `WIFI_SSID`, `WIFI_PASSPHRASE`, and `WIFI_COUNTRY_CODE` at minimum.

4. **Run the provisioning script:**
   ```bash
   sudo bash setup.sh
   ```
   This installs packages, configures hostapd/dnsmasq/nftables, creates the `impair` service user, installs the helper with sudo access, and starts all services.

5. **Connect a device** to the WiFi SSID you configured. Open a browser on that device and go to:
   ```
   http://192.168.50.1:8080
   ```

## Changing SSID or Password

1. Edit `config.env` on the Pi.
2. Re-run `sudo bash setup.sh` — it's idempotent.
3. Devices will need to reconnect with the new credentials.

## Adding a Custom Profile

**Via the web UI:** Click a profile to load it as a starting point, tweak the sliders, then use the API directly:
```bash
curl -X POST http://192.168.50.1:8080/profiles \
  -H 'Content-Type: application/json' \
  -d '{"name":"my_scenario","label":"My Scenario","latency_ms":80,"loss_pct":3.0}'
```

**Via `profiles.json`:** Add a new entry to `profiles.json`, then restart the service:
```bash
sudo systemctl restart wifi-impair
```

## Service Management

```bash
sudo systemctl status wifi-impair     # App status
sudo systemctl restart wifi-impair    # Restart app

sudo systemctl status hostapd         # AP status
sudo systemctl status dnsmasq         # DHCP status
sudo systemctl status nftables        # NAT status

sudo journalctl -u wifi-impair -f     # Live app logs
sudo journalctl -u hostapd -f         # AP logs
```

## Emergency Reset (stuck tc state)

SSH into the Pi and run:
```bash
sudo /usr/local/sbin/impair-helper clear
```

Or manually:
```bash
sudo tc qdisc del dev wlan0 root
sudo tc qdisc del dev wlan0 ingress
sudo tc qdisc del dev ifb0 root
sudo ip link set ifb0 down
```

## Security Notes

- The Flask app runs as the unprivileged `impair` user.
- All `tc`/`ip` commands go through `/usr/local/sbin/impair-helper` via a narrow `sudo` entry — the web app never gets a root shell.
- All numeric inputs are validated and clamped server-side before reaching the helper.
- The web UI port (8080) is accessible from the AP (`wlan0`) subnet only; SSH/management is via `eth0`.
- `config.env` (containing the WiFi password) is gitignored — never committed.
- Web UI has no authentication (trusted local test network). If you expose this beyond a local subnet, add auth in v2.

## Architecture

| File | Role |
|---|---|
| `app.py` | Flask REST API — validates input, delegates tc commands to helper |
| `impair/validator.py` | Input validation and clamping |
| `helper/impair_helper.py` | Root-privilege helper — executes actual `tc qdisc` commands |
| `profiles.json` | Built-in preset profiles |
| `custom_profiles.json` | Operator-saved custom profiles (gitignored) |
| `config/hostapd.conf.template` | AP config template (rendered by setup.sh) |
| `config/dnsmasq.conf` | DHCP config template |
| `config/nftables.conf` | NAT + forward filter rules |
| `systemd/wifi-impair.service` | systemd unit for the Flask app |
| `setup.sh` | Idempotent provisioning script |

## Development / Testing

Run tests on any machine (no Pi hardware needed):
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v
```

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/status` | GET | Current state, active profile, raw tc qdiscs |
| `/profiles` | GET | List built-in + custom profiles |
| `/apply` | POST | Apply impairment parameters |
| `/clear` | POST | Reset to clean pass-through |
| `/profiles` | POST | Save a new custom profile |
| `/clients` | GET | Currently connected WiFi clients (from dnsmasq leases) |

### POST /apply body
```json
{
  "latency_ms": 100,
  "jitter_ms": 10,
  "loss_pct": 1.0,
  "duplicate_pct": 0.0,
  "corrupt_pct": 0.0,
  "reorder_pct": 0.0,
  "rate_down_kbps": 5000,
  "rate_up_kbps": 1000,
  "profile_name": "congested_wifi"
}
```
All fields are optional and default to 0. Rate values of 0 mean unlimited.

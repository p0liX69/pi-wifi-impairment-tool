#!/usr/bin/env bash
# setup.sh — Idempotent provisioning for the Pi 5 WiFi impairment device.
# Run as root: sudo bash setup.sh
# Re-running is safe.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/wifi-impair"
HELPER_BIN="/usr/local/sbin/impair-helper"
SUDOERS_FILE="/etc/sudoers.d/wifi-impair"
SERVICE_USER="impair"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()    { echo "[INFO]  $*"; }
success() { echo "[OK]    $*"; }
warn()    { echo "[WARN]  $*" >&2; }
die()     { echo "[ERROR] $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash setup.sh"

CONFIG_ENV="$REPO_DIR/config.env"
[[ -f "$CONFIG_ENV" ]] || die "config.env not found. Copy config.env.example → config.env and fill in your values."

# shellcheck source=/dev/null
source "$CONFIG_ENV"

: "${WIFI_SSID:?config.env: WIFI_SSID is required}"
: "${WIFI_PASSPHRASE:?config.env: WIFI_PASSPHRASE is required}"
: "${WIFI_CHANNEL:=6}"
: "${WIFI_COUNTRY_CODE:=US}"
: "${WAN_IFACE:=eth0}"
: "${AP_IFACE:=wlan0}"
: "${AP_SUBNET:=192.168.50}"
: "${AP_IP:=192.168.50.1}"
: "${DHCP_RANGE_START:=192.168.50.100}"
: "${DHCP_RANGE_END:=192.168.50.200}"
: "${CONTROL_PORT:=8080}"

[[ ${#WIFI_PASSPHRASE} -ge 8 ]] || die "WIFI_PASSPHRASE must be at least 8 characters"

info "Config loaded: SSID=$WIFI_SSID, AP=$AP_IFACE, WAN=$WAN_IFACE, IP=$AP_IP"

# ---------------------------------------------------------------------------
# 1. Packages
# ---------------------------------------------------------------------------

info "Installing packages…"
apt-get update -qq
apt-get install -y --no-install-recommends \
    hostapd \
    dnsmasq \
    nftables \
    iproute2 \
    kmod \
    python3 \
    python3-pip \
    python3-venv \
    wireless-tools

success "Packages installed"

# ---------------------------------------------------------------------------
# 2. System user for Flask app
# ---------------------------------------------------------------------------

if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    info "Created system user: $SERVICE_USER"
else
    info "User '$SERVICE_USER' already exists"
fi

# ---------------------------------------------------------------------------
# 3. Install application files
# ---------------------------------------------------------------------------

info "Installing app to $INSTALL_DIR…"
mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/static"
cp -r "$REPO_DIR/app.py" "$REPO_DIR/profiles.json" "$REPO_DIR/impair" "$REPO_DIR/templates" "$INSTALL_DIR/"
[[ -d "$REPO_DIR/static" ]] && cp -r "$REPO_DIR/static/." "$INSTALL_DIR/static/"

# Create or update custom_profiles.json if not present
[[ -f "$INSTALL_DIR/custom_profiles.json" ]] || echo '[]' > "$INSTALL_DIR/custom_profiles.json"

# Virtual environment
if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --quiet flask

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
chmod 750 "$INSTALL_DIR"

success "App installed"

# ---------------------------------------------------------------------------
# 4. Root helper script
# ---------------------------------------------------------------------------

info "Installing impairment helper to $HELPER_BIN…"
cp "$REPO_DIR/helper/impair_helper.py" "$HELPER_BIN"
chown root:root "$HELPER_BIN"
chmod 755 "$HELPER_BIN"
# Ensure the shebang runs as python3
sed -i '1s|.*|#!/usr/bin/env python3|' "$HELPER_BIN"
success "Helper installed"

# ---------------------------------------------------------------------------
# 5. sudoers entry
# ---------------------------------------------------------------------------

info "Configuring sudoers…"
cat > "$SUDOERS_FILE" <<EOF
# Allows the impair user to run the impairment helper as root (no password prompt).
$SERVICE_USER ALL=(root) NOPASSWD: $HELPER_BIN
EOF
chmod 440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE" || die "sudoers syntax check failed — aborting"
success "sudoers configured"

# ---------------------------------------------------------------------------
# 6. IP forwarding
# ---------------------------------------------------------------------------

info "Enabling IPv4 forwarding…"
if ! grep -qF "net.ipv4.ip_forward=1" /etc/sysctl.d/99-wifi-impair.conf 2>/dev/null; then
    echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-wifi-impair.conf
fi
sysctl -w net.ipv4.ip_forward=1 >/dev/null
success "IPv4 forwarding enabled"

# ---------------------------------------------------------------------------
# 7. ifb module persistence
# ---------------------------------------------------------------------------

info "Persisting ifb kernel module…"
if ! grep -qF "ifb" /etc/modules-load.d/wifi-impair.conf 2>/dev/null; then
    echo "ifb" > /etc/modules-load.d/wifi-impair.conf
fi
modprobe ifb numifbs=1 2>/dev/null || warn "ifb module not loaded — may be missing; ingress shaping may not work until reboot"
success "ifb module configured"

# ---------------------------------------------------------------------------
# 8. Tell NetworkManager to ignore the AP interface
# ---------------------------------------------------------------------------

info "Configuring NetworkManager to unmanage $AP_IFACE…"
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/99-wifi-impair-unmanaged.conf <<EOF
[keyfile]
unmanaged-devices=interface-name:$AP_IFACE
EOF
if systemctl is-active --quiet NetworkManager; then
    systemctl reload NetworkManager || true
fi
success "NetworkManager will not manage $AP_IFACE"

# ---------------------------------------------------------------------------
# 9. Static IP for AP interface via systemd-networkd
# ---------------------------------------------------------------------------

info "Configuring static IP for $AP_IFACE via systemd-networkd…"
systemctl enable --now systemd-networkd >/dev/null

cat > "/etc/systemd/network/20-${AP_IFACE}.network" <<EOF
[Match]
Name=$AP_IFACE

[Network]
Address=$AP_IP/24
EOF

networkctl reload 2>/dev/null || true
success "Static IP $AP_IP/24 configured on $AP_IFACE"

# ---------------------------------------------------------------------------
# 10. hostapd
# ---------------------------------------------------------------------------

info "Configuring hostapd…"
mkdir -p /etc/hostapd

# Render template
sed \
    -e "s|__AP_IFACE__|$AP_IFACE|g" \
    -e "s|__WIFI_SSID__|$WIFI_SSID|g" \
    -e "s|__WIFI_PASSPHRASE__|$WIFI_PASSPHRASE|g" \
    -e "s|__WIFI_CHANNEL__|$WIFI_CHANNEL|g" \
    -e "s|__WIFI_COUNTRY_CODE__|$WIFI_COUNTRY_CODE|g" \
    "$REPO_DIR/config/hostapd.conf.template" \
    > /etc/hostapd/hostapd.conf

chmod 600 /etc/hostapd/hostapd.conf

# Point the hostapd default config at our file
if [[ -f /etc/default/hostapd ]]; then
    sed -i 's|#*DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
fi

systemctl unmask hostapd
systemctl enable hostapd
success "hostapd configured (SSID: $WIFI_SSID)"

# ---------------------------------------------------------------------------
# 11. dnsmasq
# ---------------------------------------------------------------------------

info "Configuring dnsmasq…"

# Disable the global dnsmasq service if systemd-resolved is active, to avoid port 53 conflicts
if systemctl is-active --quiet systemd-resolved; then
    # Only run dnsmasq on the AP interface — avoid conflict with systemd-resolved on lo/eth0
    warn "systemd-resolved is active; dnsmasq will bind only to $AP_IFACE"
fi

mkdir -p /etc/dnsmasq.d
sed \
    -e "s|__AP_IFACE__|$AP_IFACE|g" \
    -e "s|__DHCP_RANGE_START__|$DHCP_RANGE_START|g" \
    -e "s|__DHCP_RANGE_END__|$DHCP_RANGE_END|g" \
    -e "s|__AP_IP__|$AP_IP|g" \
    "$REPO_DIR/config/dnsmasq.conf" \
    > /etc/dnsmasq.d/wifi-impair.conf

systemctl enable dnsmasq
success "dnsmasq configured (range: $DHCP_RANGE_START–$DHCP_RANGE_END)"

# ---------------------------------------------------------------------------
# 12. nftables
# ---------------------------------------------------------------------------

info "Configuring nftables…"
sed \
    -e "s|__WAN_IFACE__|$WAN_IFACE|g" \
    -e "s|__AP_IFACE__|$AP_IFACE|g" \
    -e "s|__AP_SUBNET__|$AP_SUBNET|g" \
    "$REPO_DIR/config/nftables.conf" \
    > /etc/nftables.conf

systemctl enable nftables
nft -f /etc/nftables.conf
success "nftables configured (NAT: $AP_IFACE → $WAN_IFACE)"

# ---------------------------------------------------------------------------
# 13. Flask app systemd service
# ---------------------------------------------------------------------------

info "Installing wifi-impair systemd service…"
cp "$REPO_DIR/systemd/wifi-impair.service" /etc/systemd/system/wifi-impair.service
systemctl daemon-reload
systemctl enable wifi-impair
success "wifi-impair.service installed and enabled"

# ---------------------------------------------------------------------------
# 14. Start / restart services
# ---------------------------------------------------------------------------

info "Starting services…"
for svc in hostapd dnsmasq nftables wifi-impair; do
    systemctl restart "$svc" && success "$svc started" || warn "$svc failed to start — check: journalctl -u $svc"
done

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo ""
echo "================================================================"
echo " Setup complete!"
echo ""
echo "  WiFi SSID  : $WIFI_SSID"
echo "  AP IP      : $AP_IP"
echo "  Web UI     : http://$AP_IP:$CONTROL_PORT"
echo ""
echo "  Connect a device to '$WIFI_SSID' and browse to:"
echo "  http://$AP_IP:$CONTROL_PORT"
echo ""
echo "  To SSH in from WAN: ssh pi@<eth0-ip>"
echo "  Emergency tc reset : sudo /usr/local/sbin/impair-helper clear"
echo "================================================================"

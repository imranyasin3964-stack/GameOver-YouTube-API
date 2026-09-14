#!/usr/bin/env bash
set -e

echo "=========================================================="
echo "🚀 GameOver API - 1-Click RDP + Google Chrome Setup"
echo "=========================================================="

if [ "$EUID" -ne 0 ]; then
  echo "[-] Please run as root: sudo bash install_rdp.sh"
  exit 1
fi

echo "[1/6] Updating package repositories..."
apt update -y

echo "[2/6] Installing XFCE4 Desktop & XRDP..."
DEBIAN_FRONTEND=noninteractive apt install -y xfce4 xfce4-goodies xrdp dbus-x11 x11-xserver-utils wget curl

echo "[3/6] Configuring XRDP Session..."
echo "xfce4-session" > /root/.xsession
adduser xrdp ssl-cert || true

# Prevent black screen on XRDP login
cat << 'EOF' > /etc/xrdp/startwm.sh
#!/bin/sh
if [ -r /etc/default/locale ]; then
  . /etc/default/locale
  export LANG LANGUAGE
fi
unset DBUS_SESSION_BUS_ADDRESS
unset XDG_RUNTIME_DIR
exec startxfce4
EOF
chmod +x /etc/xrdp/startwm.sh

echo "[4/6] Installing Google Chrome..."
wget -q -O /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
dpkg -i /tmp/google-chrome.deb || apt-get install -f -y
rm -f /tmp/google-chrome.deb

# Enable running Chrome as root without keyring popup
if [ -f /opt/google/chrome/google-chrome ]; then
  sed -i 's/exec -a "$0" "$HERE\/chrome" "$@"/exec -a "$0" "$HERE\/chrome" "$@" --no-sandbox --password-store=basic/g' /opt/google/chrome/google-chrome
fi

# Create a Desktop shortcut for Chrome
mkdir -p /root/Desktop
cat << 'EOF' > /root/Desktop/google-chrome.desktop
[Desktop Entry]
Version=1.0
Name=Google Chrome
Comment=Access the Internet
Exec=/usr/bin/google-chrome-stable --no-sandbox --password-store=basic %U
Terminal=false
Icon=google-chrome
Type=Application
Categories=Network;WebBrowser;
EOF
chmod +x /root/Desktop/google-chrome.desktop || true

echo "[5/6] Starting XRDP Service..."
systemctl enable xrdp
systemctl restart xrdp

echo "=========================================================="
echo "✅ RDP + Google Chrome Installation Complete!"
echo "=========================================================="
echo "Next Steps:"
echo "1. Windows PC me 'Remote Desktop Connection' (mstsc) kholein."
echo "2. Computer: [YOUR_VPS_IP] (Port: 3389)"
echo "3. Username: root | Password: [Your VPS root password]"
echo "4. Desktop par Google Chrome kholein aur YouTube.com par account login kar lein!"
echo "=========================================================="

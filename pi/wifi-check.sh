#!/bin/bash
# Berry WiFi Check Script
# Starts a captive portal if no WiFi connection is available
#
# This script is run at boot by the berry-wifi.service
# If WiFi is connected, it exits immediately
# If not, it starts a hotspot "Berry-Setup" where users can configure WiFi

set -e

# Check if we have a WiFi connection
SSID=$(iwgetid -r 2>/dev/null || true)

if [ -n "$SSID" ]; then
    echo "WiFi connected to: $SSID"
    exit 0
fi

echo "No WiFi connection detected"

# Check if wifi-connect is installed
if [ ! -f /usr/local/bin/wifi-connect ]; then
    echo "wifi-connect not installed, skipping captive portal"
    exit 0
fi

echo "Starting WiFi setup portal..."
echo "Connect to 'Berry-Setup' hotspot to configure WiFi"

# Start the captive portal
# - Creates a hotspot named "Berry-Setup"
# - No password required
# - Timeout after 5 minutes of inactivity
# - Listens on port 80 for the captive portal
/usr/local/bin/wifi-connect \
    --portal-ssid "Berry-Setup" \
    --portal-passphrase "" \
    --portal-listening-port 80 \
    --activity-timeout 300

echo "WiFi configured, continuing boot..."


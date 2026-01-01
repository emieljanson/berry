#!/bin/bash
# Berry First-Time Setup Script
# Run this ONCE on a new Raspberry Pi

set -e
echo "🍓 Berry Setup Starting..."
echo ""

# ============================================
# 1. Check/Install Node.js
# ============================================
if ! command -v node &> /dev/null; then
  echo "📦 Installing Node.js..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
else
  echo "✅ Node.js already installed: $(node --version)"
fi

# ============================================
# 2. Check/Install go-librespot
# ============================================
if ! command -v go-librespot &> /dev/null; then
  echo "📦 Installing go-librespot..."
  # Download latest release
  ARCH=$(dpkg --print-architecture)
  LATEST=$(curl -s https://api.github.com/repos/devgianlu/go-librespot/releases/latest | grep "browser_download_url.*linux_${ARCH}" | cut -d '"' -f 4)
  curl -L "$LATEST" -o /tmp/go-librespot.tar.gz
  sudo tar -xzf /tmp/go-librespot.tar.gz -C /usr/local/bin go-librespot
  rm /tmp/go-librespot.tar.gz
  echo "✅ go-librespot installed"
else
  echo "✅ go-librespot already installed"
fi

# ============================================
# 3. Configure Spotify (if not done)
# ============================================
mkdir -p ~/.config/go-librespot

# Create default config if not exists
if [ ! -f ~/.config/go-librespot/config.toml ]; then
  cat > ~/.config/go-librespot/config.toml << 'EOF'
[server]
enabled = true
port = 3678

[player]
device_name = "Berry"
device_type = "speaker"
EOF
fi

# Check if credentials exist in state.json
if [ -f ~/.config/go-librespot/state.json ] && grep -q '"credentials"' ~/.config/go-librespot/state.json; then
  echo "✅ Spotify credentials found"
else
  echo ""
  echo "🎵 Spotify login required!"
  echo "   Opening Spotify app on your phone/computer..."
  echo "   Connect to 'Berry' device to authenticate"
  echo ""
  
  # Kill any existing instance
  pkill -f go-librespot 2>/dev/null || true
  sleep 1
  
  # Start go-librespot in background
  go-librespot --config_dir ~/.config/go-librespot &
  LIBRESPOT_PID=$!
  
  # Wait for credentials (max 2 minutes)
  echo "   Waiting for Spotify connection..."
  for i in {1..120}; do
    if [ -f ~/.config/go-librespot/state.json ] && grep -q '"credentials"' ~/.config/go-librespot/state.json; then
      echo "   ✅ Spotify connected!"
      sleep 2
      break
    fi
    sleep 1
    # Show progress every 10 seconds
    if [ $((i % 10)) -eq 0 ]; then
      echo "   Still waiting... ($i seconds)"
    fi
  done
  
  # Stop go-librespot (will be started by systemd later)
  kill $LIBRESPOT_PID 2>/dev/null || true
  
  # Final check
  if ! grep -q '"credentials"' ~/.config/go-librespot/state.json 2>/dev/null; then
    echo ""
    echo "❌ Spotify login timed out!"
    echo "   Run this script again and connect via Spotify app"
    exit 1
  fi
fi

# ============================================
# 4. Install system packages
# ============================================
echo "📦 Installing system packages..."
sudo apt-get update
sudo apt-get install -y unclutter

# ============================================
# 5. Install npm dependencies
# ============================================
echo "📦 Installing npm dependencies..."
cd ~/berry/backend && npm install
cd ~/berry/frontend && npm install

# ============================================
# 6. Build frontend for production
# ============================================
echo "🔨 Building frontend..."
cd ~/berry/frontend && npm run build

# ============================================
# 7. Setup systemd services (symlinks)
# ============================================
echo "⚙️ Setting up systemd services..."
sudo ln -sf ~/berry/pi/systemd/berry-librespot.service /etc/systemd/system/
sudo ln -sf ~/berry/pi/systemd/berry-backend.service /etc/systemd/system/
sudo ln -sf ~/berry/pi/systemd/berry-frontend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable berry-librespot berry-backend berry-frontend

# ============================================
# 8. Setup autostart (symlinks)
# ============================================
echo "🖥️ Setting up autostart..."
mkdir -p ~/.config/autostart
ln -sf ~/berry/pi/autostart/berry-kiosk.desktop ~/.config/autostart/
ln -sf ~/berry/pi/autostart/berry-unclutter.desktop ~/.config/autostart/

# ============================================
# 9. Setup backlight permissions (for sleep mode)
# ============================================
echo "💡 Setting up backlight permissions..."
sudo usermod -aG video $USER 2>/dev/null || true
echo 'SUBSYSTEM=="backlight", ACTION=="add", RUN+="/bin/chmod 666 /sys/class/backlight/rpi_backlight/bl_power"' | sudo tee /etc/udev/rules.d/99-backlight.rules > /dev/null

# ============================================
# 10. Setup auto-update cron job
# ============================================
echo "🔄 Setting up auto-updates..."
chmod +x ~/berry/pi/auto-update.sh
(crontab -l 2>/dev/null | grep -v "berry/pi/auto-update"; echo "0 * * * * ~/berry/pi/auto-update.sh >> ~/berry-update.log 2>&1") | crontab -

# ============================================
# 11. Start services
# ============================================
echo "🚀 Starting services..."
sudo systemctl start berry-librespot berry-backend berry-frontend

echo ""
echo "============================================"
echo "✅ Berry setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Reboot: sudo reboot"
echo "  2. After reboot, Berry starts automatically"
echo "  3. Open http://$(hostname).local:3000"
echo ""

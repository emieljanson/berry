# Berry Raspberry Pi Setup

## Installation (2 steps)

### 1. Install Raspberry Pi OS
- Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
- Choose "Raspberry Pi OS (64-bit)"
- Click **⚙️ Settings**:
  - Hostname: `berry`
  - Username: `admin`, password of your choice
  - Configure WiFi
  - Enable SSH
- Flash to SD card

### 2. Install Berry
```bash
ssh admin@berry.local
curl -sSL https://raw.githubusercontent.com/emieljanson/berry/main/install.sh | bash
sudo reboot
```

**Done!** 🎉

---

## What the install script does

- ✅ Installs go-librespot (Spotify Connect)
- ✅ Installs Python dependencies (Pygame, Pillow, etc.)
- ✅ Prompts for Spotify login (first time)
- ✅ Configures auto-start on boot
- ✅ Configures auto-updates (hourly)
- ✅ Starts Berry

After reboot:
- Berry starts automatically in fullscreen
- Touch to control

---

## Management

### Services
```bash
sudo systemctl status berry-native      # Status
sudo systemctl restart berry-native     # Restart
journalctl -u berry-native -f           # Logs
```

### Manual update
```bash
cd ~/berry && git pull
source venv/bin/activate && pip install -r requirements.txt
sudo systemctl restart berry-native
```

### Update logs
```bash
cat ~/berry-update.log
```

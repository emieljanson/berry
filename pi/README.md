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

- ✅ Installs Git, Node.js, go-librespot
- ✅ Prompts for Spotify login (first time)
- ✅ Configures auto-start on boot
- ✅ Configures auto-updates (hourly)
- ✅ Builds and starts Berry

After reboot:
- Berry starts automatically
- Open http://berry.local:3000

---

## Management

### Services
```bash
sudo systemctl status berry-backend     # Status
sudo systemctl restart berry-backend    # Restart
journalctl -u berry-backend -f          # Logs
```

### Manual update
```bash
cd ~/berry && git pull
npm run build --prefix frontend
sudo systemctl restart berry-backend berry-frontend
```

### Update logs
```bash
cat ~/berry-update.log
```

# 🍓 Berry

A simple music player for kids, running on a Raspberry Pi.

## Features

- 🎵 Spotify Connect integration
- 🖼️ Large album covers in carousel
- ▶️ Simple play/pause/skip controls
- 💾 Save favorite albums/playlists
- 😴 Auto sleep mode (screen off after inactivity)
- 🔄 Auto-updates via GitHub

## Installation

On a fresh Raspberry Pi:

```bash
ssh admin@berry.local
curl -sSL https://raw.githubusercontent.com/emieljanson/berry/main/install.sh | bash
sudo reboot
```

See [pi/README.md](pi/README.md) for detailed instructions.

## Development

On your Mac, with a Pi on the network:

```bash
./dev-pi.sh
```

This automatically syncs changes to the Pi.

## Tech Stack

- **Frontend:** React + Vite + Embla Carousel
- **Backend:** Node.js + Express + WebSocket
- **Spotify:** go-librespot (Spotify Connect)
- **Hardware:** Raspberry Pi + Touchscreen

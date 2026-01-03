# 🍓 Berry

A simple music player for kids, running on a Raspberry Pi with a touchscreen.

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

This automatically syncs changes to the Pi and streams logs.

### Local development (no Pi)

```bash
./run.sh
```

## Project Structure

```
berry/
├── berry/              # Python package
│   ├── api/            # Spotify & catalog APIs
│   ├── handlers/       # Event & touch handlers
│   ├── managers/       # Carousel, sleep, etc.
│   └── ui/             # Renderer & helpers
├── data/               # Saved albums & images
├── icons/              # UI icons
├── pi/                 # Raspberry Pi setup
│   ├── systemd/        # Service files
│   └── setup.sh        # First-time setup
├── berry.py            # Entry point
├── requirements.txt    # Python dependencies
└── run.sh              # Local dev launcher
```

## Tech Stack

- **UI:** Python + Pygame
- **Spotify:** go-librespot (Spotify Connect)
- **Hardware:** Raspberry Pi + Touchscreen

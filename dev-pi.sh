#!/bin/bash

# Berry Pi Development Script
# Syncs files and streams logs from the Raspberry Pi

set -e

PI_HOST="admin@berry.local"
PI_DIR="~/berry"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'

# Verbose mode (show all logs including progress saves)
VERBOSE=false

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        -v|--verbose) VERBOSE=true ;;
        -h|--help) 
            echo "Usage: ./dev-pi.sh [-v|--verbose]"
            echo "  -v, --verbose  Show all logs (including progress saves)"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

echo -e "${GREEN}🍓 Berry Pi Development${NC}"
if [ "$VERBOSE" = true ]; then
    echo -e "${YELLOW}(Verbose mode)${NC}"
fi
echo "========================"
echo ""

# Cleanup function - stop dev processes and restart systemd services
cleanup() {
    echo ""
    echo -e "${YELLOW}🛑 Stopping dev services on Pi...${NC}"
    # Kill the log streaming SSH session
    kill $LOG_PID 2>/dev/null || true
    ssh $PI_HOST "pkill -9 -f 'go-librespot' 2>/dev/null; pkill -9 -f 'node.*server' 2>/dev/null; pkill -9 -f 'vite' 2>/dev/null" 2>/dev/null || true
    echo -e "${BLUE}🔄 Restarting systemd services...${NC}"
    ssh $PI_HOST "sudo systemctl start berry-librespot berry-backend berry-frontend" 2>/dev/null || true
    echo -e "${GREEN}✓ Back to production mode${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

# Check if fswatch is installed
if ! command -v fswatch &> /dev/null; then
    echo -e "${YELLOW}Installing fswatch...${NC}"
    brew install fswatch
fi

# Setup SSH key if not already done (avoids password prompts)
if ! ssh -o BatchMode=yes -o ConnectTimeout=5 $PI_HOST "exit" 2>/dev/null; then
    echo -e "${YELLOW}🔑 Setting up SSH key (one-time setup)...${NC}"
    echo -e "${YELLOW}   You'll need to enter the Pi password once.${NC}"
    
    # Generate SSH key if it doesn't exist
    if [ ! -f ~/.ssh/id_ed25519 ]; then
        echo -e "${BLUE}Generating SSH key...${NC}"
        ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -q
    fi
    
    # Copy key to Pi
    ssh-copy-id -i ~/.ssh/id_ed25519.pub $PI_HOST
    
    echo -e "${GREEN}✓ SSH key installed - no more password prompts!${NC}"
    echo ""
fi

# Initial sync
echo -e "${BLUE}📦 Initial sync to Pi...${NC}"
rsync -avz --exclude 'node_modules' --exclude '.git' --exclude '.cursor' --exclude 'backend/data' \
    "$LOCAL_DIR/" "$PI_HOST:$PI_DIR/"
echo -e "${GREEN}✓ Synced${NC}"
echo ""

# Start services on Pi via SSH
echo -e "${BLUE}🚀 Starting services on Pi...${NC}"

# Stop systemd services and existing processes, then start fresh
ssh -t $PI_HOST << 'ENDSSH'
# Stop systemd services first (they auto-restart, so must stop them)
echo "Stopping systemd services..."
sudo systemctl stop berry-backend berry-librespot berry-frontend 2>/dev/null || true

# Aggressively kill ALL node and librespot processes (use -9 for force kill)
echo "Killing existing processes..."
pkill -9 -f "go-librespot" 2>/dev/null || true
pkill -9 -f "node" 2>/dev/null || true  
pkill -9 -f "vite" 2>/dev/null || true
sleep 2

# Double-check no node processes remain
while pgrep -f "node.*server" > /dev/null; do
    echo "Waiting for node processes to die..."
    pkill -9 -f "node" 2>/dev/null || true
    sleep 1
done

# Clear old logs
> /tmp/go-librespot.log
> /tmp/berry-backend.log
> /tmp/berry-frontend.log

# Start go-librespot in background
echo "Starting go-librespot..."
cd ~
nohup go-librespot --config_dir ~/.config/go-librespot > /tmp/go-librespot.log 2>&1 &
LIBRESPOT_PID=$!
echo "  go-librespot PID: $LIBRESPOT_PID"
sleep 2

# Start backend with --watch for auto-restart on file changes
echo "Starting backend..."
cd ~/berry/backend
nohup node --watch server.js > /tmp/berry-backend.log 2>&1 &
BACKEND_PID=$!
echo "  backend PID: $BACKEND_PID"
sleep 2

# Start frontend in background  
echo "Starting frontend..."
cd ~/berry/frontend
nohup npm run dev -- --host > /tmp/berry-frontend.log 2>&1 &
FRONTEND_PID=$!
echo "  frontend PID: $FRONTEND_PID"
sleep 3

echo ""
echo "✅ All services started!"
echo ""
echo "URLs:"
echo "  Frontend:  http://berry.local:3000"
echo "  Backend:   http://berry.local:3001"
echo "  Librespot: http://berry.local:3678"
ENDSSH

echo ""
echo -e "${GREEN}✓ Services running on Pi${NC}"
echo ""
echo "=================================="
echo -e "${CYAN}📋 Log Legend:${NC}"
echo -e "  ${CYAN}📱 [app]${NC}      - Frontend App.jsx"
echo -e "  ${CYAN}🎠 [carousel]${NC} - Frontend Carousel"
echo -e "  ${CYAN}⏱️ [timer]${NC}    - Playback timer"
echo -e "  ${MAGENTA}💾 [progress]${NC} - Backend save progress"
echo -e "  ${GREEN}▶️ [play]${NC}     - Play requests"
echo -e "  ${RED}❌${NC}            - Errors"
echo "=================================="
echo ""

# Stream logs from Pi in background
# This tails all relevant logs and formats them nicely
ssh $PI_HOST 'tail -f /tmp/berry-backend.log /tmp/go-librespot.log 2>/dev/null' 2>/dev/null | while read -r line; do
    # Skip noisy lines (always filtered)
    case "$line" in
        *"==> "*)
            # File header from tail -f, skip
            continue
            ;;
        *"failed getting output device delay"*)
            # Harmless go-librespot warning on some audio setups
            continue
            ;;
        *"time="*"level=debug"*)
            # Skip debug logs from go-librespot
            continue
            ;;
    esac
    
    # Verbose mode: show everything except filtered lines above
    if [ "$VERBOSE" = true ]; then
        case "$line" in
            *"📱"*|*"🎠"*|*"⏱️"*|*"▶️"*|*"💾"*|*"🔌"*|*"📤"*|*"📥"*|*"↩️"*)
                echo -e "${CYAN}$line${NC}"
                ;;
            *"error"*|*"Error"*|*"ERROR"*|*"❌"*)
                echo -e "${RED}$line${NC}"
                ;;
            *"✅"*|*"✓"*)
                echo -e "${GREEN}$line${NC}"
                ;;
            *"⚠"*|*"warn"*|*"Warn"*)
                echo -e "${YELLOW}$line${NC}"
                ;;
            *)
                if [[ ! "$line" =~ ^[[:space:]]*$ ]] && [[ ${#line} -gt 5 ]]; then
                    echo "$line"
                fi
                ;;
        esac
        continue
    fi
    
    # Normal mode: only show important stuff (clean output)
    case "$line" in
        *"time="*"level=info"*"authenticated"*)
            echo -e "${GREEN}✓ Spotify authenticated${NC}"
            ;;
        *"time="*)
            # Skip all other go-librespot time= logs in normal mode
            ;;
        *"error"*|*"Error"*|*"ERROR"*|*"❌"*)
            # Always show errors
            echo -e "${RED}$line${NC}"
            ;;
        *"🍓 Berry backend"*|*"🌐 Frontend WebSocket"*)
            # Startup messages
            echo -e "${GREEN}$line${NC}"
            ;;
        *"📸 Saved new cover"*)
            # New album added
            echo -e "${BLUE}$line${NC}"
            ;;
        *"🗑️ Removed from catalog"*)
            # Album removed
            echo -e "${YELLOW}$line${NC}"
            ;;
        *)
            # Skip everything else in normal mode
            ;;
    esac
done &
LOG_PID=$!

# Watch for changes and sync
echo -e "${BLUE}👀 Watching for file changes...${NC}"
echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
echo ""

LAST_SYNC=0
fswatch -o "$LOCAL_DIR" \
    --exclude 'node_modules' \
    --exclude '\.git' \
    --exclude '\.cursor' \
    --exclude '\.DS_Store' \
    --exclude 'backend/data' \
    --exclude 'CAROUSEL_SPEC.md' \
    | while read; do
    # Debounce: skip if less than 1 second since last sync
    NOW=$(date +%s)
    if [ $((NOW - LAST_SYNC)) -lt 1 ]; then
        continue
    fi
    LAST_SYNC=$NOW
    
    echo -e "${BLUE}📦 Syncing...${NC}"
    rsync -avz --exclude 'node_modules' --exclude '.git' --exclude '.cursor' --exclude 'backend/data' \
        "$LOCAL_DIR/" "$PI_HOST:$PI_DIR/" 2>/dev/null
    echo -e "${GREEN}✓ Synced $(date +%H:%M:%S)${NC}"
    # node --watch on Pi auto-restarts backend when it detects file changes
done

# Cleanup log streaming
kill $LOG_PID 2>/dev/null || true

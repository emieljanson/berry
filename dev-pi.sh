#!/bin/bash

# Berry Pi Development Script
# Syncs files and runs everything on the Raspberry Pi

set -e

PI_HOST="admin@berry.local"
PI_DIR="~/berry"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}🍓 Berry Pi Development${NC}"
echo "========================"
echo ""

# Cleanup function - stop dev processes and restart systemd services
cleanup() {
    echo ""
    echo -e "${YELLOW}🛑 Stopping dev services on Pi...${NC}"
    ssh $PI_HOST "pkill -f 'go-librespot' 2>/dev/null; pkill -f 'node.*server' 2>/dev/null; pkill -f 'vite' 2>/dev/null" 2>/dev/null || true
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

# Kill any remaining processes
pkill -f "go-librespot" 2>/dev/null || true
pkill -f "node.*server" 2>/dev/null || true  
pkill -f "vite" 2>/dev/null || true
sleep 2

# Start go-librespot in background
echo "Starting go-librespot..."
cd ~
nohup go-librespot --config_dir ~/.config/go-librespot > /tmp/go-librespot.log 2>&1 &
sleep 2

# Start backend in background
echo "Starting backend..."
cd ~/berry/backend
nohup npm run dev > /tmp/berry-backend.log 2>&1 &
sleep 2

# Start frontend in background  
echo "Starting frontend..."
cd ~/berry/frontend
nohup npm run dev -- --host > /tmp/berry-frontend.log 2>&1 &
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
echo -e "${BLUE}👀 Watching for file changes...${NC}"
echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
echo ""

# Watch for changes and sync
fswatch -o "$LOCAL_DIR" \
    --exclude 'node_modules' \
    --exclude '\.git' \
    --exclude '\.cursor' \
    --exclude '\.DS_Store' \
    --exclude 'backend/data' \
    | while read; do
    echo -e "${BLUE}📦 Syncing changes...${NC}"
    rsync -avz --exclude 'node_modules' --exclude '.git' --exclude '.cursor' --exclude 'backend/data' \
        "$LOCAL_DIR/" "$PI_HOST:$PI_DIR/" 2>/dev/null
    echo -e "${GREEN}✓ Synced $(date +%H:%M:%S)${NC}"
done


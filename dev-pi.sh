#!/bin/bash

# Berry Pi Development Script
# Choose between native (Pygame) or web mode

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

# Mode selection
MODE=""
VERBOSE=false
LOCAL_PID=""
LOG_PID=""

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        native|n) MODE="native" ;;
        web|w) MODE="web" ;;
        -v|--verbose) VERBOSE=true ;;
        -h|--help) 
            echo "Usage: ./dev-pi.sh [native|web] [-v|--verbose]"
            echo ""
            echo "Modes:"
            echo "  native, n   Run native Pygame UI (no browser)"
            echo "  web, w      Run web UI in Chromium kiosk"
            echo ""
            echo "Options:"
            echo "  -v, --verbose  Show all logs"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

echo -e "${GREEN}🍓 Berry Pi Development${NC}"
echo "========================"
echo ""

# If no mode specified, show menu
if [ -z "$MODE" ]; then
    echo "Select mode:"
    echo ""
    echo -e "  ${CYAN}1)${NC} ${GREEN}native${NC} - Pygame UI (lightweight, no browser)"
    echo -e "  ${CYAN}2)${NC} ${BLUE}web${NC}    - Web UI in Chromium kiosk"
    echo ""
    read -p "Enter choice [1/2]: " choice
    
    case $choice in
        1|native|n) MODE="native" ;;
        2|web|w) MODE="web" ;;
        *) 
            echo -e "${RED}Invalid choice${NC}"
            exit 1
            ;;
    esac
    echo ""
fi

echo -e "Mode: ${GREEN}$MODE${NC}"
if [ "$VERBOSE" = true ]; then
    echo -e "${YELLOW}(Verbose mode)${NC}"
fi
echo ""

# Cleanup function
cleanup() {
    echo ""
    echo -e "${YELLOW}🛑 Stopping everything...${NC}"
    kill $LOG_PID 2>/dev/null || true
    
    # Kill local processes
    pkill -f "berry.py" 2>/dev/null || true
    kill $LOCAL_PID 2>/dev/null || true
    
    # Kill ALL Pi processes
    ssh $PI_HOST "sudo systemctl stop berry-librespot berry-backend berry-frontend 2>/dev/null; pkill -9 -f 'berry.py' 2>/dev/null; pkill -9 -f 'go-librespot' 2>/dev/null; pkill -9 -f 'node' 2>/dev/null; pkill -9 -f 'vite' 2>/dev/null" 2>/dev/null || true
    
    echo -e "${GREEN}✓ All stopped${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

# Check if fswatch is installed (for file watching)
if ! command -v fswatch &> /dev/null; then
    echo -e "${YELLOW}Installing fswatch...${NC}"
    brew install fswatch
fi

# Setup SSH key if needed
if ! ssh -o BatchMode=yes -o ConnectTimeout=5 $PI_HOST "exit" 2>/dev/null; then
    echo -e "${YELLOW}🔑 Setting up SSH key (one-time setup)...${NC}"
    echo -e "${YELLOW}   You'll need to enter the Pi password once.${NC}"
    
    if [ ! -f ~/.ssh/id_ed25519 ]; then
        echo -e "${BLUE}Generating SSH key...${NC}"
        ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -q
    fi
    
    ssh-copy-id -i ~/.ssh/id_ed25519.pub $PI_HOST
    echo -e "${GREEN}✓ SSH key installed${NC}"
    echo ""
fi

# Initial sync
echo -e "${BLUE}📦 Syncing files to Pi...${NC}"
rsync -avz --exclude 'node_modules' --exclude '.git' --exclude '.cursor' --exclude 'backend/data' --exclude 'native/venv' \
    "$LOCAL_DIR/" "$PI_HOST:$PI_DIR/"
echo -e "${GREEN}✓ Synced${NC}"
echo ""

# ============================================
# NATIVE MODE
# ============================================
if [ "$MODE" = "native" ]; then
    echo -e "${BLUE}🚀 Starting Native mode...${NC}"
    
    ssh -t $PI_HOST << 'ENDSSH'
    # Stop ALL services and processes first
    echo "Stopping all services..."
    sudo systemctl stop berry-backend berry-frontend berry-librespot 2>/dev/null || true
    pkill -9 -f "node" 2>/dev/null || true
    pkill -9 -f "vite" 2>/dev/null || true
    pkill -9 -f "chromium" 2>/dev/null || true
    pkill -9 -f "go-librespot" 2>/dev/null || true
    sleep 1
    
    # Start librespot only (no backend needed - CatalogManager handles save/delete)
    echo "Starting go-librespot..."
    sudo systemctl start berry-librespot
    sleep 2
    
    # Verify it's running
    if pgrep -f "go-librespot" > /dev/null; then
        echo "✓ go-librespot running"
    else
        echo "❌ go-librespot failed to start"
        journalctl -u berry-librespot -n 5 --no-pager
    fi
    
    # Setup Python environment
    echo "Setting up Python environment..."
    cd ~/berry/native
    
    if [ ! -d "venv" ]; then
        python3 -m venv venv
    fi
    
    source venv/bin/activate
    pip install -q -r requirements.txt 2>/dev/null
    
    # Create data directory
    mkdir -p data/images
    
    # Clear log
    > /tmp/berry-native.log
    
    # Start the native app in fullscreen (unbuffered output for live logs)
    echo ""
    echo "Starting Berry Native (fullscreen)..."
    nohup python -u berry.py --fullscreen > /tmp/berry-native.log 2>&1 &
    BERRY_PID=$!
    echo "  Berry PID: $BERRY_PID"
    sleep 2
    
    if ps -p $BERRY_PID > /dev/null; then
        echo ""
        echo "✅ Berry Native running on Pi!"
    else
        echo "❌ Failed to start Berry Native on Pi"
        cat /tmp/berry-native.log
    fi
ENDSSH
    
    echo ""
    echo -e "${GREEN}✓ Berry Native running on Pi${NC}"
    echo ""
    echo "=================================="
    echo -e "  ${GREEN}🎮 Display${NC}  Fullscreen on Pi"
    echo -e "  ${CYAN}🎵 Audio${NC}    Pi speakers"  
    echo -e "  ${BLUE}🔄 Sync${NC}     Auto on file changes"
    echo "=================================="
    echo ""
    
    # Wait a moment for the app to start writing logs
    sleep 2
    
    # Stream Pi logs (native app)
    echo -e "${CYAN}📋 Streaming logs...${NC}"
    ssh $PI_HOST 'tail -f /tmp/berry-native.log 2>/dev/null' 2>/dev/null | while read -r line; do
        case "$line" in
            *"👆"*|*"→"*)
                # Touch events - always show
                echo -e "${CYAN}$line${NC}"
                ;;
            *"📡"*|*"📂"*|*"✅"*|*"✓"*)
                # Status/success messages
                echo -e "${GREEN}$line${NC}"
                ;;
            *"❌"*|*"Error"*|*"error"*|*"ERROR"*|*"Traceback"*|*"Exception"*)
                # Errors - always show
                echo -e "${RED}$line${NC}"
                ;;
            *"⚠"*)
                echo -e "${YELLOW}$line${NC}"
                ;;
            *)
                # Show everything in native mode for debugging
                if [[ ! "$line" =~ ^[[:space:]]*$ ]] && [[ ${#line} -gt 2 ]]; then
                    echo "$line"
                fi
                ;;
        esac
    done &
    LOG_PID=$!
    
# ============================================
# WEB MODE
# ============================================
else
    echo -e "${BLUE}🚀 Starting Web mode...${NC}"
    
    ssh -t $PI_HOST << 'ENDSSH'
    # Stop any native processes
    pkill -9 -f "berry.py" 2>/dev/null || true
    
    # Stop systemd services first
    echo "Stopping systemd services..."
    sudo systemctl stop berry-backend berry-librespot berry-frontend 2>/dev/null || true
    
    # Kill existing processes
    echo "Killing existing processes..."
    pkill -9 -f "go-librespot" 2>/dev/null || true
    pkill -9 -f "node" 2>/dev/null || true  
    pkill -9 -f "vite" 2>/dev/null || true
    sleep 2
    
    # Clear old logs
    > /tmp/go-librespot.log
    > /tmp/berry-backend.log
    > /tmp/berry-frontend.log
    
    # Start go-librespot
    echo "Starting go-librespot..."
    cd ~
    nohup go-librespot --config_dir ~/.config/go-librespot > /tmp/go-librespot.log 2>&1 &
    LIBRESPOT_PID=$!
    echo "  go-librespot PID: $LIBRESPOT_PID"
    sleep 2
    
    # Start backend
    echo "Starting backend..."
    cd ~/berry/backend
    nohup node --watch server.js > /tmp/berry-backend.log 2>&1 &
    BACKEND_PID=$!
    echo "  backend PID: $BACKEND_PID"
    sleep 2
    
    # Start frontend
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
    echo -e "${GREEN}✓ Web mode active${NC}"
    echo ""
    echo "=================================="
    echo -e "${CYAN}📋 Log Legend:${NC}"
    echo -e "  ${CYAN}📱 [app]${NC}      - Frontend App.jsx"
    echo -e "  ${CYAN}🎠 [carousel]${NC} - Frontend Carousel"
    echo -e "  ${MAGENTA}💾 [progress]${NC} - Backend save progress"
    echo -e "  ${GREEN}▶️ [play]${NC}     - Play requests"
    echo -e "  ${RED}❌${NC}            - Errors"
    echo "=================================="
    echo ""
    
    # Stream logs
    ssh $PI_HOST 'tail -f /tmp/berry-backend.log /tmp/go-librespot.log 2>/dev/null' 2>/dev/null | while read -r line; do
        case "$line" in
            *"==> "*)
                continue
                ;;
            *"failed getting output device delay"*)
                continue
                ;;
            *"time="*"level=debug"*)
                continue
                ;;
        esac
        
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
                *)
                    if [[ ! "$line" =~ ^[[:space:]]*$ ]] && [[ ${#line} -gt 5 ]]; then
                        echo "$line"
                    fi
                    ;;
            esac
        else
            case "$line" in
                *"time="*"level=info"*"authenticated"*)
                    echo -e "${GREEN}✓ Spotify authenticated${NC}"
                    ;;
                *"time="*)
                    ;;
                *"error"*|*"Error"*|*"ERROR"*|*"❌"*)
                    echo -e "${RED}$line${NC}"
                    ;;
                *"🍓 Berry backend"*|*"🌐 Frontend WebSocket"*)
                    echo -e "${GREEN}$line${NC}"
                    ;;
                *"📸 Saved new cover"*)
                    echo -e "${BLUE}$line${NC}"
                    ;;
                *"🗑️ Removed from catalog"*)
                    echo -e "${YELLOW}$line${NC}"
                    ;;
            esac
        fi
    done &
    LOG_PID=$!
fi

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
    --exclude 'native/venv' \
    | while read; do
    NOW=$(date +%s)
    if [ $((NOW - LAST_SYNC)) -lt 1 ]; then
        continue
    fi
    LAST_SYNC=$NOW
    
    echo -e "${BLUE}📦 Syncing...${NC}"
    rsync -avz --exclude 'node_modules' --exclude '.git' --exclude '.cursor' --exclude 'backend/data' --exclude 'native/venv' \
        "$LOCAL_DIR/" "$PI_HOST:$PI_DIR/" 2>/dev/null
    echo -e "${GREEN}✓ Synced $(date +%H:%M:%S) - Ctrl+C and restart to apply${NC}"
done

kill $LOG_PID 2>/dev/null || true

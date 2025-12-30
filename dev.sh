#!/bin/bash

# Berry Development Script
# Start alle services en monitor de status

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${GREEN}🍓 Berry Development Environment${NC}"
echo "=================================="
echo ""

# Directories
BERRY_DIR="$(cd "$(dirname "$0")" && pwd)"
LIBRESPOT_CONFIG="/opt/homebrew/etc/go-librespot"

# Cleanup function
cleanup() {
    echo ""
    echo -e "${YELLOW}Shutting down...${NC}"
    pkill -f "go-librespot" 2>/dev/null || true
    pkill -f "node.*berry.*server" 2>/dev/null || true
    pkill -f "vite" 2>/dev/null || true
    echo -e "${GREEN}Done!${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

# Check prerequisites
check_prereqs() {
    echo -e "${BLUE}Checking prerequisites...${NC}"
    
    if ! command -v node &> /dev/null; then
        echo -e "${RED}Node.js not found!${NC}"
        exit 1
    fi
    
    if ! command -v /opt/homebrew/opt/go-librespot/bin/go-librespot &> /dev/null; then
        echo -e "${RED}go-librespot not found! Run: brew install go-librespot${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}✓ Prerequisites OK${NC}"
    echo ""
}

# Start go-librespot
start_librespot() {
    echo -e "${BLUE}Starting go-librespot...${NC}"
    
    # Kill existing
    pkill -f "go-librespot" 2>/dev/null || true
    sleep 1
    
    # Ensure config exists
    if [ ! -f "$LIBRESPOT_CONFIG/config.yml" ]; then
        echo "Creating default config..."
        mkdir -p "$LIBRESPOT_CONFIG"
        cat > "$LIBRESPOT_CONFIG/config.yml" << 'EOF'
device_name: Berry
device_type: speaker
zeroconf_enabled: true
credentials:
  type: interactive
server:
  enabled: true
  address: 0.0.0.0
  port: 3678
  allow_origin: '*'
audio_backend: pipe
log_level: info
EOF
    fi
    
    # Start in background
    /opt/homebrew/opt/go-librespot/bin/go-librespot --config_dir "$LIBRESPOT_CONFIG" > /tmp/berry-librespot.log 2>&1 &
    LIBRESPOT_PID=$!
    sleep 2
    
    # Check if running
    if ps -p $LIBRESPOT_PID > /dev/null; then
        echo -e "${GREEN}✓ go-librespot running (PID: $LIBRESPOT_PID)${NC}"
        
        # Check if needs login
        if grep -q "to complete authentication" /tmp/berry-librespot.log 2>/dev/null; then
            echo ""
            echo -e "${YELLOW}⚠️  Login required! Open this URL in your browser:${NC}"
            grep "accounts.spotify.com" /tmp/berry-librespot.log | tail -1
            echo ""
            echo "Waiting for login..."
            while ! curl -s http://localhost:3678/status | grep -q "username"; do
                sleep 2
            done
            echo -e "${GREEN}✓ Logged in!${NC}"
        fi
    else
        echo -e "${RED}✗ go-librespot failed to start${NC}"
        cat /tmp/berry-librespot.log
        exit 1
    fi
    echo ""
}

# Start backend
start_backend() {
    echo -e "${BLUE}Starting Berry backend...${NC}"
    
    pkill -f "node.*server" 2>/dev/null || true
    sleep 1
    
    cd "$BERRY_DIR/backend"
    npm install --silent
    npm run dev > /tmp/berry-backend.log 2>&1 &
    BACKEND_PID=$!
    sleep 2
    
    if curl -s http://localhost:3001/api/now-playing > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Backend running at http://localhost:3001${NC}"
    else
        echo -e "${RED}✗ Backend failed to start${NC}"
        cat /tmp/berry-backend.log
        exit 1
    fi
    echo ""
}

# Start frontend
start_frontend() {
    echo -e "${BLUE}Starting Berry frontend...${NC}"
    
    pkill -f "vite" 2>/dev/null || true
    sleep 1
    
    cd "$BERRY_DIR/frontend"
    npm install --silent
    npm run dev > /tmp/berry-frontend.log 2>&1 &
    FRONTEND_PID=$!
    sleep 3
    
    if curl -s http://localhost:3000 > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Frontend running at http://localhost:3000${NC}"
    else
        echo -e "${RED}✗ Frontend failed to start${NC}"
        cat /tmp/berry-frontend.log
        exit 1
    fi
    echo ""
}

# Monitor status
monitor() {
    echo -e "${GREEN}All services running!${NC}"
    echo ""
    echo "URLs:"
    echo "  Frontend:  http://localhost:3000"
    echo "  Backend:   http://localhost:3001"
    echo "  Librespot: http://localhost:3678"
    echo ""
    echo -e "${YELLOW}Press Ctrl+C to stop all services${NC}"
    echo ""
    echo "=================================="
    echo ""
    
    # Monitor loop
    while true; do
        # Check if go-librespot crashed
        if ! pgrep -f "go-librespot" > /dev/null; then
            echo ""
            echo -e "${RED}💥 go-librespot crashed!${NC}"
            
            # Show last lines of log
            if [ -f /tmp/berry-librespot.log ]; then
                echo -e "${YELLOW}Last log lines:${NC}"
                tail -10 /tmp/berry-librespot.log
            fi
            
            echo ""
            echo -e "${YELLOW}Restarting go-librespot...${NC}"
            /opt/homebrew/opt/go-librespot/bin/go-librespot --config_dir "$LIBRESPOT_CONFIG" > /tmp/berry-librespot.log 2>&1 &
            sleep 3
            
            if pgrep -f "go-librespot" > /dev/null; then
                echo -e "${GREEN}✓ go-librespot restarted${NC}"
            else
                echo -e "${RED}✗ Failed to restart go-librespot${NC}"
            fi
            echo ""
        fi
        
        # Get now playing
        NOW=$(curl -s http://localhost:3001/api/now-playing 2>/dev/null)
        
        if [ -n "$NOW" ]; then
            TRACK=$(echo "$NOW" | jq -r '.track.name // "Nothing playing"')
            ARTIST=$(echo "$NOW" | jq -r '.track.artist // ""')
            PLAYING=$(echo "$NOW" | jq -r 'if .playing then "▶" else "⏸" end')
            
            # Clear line and print status
            echo -ne "\r\033[K${PLAYING} ${TRACK}"
            if [ -n "$ARTIST" ] && [ "$ARTIST" != "null" ]; then
                echo -ne " - ${ARTIST}"
            fi
        else
            echo -ne "\r\033[K⚠️  Backend not responding"
        fi
        
        sleep 2
    done
}

# Main
check_prereqs
start_librespot
start_backend
start_frontend
monitor


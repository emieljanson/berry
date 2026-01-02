#!/bin/bash

# Berry Development Script
# Start all services and monitor status

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# Verbose mode (show all logs including progress saves)
VERBOSE=false

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        -v|--verbose) VERBOSE=true ;;
        -h|--help) 
            echo "Usage: ./dev.sh [-v|--verbose]"
            echo "  -v, --verbose  Show all logs (including progress saves)"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

echo -e "${GREEN}🍓 Berry Development Environment${NC}"
if [ "$VERBOSE" = true ]; then
    echo -e "${YELLOW}(Verbose mode)${NC}"
fi
echo "=================================="
echo ""

# Directories
BERRY_DIR="$(cd "$(dirname "$0")" && pwd)"
LIBRESPOT_CONFIG="/opt/homebrew/etc/go-librespot"

# Cleanup function
cleanup() {
    echo ""
    echo -e "${YELLOW}Shutting down...${NC}"
    
    # Force kill all related processes
    pkill -9 -f "go-librespot" 2>/dev/null || true
    pkill -9 -f "node.*server" 2>/dev/null || true
    pkill -9 -f "vite" 2>/dev/null || true
    
    # Wait for processes to die
    sleep 1
    
    # Double check
    if pgrep -f "node.*server" > /dev/null; then
        echo -e "${YELLOW}Force killing remaining node processes...${NC}"
        pkill -9 -f "node" 2>/dev/null || true
    fi
    
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
    
    # Force kill existing
    pkill -9 -f "go-librespot" 2>/dev/null || true
    sleep 1
    
    # Wait until dead
    while pgrep -f "go-librespot" > /dev/null; do
        echo "  Waiting for old librespot to die..."
        pkill -9 -f "go-librespot" 2>/dev/null || true
        sleep 1
    done
    
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
    
    # Kill any existing backend processes
    pkill -9 -f "node.*server" 2>/dev/null || true
    sleep 1
    
    # Wait until all node processes are dead
    while pgrep -f "node.*server" > /dev/null; do
        echo "  Waiting for old processes to die..."
        pkill -9 -f "node" 2>/dev/null || true
        sleep 1
    done
    
    cd "$BERRY_DIR/backend"
    npm install --silent
    
    # Use node --watch for auto-restart on file changes
    npm run dev > /tmp/berry-backend.log 2>&1 &
    BACKEND_PID=$!
    echo "  Backend PID: $BACKEND_PID"
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
    
    pkill -9 -f "vite" 2>/dev/null || true
    sleep 1
    
    cd "$BERRY_DIR/frontend"
    npm install --silent
    npm run dev > /tmp/berry-frontend.log 2>&1 &
    FRONTEND_PID=$!
    echo "  Frontend PID: $FRONTEND_PID"
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
    echo -e "${YELLOW}Press Ctrl+C to stop all services${NC}"
    echo ""
    
    # Track last log positions
    LAST_BACKEND_LINE=0
    LAST_LIBRESPOT_LINE=0
    LAST_TRACK=""
    
    # Monitor loop
    while true; do
        # Check for multiple backend processes (bad!)
        NODE_COUNT=$(pgrep -fc "node.*server" 2>/dev/null || echo "0")
        if [ "$NODE_COUNT" -gt 1 ]; then
            echo -e "${RED}⚠️ Multiple backend processes detected ($NODE_COUNT)! Cleaning up...${NC}"
            pkill -9 -f "node.*server" 2>/dev/null || true
            sleep 2
            # Restart backend
            cd "$BERRY_DIR/backend"
            npm run dev > /tmp/berry-backend.log 2>&1 &
            echo -e "${GREEN}✓ Backend restarted${NC}"
            LAST_BACKEND_LINE=0
        fi
        
        # Check if go-librespot crashed
        if ! pgrep -f "go-librespot" > /dev/null; then
            echo -e "${RED}💥 librespot crashed${NC}"
            
            if [ -f /tmp/berry-librespot.log ]; then
                tail -3 /tmp/berry-librespot.log | while read -r line; do
                    echo -e "   ${RED}$line${NC}"
                done
            fi
            
            echo -e "${YELLOW}↻ restarting...${NC}"
            /opt/homebrew/opt/go-librespot/bin/go-librespot --config_dir "$LIBRESPOT_CONFIG" > /tmp/berry-librespot.log 2>&1 &
            sleep 3
            LAST_LIBRESPOT_LINE=0
            
            if pgrep -f "go-librespot" > /dev/null; then
                echo -e "${GREEN}✓ librespot back${NC}"
            else
                echo -e "${RED}✗ restart failed${NC}"
            fi
        fi
        
        # Stream new backend log lines (events)
        if [ -f /tmp/berry-backend.log ]; then
            CURRENT_LINES=$(wc -l < /tmp/berry-backend.log)
            if [ "$CURRENT_LINES" -gt "$LAST_BACKEND_LINE" ]; then
                tail -n +$((LAST_BACKEND_LINE + 1)) /tmp/berry-backend.log | while read -r line; do
                    # Verbose mode: show everything
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
                                echo "$line"
                                ;;
                        esac
                        continue
                    fi
                    
                    # Normal mode: only show important stuff (clean output)
                    case "$line" in
                        *"Error"*|*"error"*|*"ERROR"*|*"✗"*|*"❌"*)
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
                done
                LAST_BACKEND_LINE=$CURRENT_LINES
            fi
        fi
        
        # Stream librespot logs
        if [ -f /tmp/berry-librespot.log ]; then
            CURRENT_LINES=$(wc -l < /tmp/berry-librespot.log)
            if [ "$CURRENT_LINES" -gt "$LAST_LIBRESPOT_LINE" ]; then
                tail -n +$((LAST_LIBRESPOT_LINE + 1)) /tmp/berry-librespot.log | while read -r line; do
                    # Skip harmless warnings
                    case "$line" in
                        *"failed getting output device delay"*)
                            continue
                            ;;
                    esac
                    
                    # Show errors and warnings
                    case "$line" in
                        *"error"*|*"Error"*|*"ERROR"*|*"failed"*|*"Failed"*)
                            MSG=$(echo "$line" | sed 's/.*level=error //' | sed 's/.*msg="//' | sed 's/".*//' | cut -c1-50)
                            echo -e "${RED}✗ librespot: $MSG${NC}"
                            ;;
                        *"level=warn"*)
                            MSG=$(echo "$line" | sed 's/.*msg="//' | sed 's/".*//' | cut -c1-40)
                            echo -e "${YELLOW}⚠ $MSG${NC}"
                            ;;
                    esac
                done
                LAST_LIBRESPOT_LINE=$CURRENT_LINES
            fi
        fi
        
        # Show current track (only when changed)
        NOW=$(curl -s http://localhost:3001/api/now-playing 2>/dev/null)
        if [ -n "$NOW" ]; then
            TRACK=$(echo "$NOW" | jq -r '.track.name // ""')
            ARTIST=$(echo "$NOW" | jq -r '.track.artist // ""')
            PLAYING=$(echo "$NOW" | jq -r '.playing // false')
            
            TRACK_KEY="${TRACK}|${ARTIST}|${PLAYING}"
            if [ "$TRACK_KEY" != "$LAST_TRACK" ] && [ -n "$TRACK" ] && [ "$TRACK" != "null" ]; then
                if [ "$PLAYING" = "true" ]; then
                    echo -e "${GREEN}▶${NC} $TRACK - $ARTIST"
                else
                    echo -e "${YELLOW}⏸${NC} $TRACK - $ARTIST"
                fi
                LAST_TRACK="$TRACK_KEY"
            fi
        fi
        
        sleep 1
    done
}

# Main
check_prereqs
start_librespot
start_backend
start_frontend
monitor


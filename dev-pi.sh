#!/bin/bash

# Berry Pi Development Script
# Syncs files and runs the Pygame app on the Pi

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
DIM='\033[2m'
NC='\033[0m'

VERBOSE=false
LOG_PID=""

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        -v|--verbose) VERBOSE=true ;;
        -h|--help) 
            echo "Usage: ./dev-pi.sh [-v|--verbose]"
            echo ""
            echo "Options:"
            echo "  -v, --verbose  Show all logs (INFO + DEBUG)"
            echo ""
            echo "Commands while running:"
            echo "  r, Enter  Sync files and restart app"
            echo "  s         Sync files only"
            echo "  l         Show last 20 log lines"
            echo "  q         Quit"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

echo -e "${GREEN}🍓 Berry Pi Development${NC}"
echo "========================"
echo ""

if [ "$VERBOSE" = true ]; then
    echo -e "${DIM}(Verbose mode)${NC}"
    echo ""
fi

# Cleanup function
cleanup() {
    echo ""
    echo -e "${YELLOW}🛑 Stopping...${NC}"
    kill $LOG_PID 2>/dev/null || true
    
    # Quick kill with timeout - don't wait for systemctl
    ssh -o ConnectTimeout=2 $PI_HOST "pkill -9 -f 'berry.py'" 2>/dev/null &
    sleep 0.5
    
    echo -e "${GREEN}✓ Stopped${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

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

# Sync function - shows what changed
sync_files() {
    echo -e "${BLUE}📦 Syncing...${NC}"
    local output
    output=$(rsync -avz --itemize-changes \
        --exclude '.git' --exclude '.cursor' --exclude 'data' --exclude 'venv' --exclude '__pycache__' \
        "$LOCAL_DIR/" "$PI_HOST:$PI_DIR/" 2>&1)
    
    # Count and show changed files
    local changes=$(echo "$output" | grep "^>f" | wc -l | tr -d ' ')
    if [ "$changes" -gt 0 ]; then
        echo "$output" | grep "^>f" | sed 's/^>f[^ ]* /  /' | head -5
        [ "$changes" -gt 5 ] && echo -e "  ${DIM}... and $((changes - 5)) more${NC}"
    fi
    echo -e "${GREEN}✓ Synced${NC}"
}

# Restart the Berry app on Pi
restart_app() {
    echo -e "${BLUE}🔄 Restarting...${NC}"
    ssh $PI_HOST 'pkill -f "berry.py" 2>/dev/null || true; sleep 0.5; cd ~/berry && source venv/bin/activate && nohup python -u berry.py --fullscreen > /tmp/berry.log 2>&1 &' 2>/dev/null
    sleep 1
    if ssh $PI_HOST 'pgrep -f "berry.py" > /dev/null' 2>/dev/null; then
        echo -e "${GREEN}✓ Running${NC}"
    else
        echo -e "${RED}✗ Failed to start${NC}"
        ssh $PI_HOST 'tail -10 /tmp/berry.log' 2>/dev/null
    fi
}

# Start log streaming in background
start_logs() {
    kill $LOG_PID 2>/dev/null || true
    sleep 0.2
    
    ssh $PI_HOST 'tail -f /tmp/berry.log 2>/dev/null' 2>/dev/null | while IFS= read -r line; do
        # Filter based on Python log levels
        case "$line" in
            *"[ERROR]"*|*"[CRITICAL]"*|*"Traceback"*|*"Error:"*)
                echo -e "${RED}$line${NC}"
                ;;
            *"[WARNING]"*)
                echo -e "${YELLOW}$line${NC}"
                ;;
            *"[INFO]"*)
                if [ "$VERBOSE" = true ]; then
                    echo -e "${CYAN}$line${NC}"
                else
                    # Only show important actions in non-verbose mode
                    case "$line" in
                        *"Starting"*|*"Playing"*|*"Pausing"*|*"Resuming"*|*"Saving"*|*"Deleting"*|*"Volume"*|*"Sleep"*|*"Waking"*)
                            echo -e "${CYAN}$line${NC}"
                            ;;
                    esac
                fi
                ;;
            *"[DEBUG]"*)
                [ "$VERBOSE" = true ] && echo -e "${DIM}$line${NC}"
                ;;
            "Controls:"*|"   "*|"")
                # Startup text or empty lines - show in verbose
                [ "$VERBOSE" = true ] && echo "$line"
                ;;
            *)
                # Other output (startup messages without log level)
                if [[ ! "$line" =~ ^[[:space:]]*$ ]]; then
                    echo "$line"
                fi
                ;;
        esac
    done &
    LOG_PID=$!
}

# Initial sync
sync_files
echo ""

# Start the app on Pi
echo -e "${BLUE}🚀 Starting Berry...${NC}"

ssh -t $PI_HOST << 'ENDSSH'
# Stop everything first
sudo systemctl stop berry-native berry-librespot 2>/dev/null || true
pkill -9 -f "berry.py" 2>/dev/null || true
pkill -9 -f "go-librespot" 2>/dev/null || true
sleep 1

# Ensure systemd services are linked
sudo ln -sf ~/berry/pi/systemd/berry-*.service /etc/systemd/system/ 2>/dev/null
sudo systemctl daemon-reload

# Start librespot
sudo systemctl start berry-librespot
sleep 2

if pgrep -f "go-librespot" > /dev/null; then
    echo "✓ go-librespot"
else
    echo "✗ go-librespot failed"
    journalctl -u berry-librespot -n 3 --no-pager
fi

# Setup Python environment
cd ~/berry
[ ! -d "venv" ] && python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt 2>/dev/null

mkdir -p data/images
> /tmp/berry.log

# Start Berry (auto-detects GPU acceleration)
nohup python -u berry.py --fullscreen > /tmp/berry.log 2>&1 &
sleep 2

if pgrep -f "berry.py" > /dev/null; then
    echo "✓ Berry"
else
    echo "✗ Berry failed"
    cat /tmp/berry.log
fi
ENDSSH

echo ""
echo -e "${GREEN}✓ Berry running on Pi${NC}"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}r${NC}/Enter  Sync + Restart"
echo -e "  ${BLUE}s${NC}        Sync only"
echo -e "  ${CYAN}l${NC}        Show recent logs"
echo -e "  ${RED}q${NC}        Quit"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

sleep 1
start_logs

# Command loop
while true; do
    if read -rsn1 -t 1 key 2>/dev/null; then
        case "$key" in
            r|"")
                echo ""
                sync_files
                restart_app
                echo ""
                start_logs
                ;;
            s)
                echo ""
                sync_files
                echo ""
                ;;
            l)
                echo ""
                echo -e "${CYAN}━━━ Recent logs ━━━${NC}"
                ssh $PI_HOST 'tail -20 /tmp/berry.log' 2>/dev/null
                echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━${NC}"
                echo ""
                ;;
            q)
                cleanup
                ;;
        esac
    fi
done

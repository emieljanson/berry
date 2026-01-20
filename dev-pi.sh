#!/bin/bash

# Berry Pi Development Script
# Syncs files and runs the Pygame app on the Pi via systemd

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
PROFILE=false
SKIP_TESTS=false
LOG_PID=""

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        -v|--verbose) VERBOSE=true ;;
        -p|--profile) PROFILE=true ;;
        -T|--skip-tests) SKIP_TESTS=true ;;
        -h|--help)
            echo "Usage: ./dev-pi.sh [-v|--verbose] [-p|--profile] [-T|--skip-tests]"
            echo ""
            echo "Options:"
            echo "  -v, --verbose     Show all logs (INFO + DEBUG)"
            echo "  -p, --profile     Enable frame profiler (shows render timing)"
            echo "  -T, --skip-tests  Skip running tests before sync"
            echo ""
            echo "Commands while running:"
            echo "  r, Enter  Sync files and restart app"
            echo "  s         Sync files only"
            echo "  t         Run tests locally"
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
fi
if [ "$PROFILE" = true ]; then
    echo -e "${DIM}(Profile mode - frame timing enabled)${NC}"
fi
if [ "$VERBOSE" = true ] || [ "$PROFILE" = true ]; then
    echo ""
fi

# Cleanup function
cleanup() {
    echo ""
    echo -e "${YELLOW}🛑 Stopping...${NC}"
    
    # Kill log tail
    kill $LOG_PID 2>/dev/null || true
    
    # Stop Berry service gracefully (SIGTERM → graceful shutdown)
    ssh -o ConnectTimeout=3 $PI_HOST "sudo systemctl stop berry-native" 2>/dev/null || true
    
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

# Run tests locally (fast feedback before sync)
run_tests() {
    echo -e "${BLUE}🧪 Running tests...${NC}"

    # Check if venv exists, create if needed
    if [ ! -d "$LOCAL_DIR/venv" ]; then
        echo -e "${DIM}Creating local venv...${NC}"
        python3 -m venv "$LOCAL_DIR/venv"
        source "$LOCAL_DIR/venv/bin/activate"
        pip install -q -r "$LOCAL_DIR/requirements.txt"
        pip install -q pytest
    else
        source "$LOCAL_DIR/venv/bin/activate"
        # Install pytest if missing
        pip show pytest > /dev/null 2>&1 || pip install -q pytest
    fi

    # Run tests
    if python3 -m pytest "$LOCAL_DIR/tests/" -v --tb=short 2>&1 | tee /tmp/berry_tests.log | tail -20; then
        local passed=$(grep -E "passed|PASSED" /tmp/berry_tests.log | tail -1)
        echo -e "${GREEN}✓ Tests passed${NC} ${DIM}$passed${NC}"
        deactivate 2>/dev/null || true
        return 0
    else
        echo -e "${RED}✗ Tests failed${NC}"
        echo -e "${YELLOW}Fix tests before syncing, or use -T to skip${NC}"
        deactivate 2>/dev/null || true
        return 1
    fi
}

# Sync function - shows what changed
sync_files() {
    # Run tests first (unless skipped)
    if [ "$SKIP_TESTS" = false ] && [ -d "$LOCAL_DIR/tests" ]; then
        if ! run_tests; then
            echo -e "${RED}Sync aborted due to failing tests${NC}"
            return 1
        fi
        echo ""
    fi

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

# Restart the Berry app via systemd
restart_app() {
    echo -e "${BLUE}🔄 Restarting...${NC}"
    
    # Reload systemd config in case service file changed, then restart
    ssh $PI_HOST "sudo systemctl daemon-reload && sudo systemctl restart berry-native" 2>/dev/null
    
    # Wait for service to start
    sleep 1
    
    # Check status
    if ssh $PI_HOST "systemctl is-active --quiet berry-native" 2>/dev/null; then
        echo -e "${GREEN}✓ Running${NC}"
    else
        echo -e "${RED}✗ Failed to start${NC}"
        ssh $PI_HOST "sudo journalctl -u berry-native -n 10 --no-pager" 2>/dev/null || true
        ssh $PI_HOST "tail -10 /home/admin/berry/berry.log" 2>/dev/null || true
    fi
}

# Start log streaming in background
start_logs() {
    kill $LOG_PID 2>/dev/null || true
    sleep 0.2
    
    ssh $PI_HOST 'tail -f /home/admin/berry/berry.log 2>/dev/null' 2>/dev/null | while IFS= read -r line; do
        if [ "$VERBOSE" = true ]; then
            # Verbose mode: show everything, just add colors
            case "$line" in
                *"[ERROR]"*|*"[CRITICAL]"*|*"Traceback"*|*"Error:"*)
                    echo -e "${RED}$line${NC}"
                    ;;
                *"[WARNING]"*)
                    echo -e "${YELLOW}$line${NC}"
                    ;;
                *"[INFO]"*)
                    echo -e "${CYAN}$line${NC}"
                    ;;
                *"[DEBUG]"*)
                    echo -e "${DIM}$line${NC}"
                    ;;
                *)
                    echo "$line"
                    ;;
            esac
        else
            # Normal mode: show only important logs
            case "$line" in
                *"[ERROR]"*|*"[CRITICAL]"*|*"Traceback"*|*"Error:"*)
                    echo -e "${RED}$line${NC}"
                    ;;
                *"[WARNING]"*)
                    echo -e "${YELLOW}$line${NC}"
                    ;;
                *"[PROFILER]"*)
                    # Always show profiler output (cyan/bold)
                    echo -e "${CYAN}$line${NC}"
                    ;;
                *"[INFO]"*)
                    # Only show important actions
                    case "$line" in
                        *"STARTUP"*|*"Starting"*|*"started"*|*"Entering"*|\
                        *"Playing"*|*"Pausing"*|*"Resuming"*|*"Stopped"*|\
                        *"Saving"*|*"Deleting"*|*"Saved"*|\
                        *"Volume"*|*"Sleep"*|*"Wake"*|*"WAKE"*|\
                        *"Connected"*|*"CONNECTION"*|*"Disconnected"*|\
                        *"SIGTERM"*|*"SIGINT"*|*"shutting down"*|*"Shutdown"*|\
                        *"TempItem"*|*"Syncing"*|*"Context"*|\
                        *"profiler"*|*"PROFILER"*|*"GPU"*|*"SOFTWARE"*)
                            echo -e "${CYAN}$line${NC}"
                            ;;
                    esac
                    ;;
                *"====="*)
                    # Startup banners
                    echo -e "${GREEN}$line${NC}"
                    ;;
            esac
        fi
    done &
    LOG_PID=$!
}

# Initial sync
sync_files
echo ""

# Start/setup services on Pi
echo -e "${BLUE}🚀 Starting Berry...${NC}"

# Create systemd override for profile mode
if [ "$PROFILE" = true ]; then
    ssh $PI_HOST "sudo mkdir -p /etc/systemd/system/berry-native.service.d && echo -e '[Service]\nEnvironment=BERRY_PROFILE=1' | sudo tee /etc/systemd/system/berry-native.service.d/profile.conf > /dev/null"
else
    ssh $PI_HOST "sudo rm -f /etc/systemd/system/berry-native.service.d/profile.conf 2>/dev/null; true"
fi

ssh -t $PI_HOST << 'ENDSSH'
# Ensure systemd services are linked
sudo ln -sf ~/berry/pi/systemd/berry-*.service /etc/systemd/system/ 2>/dev/null
sudo systemctl daemon-reload

# Stop any orphan processes first
pkill -9 -f "berry.py" 2>/dev/null || true
pkill -9 -f "go-librespot" 2>/dev/null || true
sleep 0.5

# Start librespot if not running
if ! systemctl is-active --quiet berry-librespot; then
    sudo systemctl start berry-librespot
    sleep 2
fi

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

# Start Berry via systemd
sudo systemctl restart berry-native
sleep 1

if systemctl is-active --quiet berry-native; then
    echo "✓ Berry"
else
    echo "✗ Berry failed"
    sudo journalctl -u berry-native -n 5 --no-pager
    cat /home/admin/berry/berry.log 2>/dev/null || true
fi
ENDSSH

echo ""
echo -e "${GREEN}✓ Berry running on Pi${NC}"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}r${NC}/Enter  Sync + Restart"
echo -e "  ${BLUE}s${NC}        Sync only"
echo -e "  ${CYAN}t${NC}        Run tests locally"
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
            t)
                echo ""
                run_tests
                echo ""
                ;;
            l)
                echo ""
                echo -e "${CYAN}━━━ Recent logs ━━━${NC}"
                ssh $PI_HOST 'tail -20 /home/admin/berry/berry.log' 2>/dev/null
                echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━${NC}"
                echo ""
                ;;
            q)
                cleanup
                ;;
        esac
    fi
done

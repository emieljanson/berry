#!/bin/bash
# Script om alle audioboeken op te halen van onlinebibliotheek.nl

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Activate virtual environment
if [ -f "$PROJECT_ROOT/venv/bin/activate" ]; then
    source "$PROJECT_ROOT/venv/bin/activate"
else
    echo "❌ Virtual environment not found at $PROJECT_ROOT/venv"
    echo "   Please run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Check if credentials are provided as arguments
if [ $# -ge 2 ]; then
    USERNAME="$1"
    PASSWORD="$2"
    OUTPUT_FILE="${3:-audiobooks.json}"
else
    # Ask for credentials
    echo "📚 Onlinebibliotheek.nl Audiobooks Downloader"
    echo "=============================================="
    echo ""
    read -p "Username: " USERNAME
    read -sp "Password: " PASSWORD
    echo ""
    echo ""
    read -p "Output file [audiobooks.json]: " OUTPUT_FILE
    OUTPUT_FILE="${OUTPUT_FILE:-audiobooks.json}"
fi

# Run the Python script
echo ""
echo "🚀 Starting download..."
echo ""

cd "$SCRIPT_DIR"
python3 get_all_audiobooks.py "$USERNAME" "$PASSWORD" "$OUTPUT_FILE"

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "✅ Done! Output saved to: $OUTPUT_FILE"
else
    echo ""
    echo "❌ Error occurred (exit code: $EXIT_CODE)"
    exit $EXIT_CODE
fi



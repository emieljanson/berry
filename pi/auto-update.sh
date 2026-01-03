#!/bin/bash
# Berry Auto-Update Script
# Runs via cron, checks GitHub and applies ALL changes

cd ~/berry || exit 1

# Get current branch name (could be main or master)
BRANCH=$(git rev-parse --abbrev-ref HEAD)

# Check if there are updates
git fetch origin "$BRANCH" 2>/dev/null || exit 0

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL" = "$REMOTE" ]; then
  exit 0  # No updates
fi

echo "$(date): Updates found on $BRANCH, applying..."

# Pull changes
git pull origin "$BRANCH" || exit 1

# ============================================
# 1. Update dependencies if package.json changed
# ============================================
if git diff --name-only "$LOCAL" "$REMOTE" | grep -q "package.json"; then
  echo "Updating dependencies..."
  cd ~/berry/backend && npm install --production
  cd ~/berry/frontend && npm install
fi

# ============================================
# 2. Rebuild frontend
# ============================================
echo "Building frontend..."
cd ~/berry/frontend && npm run build

# ============================================
# 3. Update systemd services
# ============================================
echo "Updating systemd services..."
for f in ~/berry/pi/systemd/*.service; do
  [ -f "$f" ] || continue
  name=$(basename "$f")
  sudo ln -sf "$f" "/etc/systemd/system/$name"
done
sudo systemctl daemon-reload

# ============================================
# 4. Restart services
# ============================================
echo "Restarting services..."
sudo systemctl restart berry-librespot berry-backend berry-frontend

# ============================================
# 6. Run any migration script if present
# ============================================
if [ -f ~/berry/pi/migrate.sh ]; then
  echo "Running migration script..."
  bash ~/berry/pi/migrate.sh
fi

echo "$(date): Update complete!"

#!/bin/bash
# Deploy the Kindle-side files to a jailbroken Kindle Scribe via SCP.
# Usage: ./deploy.sh <kindle-ip> [ssh-key]
#
# This copies the KUAL extension and scripts to the Kindle.

set -e

KINDLE_IP="${1:?Usage: $0 <kindle-ip> [ssh-key-path]}"
SSH_KEY="${2:-}"
SSH_USER="root"
SSH_PORT="2222"
COMMON_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

if [ -n "$SSH_KEY" ]; then
    COMMON_OPTS="$COMMON_OPTS -i $SSH_KEY"
fi

SSH_OPTS="-p $SSH_PORT $COMMON_OPTS"
SCP_OPTS="-P $SSH_PORT $COMMON_OPTS"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KINDLE_DIR="$SCRIPT_DIR/kindle"

echo "Deploying to Kindle at $KINDLE_IP..."

# Create directories on Kindle
ssh $SSH_OPTS "$SSH_USER@$KINDLE_IP" "
    mkdir -p /mnt/us/extensions/kindle-tablet/bin
"

# Copy KUAL extension
echo "Copying KUAL extension..."
scp $SCP_OPTS -r "$KINDLE_DIR/kual/kindle-tablet/config.xml" \
    "$SSH_USER@$KINDLE_IP:/mnt/us/extensions/kindle-tablet/"
scp $SCP_OPTS -r "$KINDLE_DIR/kual/kindle-tablet/menu.json" \
    "$SSH_USER@$KINDLE_IP:/mnt/us/extensions/kindle-tablet/"

# Copy scripts
echo "Copying scripts..."
scp $SCP_OPTS "$KINDLE_DIR/bin/tablet-mode.sh" \
    "$SSH_USER@$KINDLE_IP:/mnt/us/extensions/kindle-tablet/bin/"
scp $SCP_OPTS "$KINDLE_DIR/kual/kindle-tablet/bin/start.sh" \
    "$SSH_USER@$KINDLE_IP:/mnt/us/extensions/kindle-tablet/bin/"
scp $SCP_OPTS "$KINDLE_DIR/kual/kindle-tablet/bin/stop.sh" \
    "$SSH_USER@$KINDLE_IP:/mnt/us/extensions/kindle-tablet/bin/"
scp $SCP_OPTS "$KINDLE_DIR/kual/kindle-tablet/bin/status.sh" \
    "$SSH_USER@$KINDLE_IP:/mnt/us/extensions/kindle-tablet/bin/"

# Copy tablet-ui binary (GTK UI app)
if [ -f "$KINDLE_DIR/bin/tablet-ui" ]; then
    echo "Copying tablet-ui binary..."
    scp $SCP_OPTS "$KINDLE_DIR/bin/tablet-ui" \
        "$SSH_USER@$KINDLE_IP:/mnt/us/extensions/kindle-tablet/bin/"
else
    echo "WARNING: tablet-ui binary not found!"
    echo "  Build it first:  make -C kindle gtk-ui MESON_CROSS=<path>"
fi

# Make everything executable
echo "Setting permissions..."
ssh $SSH_OPTS "$SSH_USER@$KINDLE_IP" "
    chmod +x /mnt/us/extensions/kindle-tablet/bin/*
"

echo ""
echo "Deployment complete!"
echo "Open KUAL on your Kindle to see 'Kindle Tablet' in the menu."
echo ""
echo "To use:"
echo "  1. On PC:  kindle-tablet --host $KINDLE_IP"
echo "  Or via KUAL on Kindle first, then: kindle-tablet --host $KINDLE_IP"

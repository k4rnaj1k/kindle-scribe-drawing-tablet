#!/bin/bash
# download-and-deploy.sh
#
# Downloads the latest successful main-branch CI build from GitHub and
# deploys the compiled binaries (tablet-daemon, tablet-ui) to the Kindle.
#
# Usage:
#   ./download-and-deploy.sh [ssh-key-path]
#
# Requirements:
#   - gh CLI (https://cli.github.com/) installed and authenticated
#   - SSH access to the Kindle (jailbroken, SSH enabled)

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
KINDLE_IP="192.168.50.73"
SSH_USER="root"
SSH_PORT="2222"
REMOTE_BIN="/mnt/us/extensions/kindle-tablet/bin"
REPO="k4rnaj1k/kindle-scribe-drawing-tablet"
WORKFLOW="build.yml"
SSH_KEY="${1:-}"
# ─────────────────────────────────────────────────────────────────────────────

COMMON_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
if [ -n "$SSH_KEY" ]; then
    COMMON_OPTS="$COMMON_OPTS -i $SSH_KEY"
fi
SSH_OPTS="-p $SSH_PORT $COMMON_OPTS"
SCP_OPTS="-P $SSH_PORT $COMMON_OPTS"

# ── Sanity checks ─────────────────────────────────────────────────────────────
if ! command -v gh &>/dev/null; then
    echo "ERROR: gh CLI not found."
    echo "  Install: https://cli.github.com/"
    exit 1
fi

if ! gh auth status &>/dev/null; then
    echo "ERROR: gh CLI is not authenticated. Run: gh auth login"
    exit 1
fi

# ── Find latest successful run on main ───────────────────────────────────────
echo "Looking up latest successful build on main..."
RUN_ID=$(gh run list \
    --repo "$REPO" \
    --branch main \
    --workflow "$WORKFLOW" \
    --status success \
    --limit 1 \
    --json databaseId,headSha,createdAt \
    --jq '.[0] | "\(.databaseId) \(.headSha[0:8]) \(.createdAt)"')

if [ -z "$RUN_ID" ]; then
    echo "ERROR: No successful build found on main branch."
    echo "  Check: https://github.com/$REPO/actions"
    exit 1
fi

RUN_DBID=$(echo "$RUN_ID" | awk '{print $1}')
RUN_SHA=$(echo "$RUN_ID"  | awk '{print $2}')
RUN_DATE=$(echo "$RUN_ID" | awk '{print $3}')
echo "  Run ID : $RUN_DBID"
echo "  Commit : $RUN_SHA"
echo "  Date   : $RUN_DATE"

# ── Download artifact into a temp dir ────────────────────────────────────────
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo ""
echo "Downloading artifact from run $RUN_DBID..."
gh run download "$RUN_DBID" \
    --repo "$REPO" \
    --dir "$TMPDIR"

# Artifact zip preserves the path from the workflow: kindle/bin/{binary}
DAEMON=$(find "$TMPDIR" -name "tablet-daemon" | head -1)
UI=$(find     "$TMPDIR" -name "tablet-ui"     | head -1)

if [ -z "$DAEMON" ] || [ -z "$UI" ]; then
    echo "ERROR: Could not find binaries in downloaded artifact."
    echo "  Contents of $TMPDIR:"
    find "$TMPDIR" -type f
    exit 1
fi

echo "  tablet-daemon : $DAEMON"
echo "  tablet-ui     : $UI"

# ── Deploy ───────────────────────────────────────────────────────────────────
echo ""
echo "Deploying to $SSH_USER@$KINDLE_IP:$REMOTE_BIN ..."

echo "  Creating remote directory..."
# shellcheck disable=SC2029
ssh $SSH_OPTS "$SSH_USER@$KINDLE_IP" "mkdir -p $REMOTE_BIN"

echo "  Copying tablet-daemon..."
scp $SCP_OPTS "$DAEMON" "$SSH_USER@$KINDLE_IP:$REMOTE_BIN/tablet-daemon"

echo "  Copying tablet-ui..."
scp $SCP_OPTS "$UI"     "$SSH_USER@$KINDLE_IP:$REMOTE_BIN/tablet-ui"

echo "  Setting execute permissions..."
ssh $SSH_OPTS "$SSH_USER@$KINDLE_IP" "chmod +x $REMOTE_BIN/tablet-daemon $REMOTE_BIN/tablet-ui"

echo ""
echo "Done!  Deployed commit $RUN_SHA to $KINDLE_IP:$REMOTE_BIN"
echo ""
echo "Next step on PC:"
echo "  kindle-tablet --host $KINDLE_IP"

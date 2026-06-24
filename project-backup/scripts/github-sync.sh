#!/bin/bash
# ============================================================
# GitHub Auto-Sync Script
# Syncs /home/z/my-project/ deliverables to GitHub repo as backup
# Token is read from .github-token file (not committed to repo)
# ============================================================

TOKEN_FILE="/home/z/my-project/.github-token"
REPO="rapdominecraft212-pixel/teste"
BRANCH="main"
PROJECT_DIR="/home/z/my-project"
SYNC_DIR="/tmp/github-sync-repo"

# Read token from file
if [ ! -f "$TOKEN_FILE" ]; then
    echo "❌ Token file not found at $TOKEN_FILE"
    exit 1
fi
GITHUB_TOKEN=$(cat "$TOKEN_FILE" | tr -d '\n')

echo "🔄 GitHub Sync Started - $(date)"

# Clone or update the repo
if [ -d "$SYNC_DIR/.git" ]; then
    echo "📦 Updating existing clone..."
    cd "$SYNC_DIR"
    git pull origin "$BRANCH" 2>/dev/null || true
else
    echo "📦 Cloning repository..."
    rm -rf "$SYNC_DIR"
    git clone "https://${GITHUB_TOKEN}@github.com/${REPO}.git" "$SYNC_DIR" 2>&1
    cd "$SYNC_DIR"
fi

# Make sure we're on the right branch
git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH"

# Create backup directories in the repo
mkdir -p "$SYNC_DIR/project-backup/scripts"
mkdir -p "$SYNC_DIR/project-backup/download"

# Sync ONLY important files (scripts, download, worklog)
# Exclude token file, skills, and other internal directories
echo "📋 Copying important files..."
rsync -av \
    --include='/scripts/***' \
    --include='/download/***' \
    --include='/worklog.md' \
    --include='/.gitignore' \
    --exclude='*' \
    "$PROJECT_DIR/" "$SYNC_DIR/project-backup/"

# Stage all changes
cd "$SYNC_DIR"
git add -A

# Check if there are changes to commit
if git diff --staged --quiet; then
    echo "✅ No changes to sync - everything is up to date!"
else
    # Commit and push
    TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S %Z")
    git commit -m "Auto-sync backup - $TIMESTAMP"
    
    echo "🚀 Pushing to GitHub..."
    git push "https://${GITHUB_TOKEN}@github.com/${REPO}.git" "$BRANCH" 2>&1
    
    if [ $? -eq 0 ]; then
        echo "✅ Sync completed successfully!"
    else
        echo "❌ Push failed! Check network or token permissions."
        exit 1
    fi
fi

echo "🔄 GitHub Sync Finished - $(date)"

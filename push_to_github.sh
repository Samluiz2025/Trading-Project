#!/bin/bash
set -e

# Run this from inside your Trading-Project folder after unzipping the v2 files
# Usage: bash push_to_github.sh

REPO_URL="https://github.com/Samluiz2025/Trading-Project.git"

echo "Configuring git..."
git config user.email "tradingbot@tipv2.com"
git config user.name "TIP v2 Upgrade"

echo "Adding remote..."
git remote set-url origin "$REPO_URL" 2>/dev/null || git remote add origin "$REPO_URL"

echo "Staging all changes..."
git add -A

echo "Committing..."
git commit -m "v2: scoring engine, 4TF alignment, ATR stops, quality gates, new dashboard" || true

echo "Pushing to GitHub..."
git push origin main
echo "Done!"

#!/bin/bash
# Deploy to Fly.io
# Prerequisites: brew install flyctl && fly auth login

set -e

echo "🚀 Deploying Linear Enhancer to Fly.io..."

# Check if app exists
if ! fly status 2>/dev/null; then
    echo "📦 Creating new Fly app..."
    fly launch --no-deploy --name linear-enhancer
    
    # Create persistent volume
    echo "💾 Creating persistent volume..."
    fly volumes create linear_data --size 1 --region iad
fi

# Set secrets (prompt if not set)
echo "🔐 Setting secrets..."
echo "Enter your secrets (press Enter to skip if already set):"

read -p "ANTHROPIC_API_KEY: " ANTHROPIC_KEY
[ -n "$ANTHROPIC_KEY" ] && fly secrets set ANTHROPIC_API_KEY="$ANTHROPIC_KEY"

read -p "LINEAR_API_KEY: " LINEAR_KEY
[ -n "$LINEAR_KEY" ] && fly secrets set LINEAR_API_KEY="$LINEAR_KEY"

read -p "SLACK_TOKEN: " SLACK_KEY
[ -n "$SLACK_KEY" ] && fly secrets set SLACK_TOKEN="$SLACK_KEY"

read -p "GH_TOKEN (for gh cli): " GH_KEY
[ -n "$GH_KEY" ] && fly secrets set GH_TOKEN="$GH_KEY"

# Deploy
echo "🚢 Deploying..."
fly deploy

# Set up cron for sync (runs every 30 minutes)
echo "⏰ To set up scheduled sync, run:"
echo "   fly machine run . --schedule '*/30 * * * *' -e DOCS_DIR=/data --command 'uv run python -m src.main sync'"

echo ""
echo "✅ Deployed! Your webhook URL is:"
fly status | grep "https://"


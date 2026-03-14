#!/bin/bash
# Start the Polymarket War Room dashboard
PORT=3420
DIR="$(cd "$(dirname "$0")/dashboard" && pwd)"

echo "🐍 Starting OPENCLAW War Room..."
echo "📁 Serving: $DIR"
echo "🌐 URL: http://localhost:$PORT/warroom.html"
echo ""

# Kill any existing server on this port
lsof -ti:$PORT | xargs kill -9 2>/dev/null || true

# Start server
cd "$DIR" && python3 -m http.server $PORT &
SERVER_PID=$!
echo "✓ Server PID: $SERVER_PID"

# Open browser
sleep 0.5
open "http://localhost:$PORT/warroom.html"

echo "✓ Opening browser..."
echo "Press Ctrl+C to stop the server"
wait $SERVER_PID

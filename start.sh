#!/bin/bash
# ─────────────────────────────────────────────────
# agnostic_mechanical_arb_bot — start everything
# ─────────────────────────────────────────────────

PORT=3420
DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "  🐍 OPENCLAW ALPHA — WAR ROOM"
echo "  ─────────────────────────────"

# Kill any existing server on the port
if lsof -ti:$PORT &>/dev/null 2>&1; then
  echo "  ↻ Restarting dashboard server..."
  kill $(lsof -ti:$PORT) 2>/dev/null
  sleep 0.5
fi

# Start dashboard server
cd "$DIR/dashboard"
python3 -m http.server $PORT --bind 127.0.0.1 &>/dev/null &
SERVER_PID=$!
echo "  ✓ Dashboard server started (PID $SERVER_PID)"
echo "  ✓ http://localhost:$PORT/warroom.html"
echo ""

# Open browser
sleep 0.5
if command -v open &>/dev/null; then
  open "http://localhost:$PORT/warroom.html"
elif command -v xdg-open &>/dev/null; then
  xdg-open "http://localhost:$PORT/warroom.html"
fi

echo "  Pages available:"
echo "  → War Room     http://localhost:$PORT/warroom.html"
echo "  → TUI          http://localhost:$PORT/tui.html"
echo "  → Dashboard    http://localhost:$PORT/index.html"
echo "  → Scanner      http://localhost:$PORT/scanner.html"
echo "  → Trades       http://localhost:$PORT/trades.html"
echo "  → Wallet       http://localhost:$PORT/wallet.html"
echo ""
echo "  Press Ctrl+C to stop"
echo ""

# Keep alive
wait $SERVER_PID

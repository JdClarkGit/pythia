# Build Task: OpenClaw Polymarket War Room — 2D Sims-like Game

Build a complete 2D top-down office game as a single HTML file: `dashboard/game.html`
Use Phaser 3 (from CDN: https://cdn.jsdelivr.net/npm/phaser@3.60.0/dist/phaser.min.js)
NO external image files — draw everything programmatically with Phaser Graphics.

## Core Concept
Commander (user) watches 4 AI snake agents in a top-down office. Agents have idle behaviors at desks. When user triggers a scrum, agents walk to the meeting table, speak in speech bubbles (powered by real Anthropic API calls), then return to desks.

## Canvas & Layout
- Canvas: 1200 x 620px
- Background: #0a0a0a floor with subtle #111 grid lines (32px grid)
- DOM overlay below canvas: Commander input bar

## Office Elements (draw with Phaser Graphics)

### Room
- Dark floor #0f0f0f with #1a1a1a grid lines every 32px
- Room border: #1e1e1e, 4px thick
- Ambient glow effects on screens

### Desks (4 total) — draw as rounded rectangles
- BACKEND desk: x=150, y=150, 90x70, fill #1a1a2e, border #3B82F6
  - Monitor on desk: 50x35, glowing blue, shows "CHAIN DATA" ticker text
- FRONTEND desk: x=960, y=150, 90x70, fill #1a1a2e, border #8B5CF6
  - Monitor: glowing purple, shows "ORDER BOOK" text
- MAIN desk: x=150, y=430, 90x70, fill #1a1a2e, border #F59E0B
  - Monitor: glowing gold, shows "SYNTHESIS" text
- SCRUM desk: x=960, y=430, 90x70, fill #1a1a2e, border #10B981
  - Monitor: glowing green, shows "TICKETS: 41" text

### Meeting Table (center)
- x=520, y=270, 200x120, fill #161616, border #333
- 4 chairs (small 20x20 squares) at each side
- Label "WAR ROOM" in #444 on table surface

### Wall Screens
- LEFT SCREEN: x=20, y=200, 80x240, fill #0d0d0d, border #EF4444
  - Title "🔴 LIVE ON X" at top
  - Scrolling list of X Spaces names
- RIGHT SCREEN: x=1100, y=200, 80x240, fill #0d0d0d, border #F59E0B  
  - Title "📊 POLY" at top
  - Live market prices scrolling
- TOP CENTER: x=480, y=10, 280x40 — "🐍 WAR ROOM — Polymarket Alpha Standup"

### Door
- Top center: x=580, y=0, 40x20, fill #222, border #444
- Label "EXIT" above it

## Agent Sprites (draw with Phaser Graphics, NO external images)

Each agent is drawn as:
1. Body circle (radius 18px) in agent color
2. Smaller head highlight circle (radius 8px, lighter color) offset up-right
3. Eyes: 2 tiny white circles
4. Accessory drawn on top:
   - BACKEND (blue #3B82F6): small wrench shape (2 rectangles)
   - FRONTEND (purple #8B5CF6): small magnifying glass (circle + line)
   - MAIN (gold #F59E0B): small crown (3 triangles)
   - SCRUM (green #10B981): small stopwatch (circle + tick marks)
5. Name tag text below: small white text with agent name

Agent starting positions (at their desks):
- BACKEND: x=195, y=155
- FRONTEND: x=1005, y=155
- MAIN: x=195, y=435
- SCRUM: x=1005, y=435

Meeting chairs (where agents walk to during scrum):
- BACKEND chair: x=530, y=295
- FRONTEND chair: x=690, y=295
- MAIN chair: x=530, y=365
- SCRUM chair: x=690, y=365

## Animations

### Idle (at desk)
- Continuous gentle bob: y ± 3px using Phaser tween, yoyo:true, repeat:-1, duration:1200
- Randomly look left/right every 3-6 seconds (flip sprite horizontally)
- Monitor glow pulses slowly

### Walking
- When summoned, stop idle tween
- Tween x,y to meeting chair over 1200ms with ease:'Sine.easeInOut'
- During walk: alternate small rotation ±5deg to simulate walking steps (duration 200ms, repeat for walk duration)
- When walking, leave a very faint trail effect

### Speaking
- Scale pulse: 1.0 → 1.08 → 1.0 during speech bubble display
- Speech bubble appears above head, slides down 8px into position

### Returning
- Reverse walk back to desk position
- Resume idle bob

## Speech Bubbles

Draw as Phaser GameObjects.Container containing:
1. Rounded rectangle (RoundedRect) background: fill #111, stroke agent color, alpha 0.95
2. Small triangle "tail" pointing down toward agent
3. Agent name text: colored, bold, 11px, top of bubble
4. Content text: white, 11px, line-wrap at 240px, Courier New
5. Timestamp text: grey, 9px, bottom right
6. "✓ Seen" text: appears 2s after bubble shows

Bubble appears above agent head with slideDown animation (y-12 → y, opacity 0→1, 300ms)
Bubble auto-dismisses after 8 seconds with fade out
Only one bubble visible per agent at a time

## Game State Machine

States:
1. IDLE — agents at desks, idle animations, monitors ticking
2. SUMMONING — agents walk to table (sequential: scrum first, then others)
3. MEETING — agents seated, speak in order: scrum → backend → frontend → main → scrum closes
4. RETURNING — agents walk back to desks
5. DIRECT_QUERY — single agent responds without meeting

State transitions:
- IDLE → SUMMONING: user says "scrum" / "meeting" / "hey guys"
- SUMMONING → MEETING: all agents reached chairs
- MEETING → RETURNING: scrum closes meeting
- RETURNING → IDLE: all agents back at desks

## Anthropic API Integration

API key stored in window.OPENCLAW_API_KEY (set via DOM input)
Model: claude-opus-4-5

System prompts per agent:
- BACKEND: "You are the Backend Agent in a Polymarket war room. Specialty: on-chain data, whale wallets, USDC flows. Keep response to 2-3 sentences. Start ONLY on first appearance with '🚪 *walks in*'. End with Signal: HIGH/MEDIUM/LOW."
- FRONTEND: "You are the Frontend Agent. Specialty: order book microstructure, bid-ask spreads, OB imbalance, VWAP, price magnets at 25c/75c. 2-3 sentences. Signal rating at end."
- MAIN: "You are the Main Agent — alpha synthesizer. Lead with SYNTHESIS:. Give specific market, price, fair value, confidence %. Format: 🎯 ALPHA: [action] on [market] at [X]¢, target [Y]¢, [Z]% confidence."
- SCRUM: "You are SCRUM Master. Open meetings with roll call, close with ticket #ALPHA-N and position sizing. Max 2 sentences."

Call sequence during scrum:
1. Call SCRUM first with context: market snapshot
2. Wait for response, show bubble, then call BACKEND
3. Wait, show bubble, call FRONTEND
4. Wait, show bubble, call MAIN with backend+frontend context
5. Wait, show bubble, call SCRUM to close with main's output

For direct agent queries (click on agent):
- Show a small dialog prompt above the agent
- Call that agent's API with the user's question

## Market Data (simulate — no real API key needed for this)
Animate these values with small random ±2% fluctuations every 5 seconds:
- Fed Rate Cut June: 72¢ (+4¢)
- Trump Win 2028: 48¢ (-3¢)
- BTC >$100K EOY: 63¢ (+8¢)
- US Recession: 31¢ (+11¢)
- Ukraine Ceasefire: 19¢ (+6¢)

X Spaces (static, scroll on left screen):
- MartyParty: "Liquidation Levels" 30K
- WOLF Bitcoin: "BTC. Macro. War." 45K
- Simon Dixon: "Iran War Week 2" 18K
- JohnAnthony: "AMA + Giveaway" 12K

## DOM Overlay (below the canvas)

Top bar (above canvas, height 40px):
- Left: "OPENCLAW ALPHA ● LIVE — WAR ROOM"
- Right: live clock + view counter + API key button

Commander bar (below canvas, height 80px, background #080808):
- Left: "🎖️ COMMANDER" label + mic icon button (Web Speech API)
- Center: text input, placeholder 'Give orders... or "Hey guys, let\'s have a scrum"'
- Right: SEND button
- Quick buttons row above input: "🐍 Start Scrum" | "🎯 Top Alpha?" | "🔗 On-chain?" | "📖 Order book?"

Alpha Alert popup (shows when MAIN reports >70% confidence):
- Fixed center overlay
- Red border, shows market, confidence, action
- [EXECUTE] [DISMISS] buttons

## Additional Visual Polish
- Particle effect when agent starts speaking (small colored sparkles)
- Screen glow effect on wall monitors (draw colored rectangle with low alpha behind them)
- Faint light circles under each agent (their "shadow")
- When all agents are at meeting table, slightly darken desk areas
- Typewriter effect for speech bubble text (reveal character by character, 25ms per char)
- Agent blinks occasionally (tiny animation on eyes)

## File Output
Single file: dashboard/game.html
Must work by opening directly from: http://localhost:3420/game.html

## Notes
- No external files needed — all graphics are procedural Phaser Graphics
- Phaser canvas is 1200x620, total page height accounts for DOM bars above/below
- Store per-agent conversation history for multi-turn API calls
- Handle API errors gracefully (show "[API ERROR]" in speech bubble)
- If no API key set, show mock responses for demo

When completely finished run:
openclaw system event --text "Done: Polymarket War Room Phaser 3 game built" --mode now

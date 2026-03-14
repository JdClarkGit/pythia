# CHANGELOG — OpenClaw Corp.

All improvements logged here. Public-facing where appropriate.

---

## 2026-03-13

### v0.1.0 — Corporate Soul File loaded
- Received and stored `SOUL_CORP.md` — defines OpenClaw Corp. identity, two divisions, exec team roles
- Updated all 4 agent system prompts across `warroom.html` and `game.html` to exec team roles:
  - Backend → CTO
  - Frontend → CPO
  - Main → CSO
  - Scrum → COO/CFO
- Self-improvement loop: exec team context now baked into every standup

### v0.0.2 — Phaser 3 War Room Game
- Built `dashboard/game.html` — 2D top-down office, 4 snake agents walk to meeting table on scrum trigger
- Fixed layout to be screen-size-relative (layout object `L`) so all agents visible on any screen

### v0.0.1 — Trading Bot Foundation
- 65 files, 7,237 lines Python + 544 lines Rust
- Strategies: merge arb, maker arb, mean reversion, price magnet, capital recycler
- Dashboard: `warroom.html`, `tui.html`, `game.html` on `http://localhost:3420`

# CompliAgent 🛰️ — living compliance radar for regulated SMBs

Monitors regulatory changes, maps each one to **this** business's profile, and generates concrete
action items with evidence. Moat = the reg-change → obligation mapping dataset. Future: an ambient
agent that files and renews obligations automatically (see the *Ambient mode (2036)* panel).

Indigo/steel futuristic console with a sweeping **radar** hero (reg blips colour-coded by domain,
distance = urgency), an obligations board, and a compliance-score gauge.

## Run
```bash
./run.sh        # foreground  → http://localhost:8808
./run.sh -d     # detached (logs /tmp/compliagent.log)
./run.sh stop | status
```
Python 3 stdlib only. SQLite seeds on first run (business profile + ~15 reg changes + obligations).
Wires a live Claude (`claude-sonnet-4-6`) call for applicability/action-item generation when
`ANTHROPIC_API_KEY` is present in `~/creator-os/.env`, with a deterministic mock fallback.

## Endpoints
`GET /` · `GET /api/health` · `GET /api/status` · `GET /api/ambient` · `POST /api/assess` (reg change + profile → applicability + actions) · `POST /api/bootstrap`

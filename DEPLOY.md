# Deploying Synapse

> The full version of this, with the pre-launch checklist, operations and a section on
> changing things yourself, is **[Synapse-Deployment-Guide.pdf](Synapse-Deployment-Guide.pdf)**.

Three options, cheapest first. All of them keep the same rule: **Ollama is not worth deploying**. Cloud CPUs
run a 7B at a few tokens a second. Anywhere but your own machine, point Synapse at a hosted model with
`SYNAPSE_FAST_*` and treat local as a dev convenience.

---

## Option A — your Mac, always on (recommended)

Your vault, your mail, your machine. No egress, no hosting bill.

```bash
cd ~/Desktop/synapse
source .venv/bin/activate
python -m synapse.api
```

To keep it running after you close the terminal, install a launch agent:

```bash
cat > ~/Library/LaunchAgents/com.synapse.api.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.synapse.api</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOU/Desktop/synapse/.venv/bin/python</string>
    <string>-m</string><string>synapse.api</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/YOU/Desktop/synapse</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/synapse.log</string>
  <key>StandardErrorPath</key><string>/tmp/synapse.err</string>
</dict></plist>
PLIST
launchctl load ~/Library/LaunchAgents/com.synapse.api.plist
```

Replace `YOU`. Check with `tail -f /tmp/synapse.err`, stop with `launchctl unload ...`. Do the same for
`ollama serve` if you want local models available after a reboot (`brew services start ollama`).

Scheduled goals only fire while the process is up, which is the point of the launch agent.

---

## Option B — Docker on your machine or a small VPS

```bash
cp .env.example .env          # set SYNAPSE_FAST_* and SYNAPSE_API_TOKEN
VAULT_PATH=/absolute/path/to/YourVault docker compose up -d --build
docker compose logs -f
```

Notes that matter:

- **Set `SYNAPSE_API_TOKEN`.** Without it every `/api` route is open, and those routes send email.
- The vault is a bind mount, so notes land on the host filesystem and Obsidian sees them live.
- `./data` holds SQLite (runs, memory, checkpoints, schedules). Back that folder up; it's the whole state.
- On a VPS, Ollama isn't available — set the fast provider or nothing will plan.
- Health: `curl localhost:8000/api/health`. Compose restarts the container if the healthcheck fails.

---

## Option C — public URL

Only after a token is set. Two ways:

**Tunnel (simplest, keeps data on your machine):**

```bash
cloudflared tunnel --url http://localhost:8000     # or: ngrok http 8000
```

**VPS (Hetzner/Fly/Railway, ~$5/mo):** run Option B behind Caddy for TLS:

```
synapse.yourdomain.com {
    reverse_proxy 127.0.0.1:8000
}
```

Then set `SYNAPSE_HOST=127.0.0.1` so the container only listens locally and Caddy terminates TLS.

Hard requirements before exposing anything:

1. `SYNAPSE_API_TOKEN` set to a long random string (`openssl rand -hex 32`).
2. Approvals stay on — that's what stops a stranger from mailing your contacts.
3. Google `token.json` and `.env` never committed; `.gitignore` already covers them.
4. Understand the limit: **one token, one user.** There are no accounts. Share the URL with nobody.

---

## Deploying the dashboard only

The API serves the built dashboard, so there is nothing separate to deploy. If you want it on Vercel or
Netlify for a portfolio demo, build with an API base URL and host the API elsewhere — but a read-only demo
usually makes more sense: record a screen capture of a real run and link the repo.

---

## What to back up

`data/` (SQLite: runs, traces, memory, schedules, checkpoints) and your vault. Everything else is code.

## Upgrading

```bash
git pull
uv pip install -e ".[dev]"
cd web && npm install && npm run build && cd ..
pytest -q && python -m evals.run
```

Schema changes are additive (`CREATE TABLE IF NOT EXISTS`), so existing databases keep working.

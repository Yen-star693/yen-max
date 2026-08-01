# Yen Max V2

A Discord bot that generates code and projects on demand using the Groq API,
with real syntax validation, honest error reporting, and resource limits
enforced before any API call is made.

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Environment variables
Copy `.env.example` to `.env` and fill in your real values:
```bash
cp .env.example .env
```

```
TOKEN=your_discord_bot_token_here
GROQ_KEY=your_groq_api_key_here
PORT=8080
```

Never commit `.env` - it's already listed in `.gitignore`.

### 3. Register a bot owner
The first person to run `yen setowner` in Discord becomes the permanent
owner and bypasses all permission checks. Run this once immediately
after inviting the bot:
```
yen setowner
```

### 4. Run locally
```bash
python main.py
```

### 5. Deploy (Render or similar)
- Push to GitHub
- Set `TOKEN` and `GROQ_KEY` as environment variables in your host's dashboard
- The Flask keep-alive server starts automatically on boot

## Commands

| Command | Description |
|---|---|
| `yen build <prompt>` | Generate code or a full project |
| `yen preview <prompt>` | Show the planned file list without generating any code (no API cost for generation) |
| `yen grant @user` | (Admin) Grant global access |
| `yen grantserver @user` | (Admin) Grant access scoped to this server only |
| `yen revoke @user` | (Admin) Revoke global access |
| `yen setowner` | Register yourself as bot owner (one-time, first-come) |

## Architecture

```
main.py          Bot entry point, commands, cooldowns, build locking
config.py        All limits, keywords, and environment loading
errors.py        Real error classification (transient vs permanent)
permissions.py   Global/server-scoped access, atomic JSON writes
analyzer.py      Detects whether a request is a project or a question
groq.py          Groq API wrapper - generation, planning, cleanup
validator.py     Real syntax checking (Python via ast, JSON via json),
                 filename safety checks
builder.py       Orchestrates build workflow, progress messages,
                 honest upload gating and summaries
```

## What's actually real vs. what's shown for UX

Everything shown in the Developer Note corresponds to real work:

- **Syntax checking** runs `ast.parse()` for Python and `json.loads()`
  for JSON. No other language is claimed to be checked - files in
  other languages are generated and uploaded without a fake "checking"
  step, because there's no real parser backing it here.
- **"Detected a syntax issue"** only appears when the parser actually
  raised an error, with the real message and line number.
- **Regeneration** is capped at `MAX_REGENERATION_ATTEMPTS` (see
  `config.py`) per file, and every attempt is really sent to the API
  and really re-validated afterward.
- **The final summary** (files generated / passing / failing /
  regeneration attempted / build time) is computed from the actual
  per-file results, not estimated.
- **Files that still fail validation after the regeneration cap are
  still uploaded** (so you're never left with nothing) but are
  explicitly listed as requiring manual correction in the summary -
  this is never silently glossed over.

## Resource limits (config.py)

| Limit | Purpose |
|---|---|
| `MAX_FILES_PER_BUILD` | Planner output larger than this blocks the build before any file is generated |
| `MAX_FILE_BYTES` | Per-file size ceiling |
| `MAX_TOTAL_BUILD_BYTES` | Combined size ceiling across a whole build |
| `MAX_REGENERATION_ATTEMPTS` | How many times a broken file may be auto-repaired |
| `MAX_TRANSIENT_RETRIES` | Retries for timeouts/rate-limits/5xx only - permanent errors never retry |
| `BUILD_COOLDOWN_SECONDS` | Per-user cooldown between builds |
| `FILENAME_BLOCKED_PATTERNS` | Rejects `..`, `/`, `\`, `~`, null bytes in any planned filename before generation |

All of these are checked **before** the relevant API call, so a
request that would exceed them never spends a token on it.

## Error handling philosophy

`errors.py` splits failures into transient (network drops, timeouts,
rate limits, 5xx) and permanent (bad auth, malformed request, parse
failure). Only transient errors are retried, and only up to
`MAX_TRANSIENT_RETRIES`. Permanent errors fail immediately rather than
burning retries on something that will never succeed.

Successful API responses are cached in memory; failures of any kind
(timeouts, HTTP errors, malformed JSON, empty content) are never
cached, so a bad response can't "poison" a later identical request.

## Known limitations

- Real syntax validation currently covers Python and JSON only.
  Other languages are generated and uploaded without a validation
  step - there's no fake "checking" message for them.
- `yen preview` shows the file plan but not per-file token cost
  estimates (filenames only, since the planner call itself doesn't
  return size estimates).
- No persistent build history yet - each `yen build` is independent.

## Roadmap (not yet implemented)

- Async HTTP client (aiohttp) so API calls don't block the event loop
- Cross-file consistency checking (imports referencing files that
  don't exist, missing env vars referenced in code)
- `yen retry <filename>` to rebuild a single file without re-running
  the whole project (needs session/state storage first)
- ZIP archive download alongside individual attachments
- Structured logging with per-build metrics

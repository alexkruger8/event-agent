# AI Event Intelligence

Talk to an AI agent that watches your event stream, learns how your system behaves, and helps you understand what changed. Bring it where your team already works: Slack, SMS, and your AI coding tools.

No pre-built dashboards. No query writing. Just ask what's going on.

Local-first developer preview. Not hardened for hosted multi-tenant production without additional auth, TLS, deployment, and operations work.

---

## What makes it different

**Conversational by default.** Instead of a metrics dashboard you have to learn, you get a chat interface that already knows your event schema and can query your data to answer questions like *"why did checkouts drop last night?"* or *"what properties does signup have that I could track?"*

**Connects to your existing Kafka topics.** If your system already produces events to Kafka or Redpanda, point the consumer at your topics and get intelligence without changing any application code. If the events need domain context, explain your system in plain English and the agent remembers it for future analysis.

**Your coding agent can see production.** An MCP server exposes anomalies, errors, insights, and metrics to Claude Code and other MCP-compatible agents — so when you ask your coding agent to fix a bug, it already knows what's broken in production.

---

## Quick start

The only hard requirement is an Anthropic or OpenAI API key. The database, demo tenant, and web app run locally.

### 1. Configure

```bash
cp .env.example .env
```

Edit `.env` — the minimum to get started:

```
ANTHROPIC_API_KEY=sk-ant-...
# or
OPENAI_API_KEY=sk-...
```

Everything else can be left blank for now.

### 2. Start

```bash
docker compose up --build
```

This starts PostgreSQL, the API server, a Kafka consumer that waits for tenant broker settings, and a seed job that populates demo data with baked-in anomalies.

### 3. Open the chat

Navigate to **http://localhost:8000/ui/** and click **Demo Tenant**.

You're looking at a chat interface that already has 5 weeks of event history, two anomalies, and a set of insights. Try:

- *"What anomalies are open right now?"*
- *"Walk me through the page_view spike"*
- *"What does the checkout event look like? What properties does it carry?"*
- *"Track the checkout amount so we get avg and p95 metrics going forward"*

The agent can run SQL against your data, learn what your events mean, and configure metric tracking — all through conversation.

---

## Connecting your event stream

### Kafka / Redpanda (recommended)

If you already produce events to Kafka or Redpanda topics, configure the external broker from the tenant **Settings** tab. The consumer auto-subscribes to non-internal topics on that tenant's broker and routes messages using the tenant's include/exclude patterns.

The consumer expects JSON messages with at minimum an event name field (configurable — defaults to `event_name`). Any other fields land in `properties`. `user_id` and `timestamp` are extracted automatically if present.

For error topics, set the tenant's **Error topic pattern**. Matching topics are ingested as errors instead of events. The consumer maps common field aliases (`type`/`error_type`, `msg`/`message`, `stack_trace`/`stacktrace`/`stack`) automatically.

**External cluster:** enter the tenant-specific Broker address and SASL/TLS settings in the tenant Settings tab. Tenant Kafka credentials are encrypted in the database with `KAFKA_CREDENTIAL_ENCRYPTION_KEY`.

Each tenant consumes with its own consumer group:

```text
ai-events-<tenant-id>
```

Grant that group `READ` and `DESCRIBE` ACLs, along with `READ` and `DESCRIBE` on the topics the tenant should ingest.

Generate the encryption key once and keep it stable for this deployment:

```bash
python -c "from app.security.encryption import generate_encryption_key; print(generate_encryption_key())"
```

Then set `KAFKA_CREDENTIAL_ENCRYPTION_KEY` in `.env` before saving tenant passwords.

Kafka settings are refreshed by the running consumer every `KAFKA_TOPIC_REFRESH_INTERVAL_SECONDS` seconds, so saving tenant settings does not require restarting the worker.

### REST API

For lower-volume sources or testing, send events directly:

```bash
# Single event
curl -X POST http://localhost:8000/events/$TENANT_ID \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "event_name": "checkout",
    "user_id": "user_123",
    "properties": {"amount": 49.99, "plan": "pro"}
  }'

# Batch (up to 1000 per request)
curl -X POST http://localhost:8000/events/$TENANT_ID/batch \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [
      {"event_name": "page_view", "user_id": "user_123"},
      {"event_name": "signup",    "user_id": "user_456"}
    ]
  }'
```

### Error tracking

Errors are a first-class entity with fingerprint-based deduplication (SHA-256 of `error_type + message + service`). Repeated errors increment an occurrence counter rather than creating duplicate rows.

```bash
curl -X POST http://localhost:8000/errors/$TENANT_ID \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "error_type": "TimeoutError",
    "message": "Payment gateway timed out after 30s",
    "service": "payment-api",
    "severity": "error"
  }'
```

---

## MCP integration (for coding agents)

The platform ships an MCP server that exposes production intelligence to Claude Code and other MCP-compatible agents over stdio transport.

### Setup

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "ai-event-intelligence": {
      "command": "python",
      "args": ["-m", "app.mcp"],
      "env": {
        "MCP_DATABASE_URL": "postgresql+psycopg://mcp_reader:<password>@127.0.0.1:5433/events",
        "MCP_DEFAULT_TENANT_ID": "<your-tenant-id>",
        "ANTHROPIC_API_KEY": "<your-key>"
      }
    }
  }
}
```

For local experiments you can point `MCP_DATABASE_URL` at the app database user. For anything long-lived, create a read-only user:

```sql
CREATE USER mcp_reader WITH PASSWORD '<strong-password>';
GRANT CONNECT ON DATABASE events TO mcp_reader;
GRANT USAGE ON SCHEMA public TO mcp_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_reader;
```

The MCP server is designed to query data and expose context to your coding agent; it does not need write access.

### Available tools

| Tool | Description |
|------|-------------|
| `get_system_health_summary` | Open anomalies, active trends, recent errors — the current state of your system |
| `get_recent_anomalies` | Anomalies detected in the last N hours, with severity and deviation |
| `get_anomaly_detail` | Full detail on a specific anomaly including its AI-generated insight |
| `get_recent_errors` | Recent errors with occurrence counts and affected services |
| `get_unresolved_errors` | All open (unresolved) errors |
| `get_recent_insights` | Latest AI-generated insights on anomalies and trends |
| `get_metric_summary` | Recent values for a specific metric |
| `search_metric_names` | Find metric names by prefix or substring |
| `run_query` | Natural language → SQL: ask any question about your event data in plain English |

Once configured, your coding agent automatically has production context. Ask it things like *"are there any relevant errors in production that might explain this bug?"* and it will check before answering.

---

## How it works

### Pipeline

Runs automatically every 15 minutes (configurable). Also triggerable on demand via the Scan button in the UI or `POST /admin/run-pipeline`.

1. **Compute baselines** — seasonality-aware historical averages and standard deviations, bucketed by day-of-week × hour-of-day over the last 28 days
2. **Compute metrics** — event counts and numeric property aggregates (avg, p95) for the current window
3. **Detect anomalies** — flags deviations beyond a configurable threshold (default: 3σ); suppresses repeats within a cooldown window
4. **Detect trends** — linear regression over the last 6 hours; flags sustained directional movement ≥10%/hr with goodness-of-fit filtering
5. **Generate insights** — the configured LLM writes a title, summary, and explanation for each anomaly and trend, incorporating any stored knowledge about the event type
6. **Send notifications** — posts to Slack and/or SMS if configured (optional)

### Conversational agent

The chat UI and Slack/SMS conversations share the same agent. It has four capabilities:

- **ANALYST** — runs read-only SQL to answer any question about events, metrics, anomalies, or errors
- **LEARNER** — when you explain what an event means, it persists that knowledge to the event type catalog; future insights become more specific
- **TRACKER** — when you say *"track checkout amounts"*, it configures `avg` and `p95` property metrics; it can scan recent events to discover available numeric properties first
- **RESOLVER** — marks anomalies as acknowledged (investigating) or resolved (fixed / false positive); resolved anomalies suppress further alerts until a new one is detected

### Event property metrics

Track numeric values from event properties. Tracking `checkout.amount` generates:

- `property.checkout.amount.avg` — average per pipeline window
- `property.checkout.amount.p95` — 95th percentile per pipeline window

Configure via the chat: *"track the amount and items_count properties on checkout"*

---

## Optional: Slack and SMS/WhatsApp alerts

Notifications are optional — the chat UI works without them. Configure them when you want alerts pushed to you rather than having to open the browser.

### Slack

1. Create an app at [api.slack.com/apps](https://api.slack.com/apps) with bot scopes: `chat:write`, `channels:history`, `groups:history`
2. Add to `.env`:
   ```
   SLACK_BOT_TOKEN=xoxb-...
   SLACK_SIGNING_SECRET=...
   NGROK_AUTHTOKEN=...
   NGROK_DOMAIN=your-domain.ngrok-free.app
   ```
3. Set the Event Subscriptions request URL to `https://<your-ngrok-domain>/slack/events`
4. Subscribe to bot events: `message.channels`, `message.groups`
5. Set a Slack channel for your tenant in the Settings tab

### SMS / WhatsApp (Twilio)

1. Add to `.env`:
   ```
   TWILIO_ACCOUNT_SID=AC...
   TWILIO_AUTH_TOKEN=...
   TWILIO_FROM_NUMBER=whatsapp:+14155238886
   ```
2. In the Twilio console, set the WhatsApp sandbox webhook to `https://<your-ngrok-domain>/sms/events`
3. Add recipient numbers in the Settings tab

---

## Web UI

Available at `http://localhost:8000/ui/` — protected by HTTP Basic Auth if `API_KEY` is set (use any username, API key as password).

| Tab | Description |
|-----|-------------|
| **Chat** | Conversational interface — start here |
| **Dashboard** | Open anomalies, active trends, recent insights |
| **Settings** | Event type catalog, Slack channel, SMS recipients, and tenant Kafka settings |

---

## Security notes

This project is configured for local development by default.

- Set `API_KEY` before exposing the app outside your machine. When `API_KEY` is blank, auth is disabled.
- Keep `.env`, `.env.old`, `.mcp.json`, and local assistant/editor config out of git. The included `.gitignore` excludes them.
- Keep `KAFKA_CREDENTIAL_ENCRYPTION_KEY` private and stable. If it is lost, saved tenant Kafka passwords cannot be decrypted. If it leaks, rotate the key and re-save tenant Kafka passwords.
- Docker Compose binds local ports to `127.0.0.1` by default. If you change those bindings for a hosted environment, put the app behind TLS and authentication.
- Do not reuse the development Postgres credentials outside local Docker.

---

## Development

```bash
# Start just the database
docker compose up -d event-db

# Python environment
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# Run the app
venv/bin/uvicorn app.main:app --reload

# Run the Kafka consumer separately
python -m app.consumer

# Integration tests use an isolated test database on localhost:5434
docker compose -f docker-compose.test.yml up -d
venv/bin/pytest -m integration

# Unit tests do not require a database
venv/bin/pytest -m unit   # unit tests only (no database)
venv/bin/pytest           # all tests

# Lint + types
venv/bin/ruff check .
venv/bin/mypy .
```

### Applying DB migrations manually

Docker applies `event-db-setup-scripts/01_schema.sql` when the database volume is first created, and the app applies a small set of idempotent runtime schema upgrades at startup. To apply the schema manually:

```bash
psql "postgresql://user:pass@127.0.0.1:5433/events" -f event-db-setup-scripts/01_schema.sql
```

---

## Configuration reference

Key settings (all overridable via environment variables):

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Preferred LLM provider key for insights and chat |
| `OPENAI_API_KEY` | — | Fallback LLM provider key if Anthropic is not configured |
| `API_KEY` | — | Enables auth for the app; leave unset to disable auth in dev |
| `KAFKA_BOOTSTRAP_SERVERS` | — | Optional global broker fallback; tenant Settings usually override this |
| `KAFKA_CONSUMER_GROUP_PREFIX` | `ai-events` | Prefix for tenant-specific Kafka consumer groups |
| `KAFKA_TOPIC_REFRESH_INTERVAL_SECONDS` | `60` | How often the Kafka consumer reloads tenant broker settings |
| `KAFKA_CREDENTIAL_ENCRYPTION_KEY` | — | Required to save/decrypt tenant Kafka passwords |
| `PIPELINE_SCHEDULE_MINUTES` | `15` | How often the pipeline runs |
| `METRIC_WINDOW_MINUTES` | `60` | Window size for metric computation |
| `BASELINE_LOOKBACK_DAYS` | `28` | How far back baselines are computed |
| `ANOMALY_THRESHOLD_STDDEV` | `3.0` | Standard deviations to flag an anomaly |
| `ANOMALY_COOLDOWN_HOURS` | `24` | Suppresses repeat alerts for the same metric |
| `TREND_WINDOW_HOURS` | `6` | Hours of history used for trend regression |
| `TREND_CHANGE_THRESHOLD_PCT` | `10.0` | Minimum % change per hour to flag a trend |
| `ANTHROPIC_MODEL` | `claude-opus-4-6` | Claude model for insights and conversation |
| `OPENAI_MODEL` | `gpt-4o` | OpenAI model used when Anthropic is not configured |
| `MCP_DATABASE_URL` | — | Read-only DB URL for the MCP server |
| `MCP_DEFAULT_TENANT_ID` | — | Tenant scoped to by default in MCP tools |
| `INCLUDE_DEMO_TENANT` | `true` | Seed demo data on first start |
| `PUBLISH_DEMO_KAFKA_EVENTS` | `false` | Opt-in only; publishes sample seed messages to the global Kafka broker |

---

## Data model

| Table | Purpose |
|-------|---------|
| `tenants` | Multi-tenant isolation |
| `events` | Raw ingested events with arbitrary `properties` JSONB |
| `event_types` | Catalog of known event types; stores descriptions and tracked properties learned from conversation |
| `errors` | Error events with fingerprint-based deduplication and occurrence tracking |
| `metrics` | Computed metric values per window |
| `metric_baselines` | Historical averages and stddev bucketed by day-of-week × hour-of-day |
| `anomalies` | Point-in-time deviations from baseline with lifecycle tracking |
| `trends` | Sustained directional movements with regression statistics |
| `insights` | LLM-generated explanations linked to anomalies or trends |
| `notifications` | Record of delivered Slack and SMS messages |
| `conversations` | Active conversations (web, Slack, SMS) linked to an insight or tenant |
| `messages` | Individual turns within a conversation |
| `tenant_kafka_settings` | Per-tenant Kafka broker, auth, topic routing, and ingestion status |

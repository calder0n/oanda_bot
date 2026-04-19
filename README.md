# OANDA Algo Bot — Kevin Davey Strategy

Production-ready scaffold for running a **Kevin Davey-style volatility-filtered
breakout strategy** against **OANDA** across all demo-tradeable instruments,
with native multi-account support and a Docker-first deployment.

> ⚠️ **Demo only until incubated.** Per Davey's own methodology, a strategy
> must pass out-of-sample testing, Monte Carlo stress tests, and a multi-month
> incubation period on a **practice** account before any live capital is risked.
> This repo defaults to `environment: practice`. Do not flip to `live` until
> you have done that work.

---

## Strategy summary

Family: **volatility-filtered channel breakout** with ATR-based stops, an
R-multiple target, and a hard time-based exit — one of the archetypes Davey
describes in *Building Winning Algorithmic Trading Systems*.

| Rule       | Default                                                   |
|------------|-----------------------------------------------------------|
| Entry long | close > Donchian high of last `lookback_bars` (default 40)|
| Entry short| close < Donchian low of last `lookback_bars`              |
| Vol filter | ATR / price ≥ `volatility_filter_atr_pct`                 |
| Stop       | `atr_stop_mult × ATR` from entry (default 2.0)            |
| Target     | `atr_target_mult × ATR` from entry (default 4.0 → 2R)     |
| Time exit  | flatten after `time_exit_bars` bars (default 48)          |
| Sizing     | `risk_per_trade_pct` of NAV, from entry→stop distance     |
| Kill-switch| daily realized DD ≥ `max_daily_loss_pct` stops new trades |
| Hours      | restricted to `trading_window_utc.start..end`             |

All parameters are per-account and live in `config/accounts.yaml`.

### Tuning for a $1,000 starting balance

The defaults in `config/accounts.example.yaml` are set for **$1,000 USD**:

| Param                  | Default | On $1,000        |
|------------------------|---------|------------------|
| `risk_per_trade_pct`   | 1.0     | ~$10 per trade   |
| `max_open_positions`   | 3       | ~$30 total risk  |
| `max_daily_loss_pct`   | 3.0     | ~$30 daily stop  |

**Expect many instruments to be skipped.** With only ~$10 to risk, the
position sizer (`risk_amount / stop_distance`) will fall below the instrument's
minimum trade size for high-priced assets (XAU_USD, indices, BTC CFDs).
Those skips are logged as `skipped_min_size` — you'll see FX majors and
smaller-priced pairs trade regularly and high-priced assets stay flat.

If you want more coverage, you can (in order of risk increase):
1. Use a finer `granularity` (e.g. `M5`) so ATR — and thus the stop distance —
   is smaller.
2. Lower `atr_stop_mult` (e.g. 1.5).
3. Whitelist only FX majors via the `instruments:` list.
4. Raise `risk_per_trade_pct` cautiously (never beyond 2% on a $1k account).

---

## Project layout

```
oanda_bot/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── config/
│   └── accounts.example.yaml      # copy to accounts.yaml
├── src/
│   ├── main.py                    # entrypoint
│   ├── config.py                  # YAML + pydantic loader
│   ├── oanda_client.py            # async OANDA wrapper (retries)
│   ├── account_worker.py          # one isolated worker per account
│   ├── strategy/
│   │   ├── base.py
│   │   ├── kevin_davey.py         # the strategy
│   │   └── registry.py            # name -> class
│   ├── risk/manager.py            # sizing + daily-loss kill switch
│   ├── indicators/technical.py    # ATR, Donchian, EMA
│   ├── notifications/telegram.py  # Telegram notifier (order_filled, etc.)
│   └── utils/logger.py            # structured JSON logs
└── tests/
    └── test_strategy.py
```

Each account in the YAML spawns its own `asyncio` worker with its own token,
strategy instance, and risk state — nothing is shared between accounts.
Adding another account = adding another YAML block.

---

## 1. Prerequisites

- An OANDA **practice (fxTrade Practice)** account: <https://www.oanda.com/demo-account/>
- A **v20 API token** (generate inside the practice portal → *Manage API Access*)
- Your practice **account ID** (e.g. `101-001-0000000-001`)
- **Docker** + **Docker Compose v2** installed

Optional for local (non-Docker) runs: Python 3.12+.

---

## 2. Installation — Docker (recommended)

```bash
git clone <this-repo> oanda_bot
cd oanda_bot
git checkout algo_trading

# 1. Copy the example configs
cp .env.example .env
cp config/accounts.example.yaml config/accounts.yaml

# 2. Fill in config/accounts.yaml with your OANDA practice token + account ID
#    (see "Multi-account" below to add more accounts)
$EDITOR config/accounts.yaml

# 3. Build and run
docker compose build
docker compose up -d

# 4. Watch logs (JSON, one line per event)
docker compose logs -f oanda-bot
```

Stop and clean up:

```bash
docker compose down
```

### What the container does on startup

1. Loads `config/accounts.yaml` (mounted read-only).
2. Fetches the full list of practice-tradeable instruments per account via
   OANDA's `AccountInstruments` endpoint — so **every asset you can trade on
   the demo account** is covered automatically (FX majors/minors/exotics,
   metals, indices, bonds, commodities, crypto CFDs where available in your
   region).
3. Spawns one async worker per account; each worker polls candles at
   `tick_interval_seconds` and evaluates the strategy per instrument.
4. Orders are submitted with server-side SL/TP attached so protection exists
   even if the bot crashes.

---

## 3. Installation — local (for development / backtesting harness)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/accounts.example.yaml config/accounts.yaml
$EDITOR config/accounts.yaml

CONFIG_PATH=config/accounts.yaml python -m src.main
```

Run tests:

```bash
pip install pytest
pytest -q
```

---

## 4. Multi-account configuration

`config/accounts.yaml` uses a two-section format: a shared `defaults:` block
and an `accounts:` list. Any key under an account overrides the default.

```yaml
defaults:
  environment: practice
  granularity: M15
  instruments: "ALL_TRADEABLE"
  strategy:
    name: kevin_davey_breakout
    params:
      lookback_bars: 40
      atr_stop_mult: 2.0
      atr_target_mult: 4.0
      risk_per_trade_pct: 0.5
      max_daily_loss_pct: 2.0

accounts:
  - name: demo-primary
    account_id: "101-001-0000000-001"
    access_token: "DEMO_TOKEN_A"

  - name: demo-secondary
    account_id: "101-001-0000000-002"
    access_token: "DEMO_TOKEN_B"
    granularity: H1                # override
    strategy:
      params:
        lookback_bars: 20          # override
        risk_per_trade_pct: 0.25   # override

  - name: demo-fx-only
    account_id: "101-001-0000000-003"
    access_token: "DEMO_TOKEN_C"
    instruments:                    # explicit list instead of ALL_TRADEABLE
      - EUR_USD
      - USD_JPY
      - GBP_USD
```

Adding a fourth account is just another list item — the runner scales
horizontally inside the same container (one asyncio task per account), and the
process is fully restart-safe.

### Running different strategies per account

Register a new strategy class in `src/strategy/registry.py` and reference it
by `strategy.name` in the YAML. The `Strategy` base class in
`src/strategy/base.py` is two methods (`required_bars`, `evaluate`).

---

## 5. Telegram notifications

The bot posts a Telegram message every time a new trade is opened
(`order_filled` event). Setup:

1. **Create a bot**: talk to [@BotFather](https://t.me/BotFather) in Telegram,
   run `/newbot`, answer the prompts, and save the token it returns (looks
   like `123456789:AAH...`).
2. **Get your chat ID**: start a chat with your new bot (send `/start`), then
   open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser. The
   `chat.id` field in the JSON response is what you want. For a group, add
   the bot to the group and read `chat.id` from the same endpoint (group IDs
   are negative).
3. **Fill `config/accounts.yaml`** under the account (or under `defaults:`):

   ```yaml
   notifications:
     telegram:
       enabled: true
       bot_token: "123456789:AAH..."
       chat_id:   "987654321"
       notify_on:
         - order_filled       # every new trade
         # - startup          # also message when a worker starts
   ```

4. Restart: `docker compose restart`. You should see a message like:

   ```
   🟢 New LONG on EUR_USD
   • Account: demo-primary
   • Units: 1950
   • Entry: 1.08425
   • Stop: 1.07912
   • Target: 1.09452
   • Reason: breakout_high
   • Equity: 1002.37 (risk ≈ 10.02)
   ```

Different accounts can notify different chats — override the `notifications`
block inside a specific account entry.

---

## 6. Operations

**Logs** are JSON, one event per line. Each tick you'll see:

| Event            | Meaning                                                    |
|------------------|------------------------------------------------------------|
| `tick`           | Once per poll: equity, open positions, kill-switch state.  |
| `evaluation`     | Per instrument: price, ATR, ATR%, donchian_high/low, signal, reason. |
| `skipped_min_size` | Strategy fired but units < instrument minimum (common on $1k). |
| `order_filled`   | A market order was accepted by OANDA.                      |
| `time_exit`      | A trade was flattened by the time-exit rule.               |

Examples:

```bash
# Watch new orders only
docker compose logs --no-log-prefix oanda-bot | jq 'select(.event=="order_filled")'

# See the numbers the bot is evaluating for EUR_USD
docker compose logs --no-log-prefix oanda-bot \
  | jq 'select(.event=="evaluation" and .instrument=="EUR_USD")'

# See everything that got blocked by volatility filter
docker compose logs --no-log-prefix oanda-bot \
  | jq 'select(.reason=="low_volatility") | {instrument, atr_pct, vol_filter}'
```

To silence per-instrument evaluation lines, set `log_evaluations: false` on
the account (or defaults).

**Graceful shutdown**: `docker compose stop` sends SIGTERM; the bot finishes
its current tick (≤ `tick_interval_seconds`) and exits. Attached SL/TP keeps
open positions protected on OANDA's side.

**Update config without rebuilding**: the config volume is mounted live.
Edit `config/accounts.yaml` and `docker compose restart`.

**Resource limits**: the Compose file caps the bot at 1 CPU / 512 MB. Adjust
in `docker-compose.yml` if you run a lot of accounts.

---

## 7. Davey-style deployment checklist

Do **not** skip steps. From *Building Winning Algorithmic Trading Systems*:

- [ ] In-sample backtest the strategy on historical OANDA data (≥ 5 years).
- [ ] Out-of-sample test on held-out data (≥ 30 % of history).
- [ ] Monte Carlo: randomize trade order 1000× — require acceptable DD
      and profit percentiles.
- [ ] Walk-forward optimize the params you care about.
- [ ] Incubate on this repo's practice config for ≥ 2–3 months.
- [ ] Only then consider `environment: live` — and start with `risk_per_trade_pct`
      at a fraction of the incubation setting.

A backtest harness is deliberately out of scope for this initial scaffold —
plug `src/strategy/kevin_davey.py` into your preferred engine (vectorbt,
backtrader, bt). The indicator code in `src/indicators/technical.py` is pure
pandas and drop-in compatible.

---

## 8. Security notes

- `config/accounts.yaml` is in `.gitignore`. Never commit your tokens.
- Use a different token per account; scope each to the specific account ID.
- The Dockerfile runs as a non-root user.
- Rotate tokens regularly via the OANDA portal.

---

## 9. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `config_load_failed` on start | `config/accounts.yaml` missing or malformed. |
| `V20Error` 401 | Wrong `access_token` or wrong `environment` (live vs practice). |
| No trades ever | `trading_window_utc` too narrow, `volatility_filter_atr_pct` too high, or `lookback_bars` too long relative to `count`. |
| `order_failed` with units=0 | Stop distance is zero or below instrument minimum trade size. |
| `daily_loss_limit_hit` | Intended kill-switch; resets automatically at 00:00 UTC. |
| `telegram_send_failed` | Wrong bot token, wrong chat ID, or bot blocked in the chat. |
| Many `skipped_min_size` events | Normal on a $1k account for high-priced assets; see "Tuning for a $1,000 starting balance" above. |

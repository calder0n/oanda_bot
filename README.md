# OANDA Bot — Fibonacci Day Trading

Production-ready scaffold for running a **Fibonacci pullback day-trading
strategy** against **OANDA** across all demo-tradeable instruments, with
native multi-account support and a Docker-first deployment.

> ⚠️ **Demo only until incubated.** This repo defaults to
> `environment: practice`. Do **not** flip to `live` until the strategy has
> been backtested, walk-forward tested, and incubated for at least 2-3 months
> on a practice account.

---

## Strategy summary

Family: **trend-following pullback into the Fibonacci "golden zone"** with an
ATR-derived volatility filter, an invalidation stop just past the 78.6 %
retracement, and a 1.272 Fibonacci extension target — plus a hard intraday
time exit so positions don't carry overnight.

| Rule        | Default                                                          |
|-------------|------------------------------------------------------------------|
| Trend bias  | EMA(`trend_ema_period`) — long only above, short only below      |
| Swing       | most recent low → high (long) / high → low (short) over `swing_lookback` |
| Entry       | price inside [`entry_zone_low`, `entry_zone_high`] retracement   |
| Confirm     | last closed bar bullish (long) or bearish (short)                |
| Vol filter  | ATR / price ≥ `min_atr_pct`                                      |
| Stop        | just past `invalidation_fib` (default 78.6 %) of the swing       |
| Target      | `target_extension` Fibonacci extension (default 1.272)           |
| Time exit   | flatten after `time_exit_bars` bars (~2h on M5)                  |
| Hours       | restricted to `trading_window_utc.start..end`                    |
| Sizing      | `risk_per_trade_pct` of NAV, distance entry → stop               |
| Kill-switch | daily realized DD ≥ `max_daily_loss_pct` blocks new trades       |

All parameters are per-account and live in `config/accounts.yaml`.

### Tuning for a $1,000 starting balance

The defaults in `config/accounts.example.yaml` are set for **$1,000 USD**:

| Param                  | Default | On $1,000        |
|------------------------|---------|------------------|
| `risk_per_trade_pct`   | 1.0     | ~$10 per trade   |
| `max_open_positions`   | 3       | ~$30 total risk  |
| `max_daily_loss_pct`   | 3.0     | ~$30 daily stop  |
| `granularity`          | M5      | day-trading bars |
| `time_exit_bars`       | 24      | ~2h max hold     |

**Expect many instruments to be skipped on a $1k account.** With only ~$10 to
risk, the position sizer (`risk_amount / stop_distance`) often falls below the
broker's minimum trade size for high-priced assets (XAU_USD, indices,
BTC CFDs). Those skips are logged as `skipped_min_size` — FX majors and
lower-priced pairs will trade regularly while the heavy-priced assets sit out.

If you want more coverage, you can (in order of risk increase):

1. Tighten the `swing_lookback` so the swing range — and stop distance —
   shrinks. (e.g. 20)
2. Pull `invalidation_fib` in toward the entry (e.g. 0.65). **More signals
   wash out the extra cost in stops, so backtest first.**
3. Whitelist FX majors only via `instruments:`.
4. Cautiously raise `risk_per_trade_pct` (never beyond 2 % on a $1k account).

---

## Project layout

```
oanda_bot/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── config/
│   └── accounts.example.yaml          # copy to accounts.yaml
├── src/
│   ├── main.py                        # entrypoint
│   ├── config.py                      # YAML + pydantic loader
│   ├── oanda_client.py                # async OANDA REST wrapper (retries)
│   ├── account_worker.py              # one isolated worker per account
│   ├── strategy/
│   │   ├── base.py
│   │   ├── fibonacci_day_trading.py   # the strategy on this branch
│   │   ├── kevin_davey.py             # alt strategy (carryover)
│   │   └── registry.py                # name -> class
│   ├── risk/manager.py                # sizing + daily-loss kill switch
│   ├── indicators/technical.py        # ATR, EMA, Fib retracements/extensions
│   ├── notifications/telegram.py      # Telegram notifier (order_filled, etc.)
│   └── utils/logger.py                # structured JSON logs
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
- (Optional) a Telegram bot token + chat ID for trade notifications — see §5

Optional for local (non-Docker) runs: Python 3.12+.

---

## 2. Installation — Docker (recommended)

```bash
git clone <this-repo> oanda_bot
cd oanda_bot
git checkout day_trading

# 1. Copy the example configs
cp .env.example .env
cp config/accounts.example.yaml config/accounts.yaml

# 2. Fill in config/accounts.yaml with your OANDA practice token + account ID
#    (and your Telegram bot_token + chat_id if you want notifications).
#    See "Multi-account" below to add more accounts.
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
2. For each account, fetches the full list of practice-tradeable instruments
   via OANDA's `AccountInstruments` endpoint — so **every asset you can trade
   on the demo account** is covered automatically (FX majors/minors/exotics,
   metals, indices, bonds, commodities, crypto CFDs where available).
3. Spawns one async worker per account; each polls candles every
   `tick_interval_seconds` and evaluates the Fibonacci setup per instrument.
4. Orders are submitted with server-side SL/TP attached so protection exists
   even if the bot crashes.
5. Every newly opened trade triggers a Telegram message (if enabled).

---

## 3. Installation — local (for development / experimentation)

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
and an `accounts:` list. Any key under an account overrides the default
deeply.

```yaml
defaults:
  environment: practice
  granularity: M5
  instruments: "ALL_TRADEABLE"
  strategy:
    name: fibonacci_day_trading
    params:
      swing_lookback: 30
      trend_ema_period: 50
      entry_zone_low: 0.382
      entry_zone_high: 0.618
      invalidation_fib: 0.786
      target_extension: 1.272
      risk_per_trade_pct: 1.0
      max_daily_loss_pct: 3.0

accounts:
  - name: demo-primary
    account_id: "101-001-0000000-001"
    access_token: "DEMO_TOKEN_A"

  - name: demo-secondary
    account_id: "101-001-0000000-002"
    access_token: "DEMO_TOKEN_B"
    granularity: M15                 # override
    strategy:
      params:
        swing_lookback: 40           # override
        risk_per_trade_pct: 0.5      # override

  - name: demo-fx-only
    account_id: "101-001-0000000-003"
    access_token: "DEMO_TOKEN_C"
    instruments:                     # explicit list instead of ALL_TRADEABLE
      - EUR_USD
      - USD_JPY
      - GBP_USD
```

Adding a fourth account is just another list item — the runner scales
horizontally inside the same container (one asyncio task per account), and
the process is fully restart-safe.

### Running a different strategy per account

The repo also ships the original `kevin_davey_breakout` strategy. Set
`strategy.name` per account to switch:

```yaml
- name: demo-breakout
  account_id: "101-001-0000000-004"
  access_token: "DEMO_TOKEN_D"
  strategy:
    name: kevin_davey_breakout
    params:
      lookback_bars: 40
      atr_stop_mult: 2.0
```

To add your own, drop a class into `src/strategy/<your_strategy>.py`,
register it in `src/strategy/registry.py`, and reference it by name in YAML.
The `Strategy` base class is two methods: `required_bars`, `evaluate`.

---

## 5. Telegram notifications

The bot posts a Telegram message every time a new trade is opened
(`order_filled` event). Setup:

1. **Create a bot**: talk to [@BotFather](https://t.me/BotFather) in Telegram,
   run `/newbot`, answer the prompts, and save the token it returns (looks
   like `123456789:AAH...`).
2. **Get your chat ID**:
   - For a personal chat: send `/start` to your new bot, then open
     `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser. The
     `chat.id` field in the JSON response is what you want.
   - For a channel: add the bot as an *administrator* of the channel, post
     anything to the channel, then read `channel_post.chat.id` from the same
     `getUpdates` endpoint. Channel IDs are negative and start with `-100`.
3. **Fill `config/accounts.yaml`** under the account (or under `defaults:`):

   ```yaml
   notifications:
     telegram:
       enabled: true
       bot_token: "123456789:AAH..."
       chat_id:   "-1001234567890"   # channel ID, or personal chat ID
       notify_on:
         - order_filled              # every new trade
         # - startup                 # also message when a worker starts
   ```

4. Restart: `docker compose restart`. Each new trade should appear like:

   ```
   🟢 New LONG on EUR_USD
   • Account: demo-primary
   • Units: 1950
   • Entry: 1.08425
   • Stop: 1.07912
   • Target: 1.09452
   • Reason: fib_pullback_long
   • Equity: 1002.37 (risk ≈ 10.02)
   ```

Different accounts can notify different chats/channels — override the
`notifications` block inside a specific account entry.

---

## 6. Operations & logs

**Logs** are JSON, one event per line. Each tick you'll see:

| Event              | Meaning                                                    |
|--------------------|------------------------------------------------------------|
| `tick`             | Once per poll: equity, open positions, kill-switch state.  |
| `evaluation`       | Per instrument: every Fibonacci number being evaluated     |
|                    | (swing_low/high, fib_236/382/500/618/786, ext_1000/1272/1618, |
|                    | EMA, ATR, atr_pct, in_zone flag, signal, reason).          |
| `skipped_min_size` | Strategy fired but units < instrument minimum (common on $1k). |
| `order_filled`     | Market order accepted by OANDA — Telegram notification fires. |
| `time_exit`        | Trade flattened by the day-trading time-exit rule.         |

Examples (need `jq`):

```bash
# Watch new orders only
docker compose logs --no-log-prefix oanda-bot | jq 'select(.event=="order_filled")'

# See the Fibonacci numbers the bot is evaluating for EUR_USD
docker compose logs --no-log-prefix oanda-bot \
  | jq 'select(.event=="evaluation" and .instrument=="EUR_USD")'

# Just the price and the golden zone for every evaluation
docker compose logs --no-log-prefix oanda-bot \
  | jq 'select(.event=="evaluation") | {instrument, price, fib_382, fib_500, fib_618, in_zone, signal, reason}'
```

To silence per-instrument evaluation lines, set `log_evaluations: false` on
the account (or under `defaults:`).

**Graceful shutdown**: `docker compose stop` sends SIGTERM; the bot finishes
its current tick (≤ `tick_interval_seconds`) and exits. Attached SL/TP keeps
open positions protected on OANDA's side.

**Update config without rebuilding**: the config volume is mounted live.
Edit `config/accounts.yaml` and `docker compose restart`.

**Resource limits**: the Compose file caps the bot at 1 CPU / 512 MB. Adjust
in `docker-compose.yml` if you run a lot of accounts.

---

## 7. Day-trading deployment checklist

Do **not** skip steps:

- [ ] In-sample backtest the strategy on historical OANDA M5 data (≥ 2 years).
- [ ] Out-of-sample test on held-out data (≥ 30 % of history).
- [ ] Monte Carlo: randomize trade order 1000× — check drawdown percentiles.
- [ ] Walk-forward optimize `swing_lookback`, `entry_zone_*`, and
      `target_extension`.
- [ ] Incubate on this repo's practice config for ≥ 2-3 months.
- [ ] Only then consider `environment: live` — and start with
      `risk_per_trade_pct` at a fraction of the incubation setting.

A backtest harness is deliberately out of scope for this initial scaffold —
plug `src/strategy/fibonacci_day_trading.py` into your preferred engine
(vectorbt, backtrader, bt). The indicator code in
`src/indicators/technical.py` is pure pandas / numpy and drop-in compatible.

---

## 8. Security notes

- `config/accounts.yaml` is in `.gitignore`. Never commit your tokens.
- Use a different OANDA token per account; scope each to the specific
  account ID.
- The Dockerfile runs as a non-root user.
- Rotate tokens regularly via the OANDA portal.
- Telegram bot tokens are credentials too — keep them in
  `config/accounts.yaml` (gitignored), not in source files.

---

## 9. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `config_load_failed` on start | `config/accounts.yaml` missing or malformed. |
| `V20Error` 401 | Wrong `access_token` or wrong `environment` (live vs practice). |
| No trades ever | `trading_window_utc` too narrow, `min_atr_pct` too high, EMA still warming up, or all setups skipped because price is outside the golden zone. Check `evaluation` log lines — every reason is logged. |
| All evaluations say `not_in_fib_zone` | Normal — the bot only enters when price retraces *into* the 38.2-61.8% band of a fresh swing. Most ticks just watch. |
| `awaiting_bull_confirmation` repeats | Price is inside the zone but the most recent closed bar isn't bullish yet. The bot is waiting for a confirmation candle. |
| `order_failed` with units=0 | Stop distance is zero or below instrument minimum trade size. |
| Many `skipped_min_size` events | Normal on a $1k account for high-priced assets; see "Tuning for a $1,000 starting balance" above. |
| `daily_loss_limit_hit` | Intended kill-switch; resets automatically at 00:00 UTC. |
| `telegram_send_failed` | Wrong bot token, wrong chat ID, or the bot isn't an administrator of the target channel. |

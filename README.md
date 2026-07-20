# OANDA Bot — Sergey Magala FOTSI Strategy

Bot de trading en Python que ejecuta la **estrategia de Sergey Magala usando el
indicador FOTSI** contra **OANDA (cuenta demo)** sobre **todos los instrumentos
disponibles** en temporalidad **M1 (1 minuto)**. Diseñado para correr en Docker,
soporte nativo multi-cuenta, capital inicial de referencia **$1,000 USD**,
notificaciones Telegram por cada entrada nueva y un registro SQLite compartido
para que un bot compañero pueda auditar y cerrar posiciones.

> ⚠️ **Solo cuenta demo.** El repo defaultea a `environment: practice`. No
> muevas a `live` hasta haber validado la estrategia con incubación real.

---

## 1. Qué es el indicador FOTSI

FOTSI (*Fast Oscillator Trend Strength Index*) es un indicador compuesto
popularizado por Sergey Magala. Combina tres lecturas independientes del
mercado en un único score `[-100, +100]`:

| Componente | Fuente                                | Contribuye |
|------------|---------------------------------------|-----------|
| `trend`    | posición del precio vs EMA(8/21/55)   | dirección |
| `momentum` | Stochastic %K centrado + su derivada  | timing    |
| `strength` | ADX con signo (+DI vs −DI)            | fuerza    |

```
FOTSI = w_trend · trend + w_mom · momentum + w_str · strength   (normalizado)
```

La fórmula exacta de Magala no es pública; esta implementación construye el
mismo tipo de compuesto a partir de las primitivas que él enfatiza. Los pesos
y periodos son parámetros en `config/accounts.yaml` para poder ajustarlos.

## 2. Reglas de la estrategia (`magala_fotsi`)

**Entry LONG** cuando se cumplen todas:

* `FOTSI ≥ entry_threshold` (default 55)
* `trend > 0` y `strength ≥ strength_min`
* `price > EMA_fast`  (sesgo alcista confirmado)
* `momentum` estuvo negativo en las últimas `pullback_lookback` velas
  (compramos el pullback, no el rebote agotado)

**Entry SHORT**: espejo simétrico.

**Salidas** (adjuntas como SL/TP al orden de OANDA):

* `SL  = entry ∓ atr_stop_mult × ATR`   (default 1.5)
* `TP  = entry ± atr_target_mult × ATR` (default 3.0 → R:R = 2:1)

**Filtros**:

* `min_atr_pct`: ignora instrumentos con volatilidad relativa demasiado baja
* `trading_window_utc`: solo opera en la ventana horaria configurada (UTC)
* `max_daily_loss_pct`: kill-switch por drawdown diario realizado
* `max_open_positions`: cap de exposición concurrente

### Dimensionamiento para $1,000 USD

| Parámetro              | Default | En $1,000 USD    |
|------------------------|---------|------------------|
| `risk_per_trade_pct`   | 1.0     | ~$10 por trade   |
| `max_open_positions`   | 3       | ~$30 riesgo total|
| `max_daily_loss_pct`   | 3.0     | ~$30 stop diario |

Muchos instrumentos serán descartados automáticamente: con solo $10 de riesgo
por trade, el sizer (`risk_amount / stop_distance`) queda por debajo del
`minimumTradeSize` en activos caros (índices, oro, BTC CFDs). Esos skips
aparecen en los logs como `skipped_min_size`.

## 3. Estructura del proyecto

```
oanda_bot/
├── config/
│   └── accounts.example.yaml     # multi-cuenta, defaults heredables
├── src/
│   ├── main.py                   # entrypoint, arranca 1 worker por cuenta
│   ├── config.py                 # esquema Pydantic
│   ├── account_worker.py         # ciclo tick por cuenta
│   ├── oanda_client.py           # wrapper OANDA v20 con retry
│   ├── indicators/
│   │   ├── fotsi.py              # FOTSI compuesto
│   │   └── technical.py          # ATR, EMA, Donchian, TR
│   ├── strategy/
│   │   ├── base.py               # interfaz Strategy / Signal
│   │   ├── magala_fotsi.py       # estrategia principal (default)
│   │   ├── kevin_davey.py        # estrategia previa (opt-in)
│   │   └── registry.py           # registry name -> class
│   ├── risk/
│   │   └── manager.py            # sizing + kill switch
│   ├── notifications/
│   │   └── telegram.py           # notifier async, ID/TP/SL en mensaje
│   ├── persistence/
│   │   └── trade_registry.py     # SQLite compartido (id/entry/TP/SL/exit)
│   └── utils/logger.py           # structlog JSON a stdout
├── tests/
│   ├── test_strategy.py
│   ├── test_magala_fotsi.py
│   └── test_trade_registry.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

### Multi-cuenta

Cada bloque bajo `accounts:` genera un `AccountWorker` independiente (asyncio
task). Herencia con `defaults:` para no duplicar. Puedes correr distintas
estrategias por cuenta cambiando `strategy.name` (`magala_fotsi` o
`kevin_davey_breakout`).

```yaml
accounts:
  - name: demo-primary
    account_id: "101-001-0000000-001"
    access_token: "..."
  - name: demo-secondary
    account_id: "101-001-0000000-002"
    access_token: "..."
    strategy:
      params:
        entry_threshold: 65.0
        atr_stop_mult: 1.2
```

## 4. Instalación

### 4.1 Prerrequisitos

* Cuenta demo de OANDA + `Access Token` (Manage API Access en fxTrade Practice)
* Docker 24+ y docker compose plugin
* (Opcional) Bot de Telegram con su `bot_token` y el `chat_id` del canal

### 4.2 Bot de Telegram

1. Habla con [@BotFather](https://t.me/BotFather) en Telegram → `/newbot`
2. Guarda el `HTTP API token`
3. Añade el bot a tu canal como admin
4. Manda un mensaje al canal, luego consulta:
   `https://api.telegram.org/bot<TOKEN>/getUpdates` para leer el `chat.id`

### 4.3 Clonar y configurar

```bash
git clone <URL_DE_ESTE_REPO> oanda_bot
cd oanda_bot
git checkout fotsi_trading

cp .env.example .env
cp config/accounts.example.yaml config/accounts.yaml
```

Edita `config/accounts.yaml`:

* Rellena `access_token` y `account_id` (cuenta demo).
* Rellena `notifications.telegram.bot_token` y `chat_id` (o deja
  `enabled: false` para desactivar).
* Cambia parámetros por cuenta si quieres A/B testing entre configuraciones.

### 4.4 Arrancar con Docker (recomendado)

```bash
docker compose build
docker compose up -d
docker compose logs -f oanda-bot   # ver logs en JSON
```

Estado y logs quedan en:

* `./logs/`    → salidas de contenedor
* `./state/`   → **`trades.sqlite3`** (registro compartido de entradas)
* `./config/`  → montado read-only

### 4.5 Correr sin Docker (dev)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export CONFIG_PATH=config/accounts.yaml
export TRADE_DB_PATH=state/trades.sqlite3
python -m src.main
```

### 4.6 Correr los tests

```bash
pip install pytest
pytest -q
```

## 5. Observabilidad

### 5.1 Logs (`structlog` JSON en stdout)

En cada tick, y por cada instrumento evaluado, el bot emite un evento
`evaluation` con **los números que está evaluando**:

```json
{"event":"evaluation","instrument":"EUR_USD","signal":"NONE","reason":"below_long_threshold",
 "price":1.08423,"ema_fast":1.08419,"trend":40.0,"momentum":12.3,"strength":18.4,
 "fotsi":24.7,"atr":0.00021,"atr_pct":0.000194,"entry_threshold":55.0,
 "strength_min":20.0,"bar_time":"2026-07-20T14:22:00+00:00"}
```

Nivel del proceso: `tick` con equity, moneda, posiciones abiertas, y estado
del kill-switch. Cuando se llena una orden: `order_filled` con precio de fill,
SL, TP y trade_id.

### 5.2 Telegram

Cada entrada nueva dispara un mensaje con:

* Instrumento y lado (LONG / SHORT)
* Cuenta y `Trade ID` de OANDA
* Estrategia usada
* Precio de entrada, **Stop-Loss**, **Take-Profit**
* Equity y monto arriesgado
* Timestamp de apertura (UTC ISO-8601)
* Valores FOTSI/trend/momentum/strength/ATR en el momento de la señal

Cada cierre (TP / SL / time-exit / manual) dispara un mensaje adicional con
precio de salida, P/L realizado y motivo. Los eventos suscritos se controlan
en `notifications.telegram.notify_on`.

## 6. Registro de trades (SQLite compartido)

El bot escribe cada entrada abierta en `state/trades.sqlite3`. La tabla
`trades` incluye:

```
trade_id, account_name, account_id, instrument, side, units,
entry_price, stop_loss_price, take_profit_price, strategy, reason,
opened_at, closed_at, exit_price, close_reason, realized_pl, extra_json
```

El fichero se abre en modo **WAL** para que un segundo proceso (el "closer
bot" que quieres añadir después) pueda leer trades abiertos, consultar precio
en OANDA y cerrar posiciones sin colisionar con las escrituras del strategy
bot. Para inspección rápida:

```bash
sqlite3 state/trades.sqlite3 \
  "SELECT trade_id,instrument,side,entry_price,stop_loss_price,take_profit_price,closed_at
   FROM trades ORDER BY opened_at DESC LIMIT 20;"
```

Un consumidor externo puede reutilizar la misma clase:

```python
from src.persistence import TradeRegistry
reg = TradeRegistry("state/trades.sqlite3")
for t in reg.open_trades():
    print(t["trade_id"], t["instrument"], t["entry_price"], t["take_profit_price"])
```

## 7. Añadir una cuenta más

1. Duplica el bloque bajo `accounts:` en `config/accounts.yaml`.
2. Cambia `name`, `account_id`, `access_token`.
3. `docker compose restart oanda-bot`.

El registro SQLite comparte fila; el `account_name` distingue de qué cuenta
proviene cada entrada.

## 8. Comandos útiles

```bash
docker compose logs -f oanda-bot        # seguir logs
docker compose down                     # parar
docker compose up -d --build            # rebuild + arrancar
sqlite3 state/trades.sqlite3 ".schema"  # inspeccionar el registro
```

## 9. Roadmap del segundo bot ("closer")

Este repo deja preparados:

* Registro `state/trades.sqlite3` en modo WAL (lectura concurrente segura).
* Método `TradeRegistry.open_trades()` para listar entradas activas.
* Método `TradeRegistry.mark_closed(trade_id, exit_price, realized_pl, reason)`
  para registrar cierres desde ese segundo proceso.
* Método `OandaClient.close_trade(trade_id)` y `get_trade(trade_id)` reusables.

El closer típicamente vive como un servicio adicional en `docker-compose.yml`
compartiendo el volumen `./state`.

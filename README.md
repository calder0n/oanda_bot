# OANDA Scalping Bot — ADX + RSI

Bot de **scalping en M1** para **OANDA** que opera todos los instrumentos
disponibles en la cuenta demo, con arquitectura multi-cuenta y despliegue en
Docker.

> ⚠️ **Solo cuenta demo (practice)** hasta que la estrategia haya sido validada
> con backtests + incubación en demo. El repo viene configurado en
> `environment: practice`. No lo cambies a `live` sin haber hecho ese trabajo.

---

## Estrategia (rama `scalping_trading`)

Evaluada sobre la última vela cerrada de 1 minuto:

| Condición                                   | Acción |
|--------------------------------------------|--------|
| `ADX >= 23`  **y**  `RSI <= 35`            | **COMPRA** (LONG)  |
| `ADX >= 23`  **y**  `RSI >= 65`            | **VENTA** (SHORT) |

Todo lo demás se descarta. Cada entrada lleva SL/TP del lado del servidor:

- **Stop Loss**  = entrada ∓ `atr_stop_mult × ATR`   (por defecto 1.5×ATR)
- **Take Profit**= entrada ± `atr_target_mult × ATR` (por defecto 1.5×ATR)

### Tuning para $1,000 USD

Los defaults en `config/accounts.example.yaml` están pensados para un balance
inicial de **$1,000 USD**:

| Parámetro              | Valor  | Efecto sobre $1,000     |
|------------------------|--------|-------------------------|
| `risk_per_trade_pct`   | 1.0    | ~$10 arriesgados/trade  |
| `max_open_positions`   | 3      | ~$30 en riesgo total    |
| `max_daily_loss_pct`   | 3.0    | kill-switch diario ~$30 |

El sizing (`riesgo / distancia_al_stop`) puede quedar por debajo del mínimo del
instrumento en activos caros (XAU_USD, índices). Esos casos se registran como
`skipped_min_size` y son normales en una cuenta de $1k.

---

## Estructura del proyecto

```
oanda_bot/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── config/
│   └── accounts.example.yaml       # copiar a accounts.yaml
├── src/
│   ├── main.py                     # entrypoint (spawnea 1 worker/cuenta)
│   ├── config.py                   # loader YAML + pydantic
│   ├── oanda_client.py             # wrapper OANDA async con retries
│   ├── account_worker.py           # worker aislado por cuenta
│   ├── trade_registry.py           # JSON append-only de entradas
│   ├── strategy/
│   │   ├── base.py
│   │   ├── kevin_davey.py
│   │   ├── scalping_adx_rsi.py     # ← la estrategia de esta rama
│   │   └── registry.py
│   ├── indicators/technical.py     # ADX, RSI, ATR, Donchian, EMA
│   ├── notifications/telegram.py   # Telegram (order_filled)
│   ├── risk/manager.py             # sizing + kill-switch diario
│   └── utils/logger.py
└── tests/
    ├── test_strategy.py
    └── test_scalping.py
```

Cada cuenta = 1 `asyncio.Task` aislada, con su token, su estrategia y su
registro de trades. Añadir otra cuenta = añadir un bloque YAML.

---

## 1. Prerrequisitos

- Cuenta demo de OANDA (fxTrade Practice): <https://www.oanda.com/demo-account/>
- Token v20 (Manage API Access dentro del portal practice)
- ID de la cuenta practice (p. ej. `101-001-0000000-001`)
- Docker + Docker Compose v2

Opcional (dev/backtest local): Python 3.12+.

---

## 2. Instalación — Docker (recomendado)

```bash
git clone <este-repo> oanda_bot
cd oanda_bot
git checkout scalping_trading

# 1. Copiar los archivos de ejemplo
cp .env.example .env
cp config/accounts.example.yaml config/accounts.yaml

# 2. Editar config/accounts.yaml con tu token y account_id de OANDA demo
#    y (opcional) con el bot_token + chat_id de Telegram
$EDITOR config/accounts.yaml

# 3. Construir y arrancar
docker compose build
docker compose up -d

# 4. Ver logs en vivo (JSON, un evento por línea)
docker compose logs -f oanda-bot
```

Parar y limpiar:

```bash
docker compose down
```

### Qué hace el contenedor al arrancar

1. Carga `config/accounts.yaml` (montado solo-lectura).
2. Consulta `AccountInstruments` de OANDA y obtiene **todos** los instrumentos
   operables en tu cuenta demo (FX majors/minors/exóticos, metales, índices,
   commodities y CFDs de cripto donde estén disponibles).
3. Lanza un worker `asyncio` por cuenta; cada worker consulta velas M1 cada
   `tick_interval_seconds` y evalúa la estrategia sobre cada instrumento.
4. Las órdenes se envían con SL/TP adjuntos del lado del servidor (OANDA los
   respeta aunque el bot se caiga).
5. Cada entrada se guarda en `state/trades_<cuenta>.json` (ID, precio de
   compra, SL, TP, estado — ver más abajo).

---

## 3. Instalación — local (dev)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/accounts.example.yaml config/accounts.yaml
$EDITOR config/accounts.yaml

CONFIG_PATH=config/accounts.yaml STATE_DIR=./state python -m src.main
```

Tests:

```bash
pip install pytest
pytest -q
```

---

## 4. Multi-cuenta

`config/accounts.yaml` tiene dos secciones: `defaults:` y `accounts:`. Todo lo
que se ponga bajo una cuenta sobreescribe los defaults.

```yaml
defaults:
  environment: practice
  granularity: M1
  instruments: "ALL_TRADEABLE"
  strategy:
    name: scalping_adx_rsi
    params:
      adx_min: 23.0
      rsi_oversold: 35.0
      rsi_overbought: 65.0
      atr_stop_mult: 1.5
      atr_target_mult: 1.5
      risk_per_trade_pct: 1.0
      max_open_positions: 3
      max_daily_loss_pct: 3.0

accounts:
  - name: demo-primary
    account_id: "101-001-0000000-001"
    access_token: "DEMO_TOKEN_A"

  - name: demo-secondary
    account_id: "101-001-0000000-002"
    access_token: "DEMO_TOKEN_B"
    strategy:
      params:
        adx_min: 25.0           # override
        rsi_oversold: 30.0
        rsi_overbought: 70.0

  - name: demo-fx-only
    account_id: "101-001-0000000-003"
    access_token: "DEMO_TOKEN_C"
    instruments:                # lista explícita
      - EUR_USD
      - USD_JPY
      - GBP_USD
```

Añadir una cuarta cuenta = otro item en la lista. El runner escala
horizontalmente dentro del mismo contenedor (una task asyncio por cuenta) y
cada cuenta tiene su propio `state/trades_<cuenta>.json`.

### Otras estrategias por cuenta

Las estrategias se registran por nombre en `src/strategy/registry.py`. La
estrategia `kevin_davey_breakout` sigue disponible (M15, breakout Donchian);
se puede activar por cuenta sobreescribiendo `strategy.name` y
`granularity`.

---

## 5. Notificaciones de Telegram

El bot envía un mensaje cada vez que se abre una nueva transacción
(`order_filled`):

1. **Crear el bot**: habla con [@BotFather](https://t.me/BotFather) en
   Telegram, corre `/newbot`, y guarda el token que te devuelve (tipo
   `123456789:AAH...`).
2. **Obtener tu chat_id**: inicia un chat con tu bot (`/start`) y abre
   `https://api.telegram.org/bot<TOKEN>/getUpdates`. El campo `chat.id` del
   JSON es lo que necesitas (en grupos es negativo).
3. **Rellenar `config/accounts.yaml`** bajo la cuenta (o bajo `defaults:`):

   ```yaml
   notifications:
     telegram:
       enabled: true
       bot_token: "123456789:AAH..."
       chat_id:   "987654321"
       notify_on:
         - order_filled
   ```

4. Reiniciar: `docker compose restart`. Recibirás:

   ```
   🟢 New LONG on EUR_USD
   • Account: demo-primary
   • Trade ID: 12345
   • Units: 1200
   • Entry: 1.08425
   • Stop: 1.08270
   • Target: 1.08580
   • Reason: adx_strong+rsi_oversold
   • Equity: 1002.37 (risk ≈ 10.02)
   ```

Cada cuenta puede mandar a un chat distinto — basta con override el bloque
`notifications` dentro del account.

---

## 6. Registro de entradas (para el bot cerrador)

Cada cuenta escribe su histórico en `state/trades_<cuenta>.json`. Formato
pensado para que un segundo bot lo lea, vigile el precio y cierre las
posiciones cuando corresponda:

```json
{
  "account": "demo-primary",
  "trades": [
    {
      "id": "12345",
      "client_tag": "demo-primary:scalping_adx_rsi:1713547200123",
      "account": "demo-primary",
      "instrument": "EUR_USD",
      "side": "LONG",
      "units": 1200,
      "entry_price": 1.08425,
      "stop_loss": 1.08270,
      "take_profit": 1.08580,
      "sell_price": null,
      "status": "open",
      "opened_at": "2025-04-19T13:20:00+00:00",
      "closed_at": null,
      "reason": "adx_strong+rsi_oversold",
      "strategy": "scalping_adx_rsi",
      "indicators": {"adx": 28.4, "rsi": 32.1, "atr": 0.00031, "price": 1.08425}
    }
  ]
}
```

Cuando ese segundo bot cierre una entrada, puede usar
`TradeRegistry.mark_closed(trade_id, sell_price, close_reason)` en
`src/trade_registry.py` para actualizar el mismo archivo (`sell_price`,
`closed_at`, `status: "closed"`).

---

## 7. Logs (los números que evalúa cada tick)

Formato JSON, una línea por evento. En cada tick el bot emite:

| Evento             | Qué muestra |
|--------------------|-------------|
| `tick`             | equity, nº de posiciones abiertas, estado del kill-switch |
| `evaluation`       | por instrumento: `price`, `adx`, `rsi`, `atr`, `adx_min`, `rsi_oversold`, `rsi_overbought`, `signal`, `reason` |
| `skipped_min_size` | la señal disparó pero `units < minimumTradeSize` |
| `order_filled`     | orden ejecutada en OANDA (ID, entrada, SL, TP) |

Ejemplos:

```bash
# Todas las evaluaciones de EUR_USD (ves los números exactos: ADX, RSI, ATR)
docker compose logs --no-log-prefix oanda-bot \
  | jq 'select(.event=="evaluation" and .instrument=="EUR_USD")'

# Solo las órdenes abiertas
docker compose logs --no-log-prefix oanda-bot \
  | jq 'select(.event=="order_filled")'

# Revisar el registro persistente
cat state/trades_demo-primary.json | jq '.trades[] | select(.status=="open")'
```

Para silenciar las `evaluation` por instrumento (si generan demasiada
verbosidad en M1), poner `log_evaluations: false` bajo la cuenta o los
defaults.

**Apagado limpio**: `docker compose stop` manda SIGTERM; el worker termina su
tick actual y sale. Los SL/TP quedan del lado de OANDA, así que las posiciones
abiertas siguen protegidas.

**Recargar config sin rebuild**: el volumen está montado en vivo. Edita
`config/accounts.yaml` y `docker compose restart`.

---

## 8. Notas de seguridad

- `config/accounts.yaml` está en `.gitignore`. Nunca commitees los tokens.
- Usa un token distinto por cuenta, scope-ado a ese account_id.
- El Dockerfile corre como usuario no-root.
- Rota tokens periódicamente desde el portal de OANDA.

---

## 9. Troubleshooting

| Síntoma | Causa probable |
|---|---|
| `config_load_failed` al arrancar | `config/accounts.yaml` no existe o está malformado. |
| `V20Error` 401 | Token inválido o `environment` equivocado. |
| No dispara nunca | Con M1 el warmup tarda ~45 velas (~45 min). Verifica `adx` y `rsi` en los logs `evaluation`. |
| `order_failed` con units=0 | La distancia del stop es menor al mínimo del instrumento. |
| `daily_loss_limit_hit` | Kill-switch diario; se resetea a las 00:00 UTC. |
| `telegram_send_failed` | Token/chat_id incorrectos o bot bloqueado en el chat. |
| Muchos `skipped_min_size` | Normal en $1k para activos caros — ver sección "Tuning para $1,000 USD". |

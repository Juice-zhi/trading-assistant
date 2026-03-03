# Trading Assistant

A desktop application that monitors US stocks in real time, detects EMA trend alignment and pullback signals, and sends alerts via GUI and Discord webhook.

---

## Features

- **Real-time scanning** — fetches 1-minute OHLCV data (including pre/post-market) via yfinance every 60 seconds
- **EMA trend detection** — identifies bullish/bearish alignment across EMA20 / EMA50 / EMA200
- **Pullback signals** — detects when price pulls back to EMA20 while the trend is intact
- **Consolidation filter** — three-indicator voting (Bollinger Band width + EMA convergence + price range) suppresses false signals during ranging markets
- **Alert cooldown** — per-symbol cooldown (default 15 min) to avoid notification spam
- **Discord notifications** — plain-text webhook alerts: `AAPL 上涨 / AAPL 下跌 / AAPL 回撤EMA20`
- **Dark-themed GUI** — tkinter Treeview showing live price, EMA values, ATR, distance ratio, and signal state per symbol
- **Configurable** — all parameters adjustable via the Settings dialog, persisted to `config.json`

---

## Strategy Logic

| Condition | Signal |
|-----------|--------|
| EMA20 > EMA50 > EMA200 **and** price > EMA20 | Bullish trend |
| EMA20 < EMA50 < EMA200 **and** price < EMA20 | Bearish trend |
| Trend intact **and** `\|price − EMA20\| / ATR(14) < threshold` | Pullback to EMA20 |
| ≥2 of 3 consolidation indicators triggered | Trend suppressed |

**State machine per symbol:** `NO_TREND → TRENDING → PULLBACK`
Pullback exits back to TRENDING via hysteresis (`threshold × 1.5`) without re-triggering the trend alert.

---

## Project Structure

```
trading_assistant/
├── main.py                 # Entry point, scanner thread, market hours check
├── config.py               # JSON-backed ConfigManager with typed properties
├── scanner.py              # EMA/ATR calculations + per-symbol state machine
├── data_fetcher.py         # yfinance wrapper (1-min OHLCV, prepost=True)
├── notifier.py             # Discord webhook notifier
├── ui/
│   ├── main_window.py      # Dark-themed Treeview, toolbar, queue polling
│   └── settings_dialog.py  # Modal settings dialog (indicators + Discord)
├── tests/                  # Unittest suite (123 tests)
├── requirements.txt
└── config.json             # Auto-generated on first run (git-ignored)
```

---

## Installation

```bash
git clone https://github.com/Juice-zhi/trading-assistant.git
cd trading-assistant
pip install -r requirements.txt
```

> **Requires Python 3.9+.** tkinter is included in the standard library.

---

## Usage

```bash
python main.py
```

The UI opens immediately and runs an initial scan. During market hours (9:30–16:00 ET, weekdays) the scanner polls every 60 seconds automatically.

**Adding symbols:** click **Add** in the toolbar and type a ticker (e.g. `NVDA`).
**Settings:** click **Settings** to adjust EMA periods, ATR multiplier, consolidation thresholds, poll interval, and Discord webhook.

---

## Discord Setup

1. Open the Discord channel you want alerts in
2. **Channel Settings → Integrations → Webhooks → New Webhook**
3. Copy the webhook URL
4. In the app: **Settings → Discord** → paste the URL and enable Discord alerts
5. Click **Test** to verify the connection

Alert format:
```
AAPL 上涨
AAPL 回撤EMA20
AAPL 下跌
```

---

## Configuration

`config.json` is auto-generated on first run. Key parameters:

| Key | Default | Description |
|-----|---------|-------------|
| `symbols` | `["AAPL","TSLA","NVDA","SPY"]` | Symbols to monitor |
| `ema_periods.fast/mid/slow` | `20 / 50 / 200` | EMA periods |
| `atr_period` | `14` | ATR lookback period |
| `atr_multiplier` | `0.5` | Pullback threshold (`\|price−EMA20\|/ATR < X`) |
| `alert_cooldown_minutes` | `15` | Minimum minutes between same-type alerts |
| `poll_interval_seconds` | `60` | Scan frequency |
| `consolidation_min_votes` | `2` | Min indicators (0–3) to classify as consolidation |
| `consolidation_bb_threshold` | `0.03` | BB width threshold (3%) |
| `consolidation_ema_gap_atr` | `1.5` | EMA20–EMA50 gap / ATR threshold |
| `consolidation_range_threshold` | `0.015` | N-bar price range / EMA50 threshold (1.5%) |

---

## Running Tests

```bash
python -m pytest tests/ -v
```

123 tests covering indicator calculations, state machine transitions, consolidation filter, config management, and the scanner thread lifecycle.

---

## License

MIT

---

# 交易助手

实时监控美股、检测 EMA 趋势排列与回撤信号的桌面应用，通过 GUI 界面和 Discord Webhook 推送提醒。

---

## 功能

- **实时扫描** — 通过 yfinance 每60秒拉取1分钟 OHLCV 数据（含盘前/盘后）
- **EMA趋势检测** — 识别 EMA20 / EMA50 / EMA200 多头/空头排列
- **回撤信号** — 趋势延续时检测价格回踩 EMA20
- **盘整过滤** — 三指标投票（布林带宽度 + EMA收敛 + 价格区间）过滤盘整行情中的虚假信号
- **提醒冷却** — 每个 symbol 独立冷却时间（默认15分钟），避免重复提醒
- **Discord 通知** — 纯文本 Webhook 提醒：`AAPL 上涨 / AAPL 下跌 / AAPL 回撤EMA20`
- **深色桌面界面** — tkinter Treeview 实时显示价格、三条 EMA、ATR、距离比值及信号状态
- **可配置** — 所有参数均可在设置对话框中调整，自动保存到 `config.json`

---

## 策略逻辑

| 条件 | 信号 |
|------|------|
| EMA20 > EMA50 > EMA200 且 price > EMA20 | 多头趋势 |
| EMA20 < EMA50 < EMA200 且 price < EMA20 | 空头趋势 |
| 趋势延续 且 `\|price − EMA20\| / ATR(14) < 阈值` | 回撤 EMA20 |
| 3个盘整指标中≥2个触发 | 趋势信号被抑制 |

**每个 symbol 独立状态机：** `NO_TREND → TRENDING → PULLBACK`
回撤通过滞后（`阈值 × 1.5`）退回到 TRENDING，不会重新触发趋势提醒。

---

## 安装

```bash
git clone https://github.com/Juice-zhi/trading-assistant.git
cd trading-assistant
pip install -r requirements.txt
```

> 需要 **Python 3.9+**，tkinter 已内置于标准库。

---

## 使用

```bash
python main.py
```

界面启动后立即执行初始扫描。交易时段（美东时间周一至周五 9:30–16:00）自动每60秒轮询一次。

**添加标的：** 点击工具栏 **Add**，输入股票代码（如 `NVDA`）。
**设置：** 点击 **Settings** 调整 EMA 周期、ATR 倍数、盘整阈值、轮询间隔及 Discord Webhook。

---

## Discord 配置

1. 打开目标 Discord 频道
2. **频道设置 → 整合 → Webhook → 新建 Webhook**
3. 复制 Webhook URL
4. 在应用中：**Settings → Discord** → 粘贴 URL 并启用 Discord 提醒
5. 点击 **Test** 验证连接

提醒格式：
```
AAPL 上涨
AAPL 回撤EMA20
AAPL 下跌
```

---

## 运行测试

```bash
python -m pytest tests/ -v
```

共123个测试，覆盖指标计算、状态机跳转、盘整过滤、配置管理和扫描线程生命周期。

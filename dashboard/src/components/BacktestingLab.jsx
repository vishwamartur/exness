import { useDeferredValue, useEffect, useMemo, useState } from 'react'
import {
    Area,
    AreaChart,
    Bar,
    BarChart,
    CartesianGrid,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from 'recharts'
import { resolveServiceEndpoint } from '../lib/serviceEndpoint'

const TODAY = new Date().toISOString().slice(0, 10)

const STRATEGY_LABELS = {
    ema5_breakout: '5 EMA Breakout',
    traffic_light: 'Traffic Light',
}

const QUICK_SYMBOLS = [
    { label: 'XAUUSD', value: 'XAUUSD' },
    { label: 'EURUSD', value: 'EURUSD' },
]

const FOREX_DEFAULT_FILTERS = {
    sessionStart: '',
    sessionEnd: '',
    excludeStart: '',
    excludeEnd: '',
    squareOffTime: '',
}

const EMPTY_RESULT = {
    summary: {
        total_trades: 0,
        wins: 0,
        losses: 0,
        win_rate: 0,
        profit_factor: null,
        net_profit: 0,
        total_r: 0,
        max_drawdown: 0,
        max_drawdown_pct: 0,
        ending_equity: 0,
        return_pct: 0,
        expectancy_r: 0,
    },
    equity_curve: [],
    monthly: [],
    trades: [],
    warnings: [],
}

function formatCurrency(value) {
    return new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR',
        maximumFractionDigits: 0,
    }).format(Number.isFinite(value) ? value : 0)
}

function formatCompactNumber(value, digits = 2) {
    if (!Number.isFinite(value)) return '-'
    return new Intl.NumberFormat('en-IN', {
        minimumFractionDigits: 0,
        maximumFractionDigits: digits,
    }).format(value)
}

function formatPercent(value) {
    return `${formatCompactNumber(value, 2)}%`
}

function toPayloadValue(value) {
    return value === '' ? null : value
}

function buildPayload(form) {
    return {
        symbol: form.symbol.trim(),
        strategy: form.strategy,
        start_date: form.startDate,
        end_date: form.endDate,
        timeframe: form.timeframe,
        timezone_name: form.timezoneName,
        reward_risk: Number(form.rewardRisk),
        entry_window_bars: Number(form.entryWindowBars),
        initial_capital: Number(form.initialCapital),
        risk_per_trade: Number(form.riskPerTrade),
        session_start: toPayloadValue(form.sessionStart),
        session_end: toPayloadValue(form.sessionEnd),
        exclude_start: toPayloadValue(form.excludeStart),
        exclude_end: toPayloadValue(form.excludeEnd),
        square_off_time: toPayloadValue(form.squareOffTime),
        enter_on_close: form.enterOnClose,
        allow_long: form.allowLong,
        allow_short: form.allowShort,
        ema_period: Number(form.emaPeriod),
        max_trades_per_day: Number(form.maxTradesPerDay),
        traffic_light_max_range: form.trafficLightMaxRange === '' ? null : Number(form.trafficLightMaxRange),
        same_bar_exit_priority: form.sameBarExitPriority,
    }
}

function defaultForm() {
    return {
        symbol: 'XAUUSD',
        strategy: 'ema5_breakout',
        startDate: '2020-01-01',
        endDate: TODAY,
        timeframe: 'M5',
        timezoneName: 'Asia/Kolkata',
        rewardRisk: 3,
        entryWindowBars: 3,
        initialCapital: 100000,
        riskPerTrade: 1000,
        sessionStart: '',
        sessionEnd: '',
        excludeStart: '',
        excludeEnd: '',
        squareOffTime: '',
        enterOnClose: false,
        allowLong: true,
        allowShort: true,
        emaPeriod: 5,
        maxTradesPerDay: 10,
        trafficLightMaxRange: '',
        sameBarExitPriority: 'stop',
    }
}

export default function BacktestingLab() {
    const [form, setForm] = useState(defaultForm)
    const [endpoint, setEndpoint] = useState(null)
    const [catalog, setCatalog] = useState([])
    const [symbolOptions, setSymbolOptions] = useState([])
    const [result, setResult] = useState(EMPTY_RESULT)
    const [loading, setLoading] = useState(false)
    const [booting, setBooting] = useState(true)
    const [error, setError] = useState('')
    const deferredSymbol = useDeferredValue(form.symbol)

    useEffect(() => {
        let active = true

        async function boot() {
            try {
                const resolved = await resolveServiceEndpoint()
                if (!active) return

                setEndpoint(resolved)
                const response = await fetch(`${resolved.apiBase}/api/backtest/strategies`)
                const payload = response.ok ? await response.json() : { strategies: [] }
                if (!active) return
                setCatalog(payload.strategies || [])
            } catch (bootError) {
                if (!active) return
                setError('Backtest API is not reachable yet. Start the MT5 stream server and keep the terminal connected.')
            } finally {
                if (active) setBooting(false)
            }
        }

        boot()
        return () => {
            active = false
        }
    }, [])

    useEffect(() => {
        if (!endpoint) return undefined

        let active = true
        const query = deferredSymbol.trim()
        const timer = setTimeout(async () => {
            try {
                const response = await fetch(`${endpoint.apiBase}/api/backtest/symbols?query=${encodeURIComponent(query)}&limit=8`)
                if (!response.ok || !active) return
                const payload = await response.json()
                if (!active) return
                setSymbolOptions(payload.symbols || [])
            } catch {
                if (active) setSymbolOptions([])
            }
        }, 220)

        return () => {
            active = false
            clearTimeout(timer)
        }
    }, [deferredSymbol, endpoint])

    const runBacktest = async event => {
        event.preventDefault()
        if (!endpoint) {
            setError('The backtest API endpoint has not been discovered yet.')
            return
        }

        setLoading(true)
        setError('')

        try {
            const response = await fetch(`${endpoint.apiBase}/api/backtest/run`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(buildPayload(form)),
            })

            const payload = await response.json()
            if (!response.ok) {
                throw new Error(payload.detail || 'Backtest request failed.')
            }

            setResult(payload)
        } catch (requestError) {
            setError(requestError.message || 'Unable to complete the backtest request.')
        } finally {
            setLoading(false)
        }
    }

    const summary = result.summary || EMPTY_RESULT.summary
    const equityCurve = useMemo(() => {
        return (result.equity_curve || []).map((point, index) => ({
            ...point,
            label: point.time ? new Date(point.time).toLocaleDateString('en-IN', { year: '2-digit', month: 'short', day: 'numeric' }) : `Start ${index === 0 ? '' : index}`,
            drawdown_pct: Number(point.drawdown_pct || 0),
            equity: Number(point.equity || 0),
        }))
    }, [result.equity_curve])

    const monthlyCurve = useMemo(() => {
        return (result.monthly || []).map(item => ({
            ...item,
            month_label: item.month ? item.month.slice(2) : '-',
            pnl: Number(item.pnl || 0),
            trades: Number(item.trades || 0),
        }))
    }, [result.monthly])

    const trades = result.trades || []
    const winningTrades = summary.wins || 0
    const losingTrades = summary.losses || 0
    const applyQuickSymbol = value => {
        setForm(current => ({
            ...current,
            symbol: value,
            ...FOREX_DEFAULT_FILTERS,
        }))
    }

    return (
        <div className="backtest-shell fade-in">
            <div className="backtest-hero">
                <div>
                    <div className="backtest-kicker">Power of Stocks Strategy Lab</div>
                    <h2>Interactive backtesting for XAUUSD and EURUSD breakouts</h2>
                    <p>
                        Run MT5-backed backtests for the 5 EMA breakout and Traffic Light setups on 5-minute data,
                        tune the rules, and inspect risk statistics before you trust a live session.
                    </p>
                </div>
                <div className="hero-badges">
                    <span className="hero-badge">MT5 candles</span>
                    <span className="hero-badge">1:3 default RR</span>
                    <span className="hero-badge">Session filters</span>
                </div>
            </div>

            <div className="lab-layout">
                <aside className="lab-sidebar">
                    <div className="card lab-panel">
                        <div className="card-title">Strategy</div>
                        <div className="strategy-switch">
                            {['ema5_breakout', 'traffic_light'].map(id => (
                                <button
                                    key={id}
                                    type="button"
                                    className={`strategy-button ${form.strategy === id ? 'active' : ''}`}
                                    onClick={() => setForm(current => ({ ...current, strategy: id }))}
                                >
                                    <span>{STRATEGY_LABELS[id]}</span>
                                    <small>{id === 'ema5_breakout' ? 'EMA alert candle breakout' : 'Two-candle contrast range'}</small>
                                </button>
                            ))}
                        </div>
                    </div>

                    <form className="card lab-panel" onSubmit={runBacktest}>
                        <div className="card-title">Inputs</div>

                        <div className="chip-row">
                            {QUICK_SYMBOLS.map(item => (
                                <button
                                    key={item.value}
                                    type="button"
                                    className={`chip ${form.symbol.toUpperCase() === item.value ? 'active' : ''}`}
                                    onClick={() => applyQuickSymbol(item.value)}
                                >
                                    {item.label}
                                </button>
                            ))}
                        </div>

                        <div className="field">
                            <label>Symbol</label>
                            <input
                                value={form.symbol}
                                onChange={event => setForm(current => ({ ...current, symbol: event.target.value }))}
                                placeholder="Type broker symbol"
                            />
                            {symbolOptions.length > 0 && (
                                <div className="suggestions">
                                    {symbolOptions.map(option => (
                                        <button
                                            key={`${option.name}-${option.path}`}
                                            type="button"
                                            className="suggestion-item"
                                            onClick={() => setForm(current => ({ ...current, symbol: option.name }))}
                                        >
                                            <strong>{option.name}</strong>
                                            <span>{option.description || option.path || 'MT5 symbol'}</span>
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>

                        <div className="form-grid two-col">
                            <div className="field">
                                <label>Start date</label>
                                <input
                                    type="date"
                                    min="2020-01-01"
                                    value={form.startDate}
                                    onChange={event => setForm(current => ({ ...current, startDate: event.target.value }))}
                                />
                            </div>
                            <div className="field">
                                <label>End date</label>
                                <input
                                    type="date"
                                    min="2020-01-01"
                                    value={form.endDate}
                                    onChange={event => setForm(current => ({ ...current, endDate: event.target.value }))}
                                />
                            </div>
                        </div>

                        <div className="form-grid two-col">
                            <div className="field">
                                <label>Timeframe</label>
                                <select
                                    value={form.timeframe}
                                    onChange={event => setForm(current => ({ ...current, timeframe: event.target.value }))}
                                >
                                    <option value="M5">M5</option>
                                    <option value="M15">M15</option>
                                    <option value="M1">M1</option>
                                </select>
                            </div>
                            <div className="field">
                                <label>Entry window</label>
                                <input
                                    type="number"
                                    min="1"
                                    max="20"
                                    value={form.entryWindowBars}
                                    onChange={event => setForm(current => ({ ...current, entryWindowBars: event.target.value }))}
                                />
                            </div>
                        </div>

                        <div className="form-grid two-col">
                            <div className="field">
                                <label>Reward risk</label>
                                <input
                                    type="number"
                                    min="0.5"
                                    step="0.5"
                                    value={form.rewardRisk}
                                    onChange={event => setForm(current => ({ ...current, rewardRisk: event.target.value }))}
                                />
                            </div>
                            <div className="field">
                                <label>Risk per trade</label>
                                <input
                                    type="number"
                                    min="100"
                                    step="100"
                                    value={form.riskPerTrade}
                                    onChange={event => setForm(current => ({ ...current, riskPerTrade: event.target.value }))}
                                />
                            </div>
                        </div>

                        <div className="form-grid two-col">
                            <div className="field">
                                <label>Session start</label>
                                <input
                                    type="time"
                                    value={form.sessionStart}
                                    onChange={event => setForm(current => ({ ...current, sessionStart: event.target.value }))}
                                />
                            </div>
                            <div className="field">
                                <label>Session end</label>
                                <input
                                    type="time"
                                    value={form.sessionEnd}
                                    onChange={event => setForm(current => ({ ...current, sessionEnd: event.target.value }))}
                                />
                            </div>
                        </div>

                        <div className="form-grid two-col">
                            <div className="field">
                                <label>No-trade start</label>
                                <input
                                    type="time"
                                    value={form.excludeStart}
                                    onChange={event => setForm(current => ({ ...current, excludeStart: event.target.value }))}
                                />
                            </div>
                            <div className="field">
                                <label>No-trade end</label>
                                <input
                                    type="time"
                                    value={form.excludeEnd}
                                    onChange={event => setForm(current => ({ ...current, excludeEnd: event.target.value }))}
                                />
                            </div>
                        </div>

                        <div className="form-grid two-col">
                            <div className="field">
                                <label>Square off</label>
                                <input
                                    type="time"
                                    value={form.squareOffTime}
                                    onChange={event => setForm(current => ({ ...current, squareOffTime: event.target.value }))}
                                />
                            </div>
                            <div className="field">
                                <label>Max trades / day</label>
                                <input
                                    type="number"
                                    min="1"
                                    max="100"
                                    value={form.maxTradesPerDay}
                                    onChange={event => setForm(current => ({ ...current, maxTradesPerDay: event.target.value }))}
                                />
                            </div>
                        </div>

                        {form.strategy === 'ema5_breakout' ? (
                            <div className="form-grid two-col">
                                <div className="field">
                                    <label>EMA period</label>
                                    <input
                                        type="number"
                                        min="2"
                                        max="50"
                                        value={form.emaPeriod}
                                        onChange={event => setForm(current => ({ ...current, emaPeriod: event.target.value }))}
                                    />
                                </div>
                                <div className="field">
                                    <label>Trigger mode</label>
                                    <select
                                        value={form.enterOnClose ? 'close' : 'breakout'}
                                        onChange={event => setForm(current => ({ ...current, enterOnClose: event.target.value === 'close' }))}
                                    >
                                        <option value="breakout">Breakout touch</option>
                                        <option value="close">Close confirm</option>
                                    </select>
                                </div>
                            </div>
                        ) : (
                            <div className="form-grid two-col">
                                <div className="field">
                                    <label>Max range points</label>
                                    <input
                                        type="number"
                                        min="1"
                                        placeholder="Optional"
                                        value={form.trafficLightMaxRange}
                                        onChange={event => setForm(current => ({ ...current, trafficLightMaxRange: event.target.value }))}
                                    />
                                </div>
                                <div className="field">
                                    <label>Trigger mode</label>
                                    <select
                                        value={form.enterOnClose ? 'close' : 'breakout'}
                                        onChange={event => setForm(current => ({ ...current, enterOnClose: event.target.value === 'close' }))}
                                    >
                                        <option value="breakout">Breakout touch</option>
                                        <option value="close">Close confirm</option>
                                    </select>
                                </div>
                            </div>
                        )}

                        <div className="form-grid two-col">
                            <label className="toggle-field">
                                <input
                                    type="checkbox"
                                    checked={form.allowLong}
                                    onChange={event => setForm(current => ({ ...current, allowLong: event.target.checked }))}
                                />
                                <span>Enable longs</span>
                            </label>
                            <label className="toggle-field">
                                <input
                                    type="checkbox"
                                    checked={form.allowShort}
                                    onChange={event => setForm(current => ({ ...current, allowShort: event.target.checked }))}
                                />
                                <span>Enable shorts</span>
                            </label>
                        </div>

                        <div className="field">
                            <label>Same-bar tie-break</label>
                            <select
                                value={form.sameBarExitPriority}
                                onChange={event => setForm(current => ({ ...current, sameBarExitPriority: event.target.value }))}
                            >
                                <option value="stop">Assume stop first</option>
                                <option value="target">Assume target first</option>
                            </select>
                        </div>

                        <button type="submit" className="run-button" disabled={loading || booting}>
                            {loading ? 'Running backtest...' : 'Run backtest'}
                        </button>
                    </form>

                    <div className="card lab-panel assumptions-panel">
                        <div className="card-title">Assumptions</div>
                        <ul className="flat-list">
                            <li>Equity uses fixed risk per trade, so one full stop equals exactly the configured risk amount.</li>
                            <li>By default, bars that tag both stop and target on the same candle are counted as stop-outs.</li>
                            <li>Signals are only formed and entered inside the active session window, excluding any no-trade window.</li>
                        </ul>
                    </div>
                </aside>

                <section className="lab-results">
                    {booting ? (
                        <div className="card results-empty">Connecting to the MT5 analytics service...</div>
                    ) : (
                        <>
                            {error && <div className="error-banner">{error}</div>}

                            <div className="metrics-grid">
                                <MetricCard title="Win rate" value={formatPercent(summary.win_rate)} tone="green" />
                                <MetricCard title="Profit factor" value={summary.profit_factor ?? '-'} tone="blue" />
                                <MetricCard title="Max drawdown" value={`${formatCurrency(summary.max_drawdown)} / ${formatPercent(summary.max_drawdown_pct)}`} tone="red" />
                                <MetricCard title="Net profit" value={formatCurrency(summary.net_profit)} tone={summary.net_profit >= 0 ? 'green' : 'red'} />
                                <MetricCard title="Ending equity" value={formatCurrency(summary.ending_equity)} tone="blue" />
                                <MetricCard title="Total R" value={formatCompactNumber(summary.total_r, 2)} tone="amber" />
                            </div>

                            <div className="results-grid">
                                <div className="card chart-card">
                                    <div className="card-title">Equity Curve</div>
                                    {equityCurve.length > 1 ? (
                                        <div className="chart-wrap">
                                            <ResponsiveContainer width="100%" height="100%">
                                                <AreaChart data={equityCurve}>
                                                    <defs>
                                                        <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
                                                            <stop offset="0%" stopColor="#22c55e" stopOpacity={0.45} />
                                                            <stop offset="100%" stopColor="#22c55e" stopOpacity={0.05} />
                                                        </linearGradient>
                                                    </defs>
                                                    <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
                                                    <XAxis dataKey="label" tick={{ fill: '#94a3b8', fontSize: 11 }} minTickGap={40} />
                                                    <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={value => formatCompactNumber(value, 0)} />
                                                    <Tooltip
                                                        formatter={value => formatCurrency(Number(value))}
                                                        labelFormatter={label => label}
                                                        contentStyle={{ background: '#0f172a', border: '1px solid #22304b', borderRadius: 12 }}
                                                    />
                                                    <Area type="monotone" dataKey="equity" stroke="#22c55e" fill="url(#equityFill)" strokeWidth={2.5} />
                                                </AreaChart>
                                            </ResponsiveContainer>
                                        </div>
                                    ) : (
                                        <div className="results-empty">Run a backtest to plot the equity curve.</div>
                                    )}
                                </div>

                                <div className="card chart-card">
                                    <div className="card-title">Monthly P&L</div>
                                    {monthlyCurve.length > 0 ? (
                                        <div className="chart-wrap">
                                            <ResponsiveContainer width="100%" height="100%">
                                                <BarChart data={monthlyCurve}>
                                                    <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
                                                    <XAxis dataKey="month_label" tick={{ fill: '#94a3b8', fontSize: 11 }} />
                                                    <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={value => formatCompactNumber(value, 0)} />
                                                    <Tooltip
                                                        formatter={value => formatCurrency(Number(value))}
                                                        labelFormatter={label => label}
                                                        contentStyle={{ background: '#0f172a', border: '1px solid #22304b', borderRadius: 12 }}
                                                    />
                                                    <Bar dataKey="pnl" fill="#38bdf8" radius={[6, 6, 0, 0]} />
                                                </BarChart>
                                            </ResponsiveContainer>
                                        </div>
                                    ) : (
                                        <div className="results-empty">Monthly performance will appear once trades are generated.</div>
                                    )}
                                </div>
                            </div>

                            <div className="results-grid secondary">
                                <div className="card insight-card">
                                    <div className="card-title">Run Snapshot</div>
                                    <div className="snapshot-grid">
                                        <SnapshotRow label="Strategy" value={STRATEGY_LABELS[form.strategy]} />
                                        <SnapshotRow label="Symbol" value={form.symbol || '-'} />
                                        <SnapshotRow label="Trades" value={`${summary.total_trades} (${winningTrades}W / ${losingTrades}L)`} />
                                        <SnapshotRow label="Expectancy" value={`${formatCompactNumber(summary.expectancy_r, 2)} R`} />
                                        <SnapshotRow label="Return" value={formatPercent(summary.return_pct)} />
                                        <SnapshotRow label="Entry window" value={`${form.entryWindowBars} candles`} />
                                    </div>
                                </div>

                                <div className="card insight-card">
                                    <div className="card-title">Warnings</div>
                                    {result.warnings && result.warnings.length > 0 ? (
                                        <ul className="flat-list compact">
                                            {result.warnings.map(warning => <li key={warning}>{warning}</li>)}
                                        </ul>
                                    ) : (
                                        <div className="results-empty tight">No engine warnings for the latest run.</div>
                                    )}
                                </div>
                            </div>

                            <div className="card table-card">
                                <div className="table-header">
                                    <div className="card-title">Trade Ledger</div>
                                    <span>{summary.total_trades} closed trades</span>
                                </div>
                                {trades.length > 0 ? (
                                    <div className="table-wrap">
                                        <table className="data-table">
                                            <thead>
                                                <tr>
                                                    <th>Exit</th>
                                                    <th>Direction</th>
                                                    <th>Entry</th>
                                                    <th>Exit</th>
                                                    <th>R</th>
                                                    <th>P&L</th>
                                                    <th>Reason</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {trades.map(trade => (
                                                    <tr key={`${trade.entry_time}-${trade.exit_time}-${trade.direction}`}>
                                                        <td>{new Date(trade.exit_time).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}</td>
                                                        <td className={trade.direction === 'LONG' ? 'text-green' : 'text-red'}>{trade.direction}</td>
                                                        <td>{formatCompactNumber(trade.entry_price, 2)}</td>
                                                        <td>{formatCompactNumber(trade.exit_price, 2)}</td>
                                                        <td className={trade.result_r >= 0 ? 'text-green' : 'text-red'}>{formatCompactNumber(trade.result_r, 2)}</td>
                                                        <td className={trade.pnl >= 0 ? 'text-green' : 'text-red'}>{formatCurrency(trade.pnl)}</td>
                                                        <td>{trade.exit_reason}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                ) : (
                                    <div className="results-empty">No trades yet. Adjust the symbol, date window, or session filters and run again.</div>
                                )}
                            </div>
                        </>
                    )}
                </section>
            </div>

            {catalog.length > 0 && (
                <div className="catalog-strip">
                    {catalog.map(item => (
                        <div key={item.id} className="catalog-card">
                            <strong>{item.name}</strong>
                            <span>{item.description}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

function MetricCard({ title, value, tone = 'blue' }) {
    return (
        <div className={`metric-card tone-${tone}`}>
            <span>{title}</span>
            <strong>{value}</strong>
        </div>
    )
}

function SnapshotRow({ label, value }) {
    return (
        <div className="snapshot-row">
            <span>{label}</span>
            <strong>{value}</strong>
        </div>
    )
}

export default function PositionsTable({ positions }) {
    if (!positions || positions.length === 0) {
        return (
            <div className="card" style={{ height: '100%' }}>
                <div className="card-title">📂 Open Positions</div>
                <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: 24 }}>
                    No open positions
                </div>
            </div>
        )
    }

    return (
        <div className="card" style={{ height: '100%', overflowY: 'auto' }}>
            <div className="card-title" style={{ justifyContent: 'space-between' }}>
                <span>📂 Open Positions</span>
                <span className="badge badge-blue">{positions.length} open</span>
            </div>
            <table className="data-table">
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Dir</th>
                        <th>Lots</th>
                        <th>Entry</th>
                        <th>P&L</th>
                        <th>SL</th>
                        <th>TP</th>
                        <th>Progress</th>
                    </tr>
                </thead>
                <tbody>
                    {positions.map((p, i) => {
                        const pl = p.profit ?? 0
                        const plColor = pl >= 0 ? 'var(--green)' : 'var(--red)'
                        const dirColor = p.direction === 'BUY' ? 'var(--green)' : 'var(--red)'

                        // Progress toward TP (0-100%)
                        const entry = p.entry_price ?? p.price_open ?? 0
                        const tp = p.tp_price ?? p.sl_tp?.[1] ?? 0
                        const sl = p.sl_price ?? p.sl_tp?.[0] ?? 0
                        const cur = p.price_current ?? entry
                        let progress = 0
                        if (tp !== entry && tp !== 0) {
                            progress = Math.max(0, Math.min(100, ((cur - entry) / (tp - entry)) * 100))
                        }

                        return (
                            <tr key={i} className="fade-in">
                                <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{p.symbol}</td>
                                <td style={{ color: dirColor, fontWeight: 700 }}>{p.type === 0 || p.direction === 'BUY' ? 'BUY' : 'SELL'}</td>
                                <td>{(p.volume ?? p.lot ?? 0).toFixed(2)}</td>
                                <td>{(entry).toFixed(5)}</td>
                                <td style={{ color: plColor, fontWeight: 600 }}>
                                    {pl >= 0 ? '+' : ''}{pl.toFixed(2)}
                                </td>
                                <td style={{ color: 'var(--red)' }}>{sl > 0 ? sl.toFixed(5) : '—'}</td>
                                <td style={{ color: 'var(--green)' }}>{tp > 0 ? tp.toFixed(5) : '—'}</td>
                                <td style={{ minWidth: 70 }}>
                                    <div className="progress-bar">
                                        <div
                                            className="progress-bar-fill"
                                            style={{
                                                width: `${progress}%`,
                                                background: progress > 75 ? 'var(--green)' : progress > 40 ? 'var(--blue)' : 'var(--purple)',
                                            }}
                                        />
                                    </div>
                                    <div style={{ fontSize: 9, color: 'var(--text-muted)', marginTop: 2 }}>{progress.toFixed(0)}%</div>
                                </td>
                            </tr>
                        )
                    })}
                </tbody>
            </table>
        </div>
    )
}

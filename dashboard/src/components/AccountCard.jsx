export default function AccountCard({ account, connected }) {
    const equity = account?.equity ?? 0
    const balance = account?.balance ?? 0
    const floatPL = account?.profit ?? 0
    const dayPL = account?.day_pl ?? 0
    const currency = account?.currency ?? 'USD'

    const plColor = floatPL >= 0 ? 'var(--green)' : 'var(--red)'
    const dayColor = dayPL >= 0 ? 'var(--green)' : 'var(--red)'

    const drawdownPct = balance > 0 ? Math.max(0, ((balance - equity) / balance) * 100) : 0

    return (
        <div className="card" style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16 }}>
            {/* Status */}
            <div>
                <div className="card-title">
                    <span className={`pulse ${connected ? '' : 'red'}`} />
                    {connected ? 'Live' : 'Disconnected'}
                </div>
                <div className="stat-value" style={{ fontSize: 20 }}>
                    {connected ? 'ONLINE' : 'OFFLINE'}
                </div>
                <div className="stat-label">WebSocket</div>
            </div>

            {/* Balance */}
            <div>
                <div className="card-title">Balance</div>
                <div className="stat-value">{balance.toLocaleString('en-US', { minimumFractionDigits: 2 })}</div>
                <div className="stat-label">{currency}</div>
            </div>

            {/* Equity */}
            <div>
                <div className="card-title">Equity</div>
                <div className="stat-value">{equity.toLocaleString('en-US', { minimumFractionDigits: 2 })}</div>
                <div style={{ marginTop: 6 }}>
                    <div className="progress-bar">
                        <div
                            className="progress-bar-fill"
                            style={{
                                width: `${Math.min(100, (equity / Math.max(balance, 1)) * 100)}%`,
                                background: drawdownPct > 5 ? 'var(--red)' : 'var(--blue)',
                            }}
                        />
                    </div>
                    <div className="stat-label" style={{ marginTop: 4 }}>
                        {drawdownPct.toFixed(2)}% drawdown
                    </div>
                </div>
            </div>

            {/* P&L */}
            <div>
                <div className="card-title">Floating P&amp;L</div>
                <div className="stat-value" style={{ color: plColor, fontSize: 22 }}>
                    {floatPL >= 0 ? '+' : ''}{floatPL.toFixed(2)}
                </div>
                <div className="stat-label" style={{ color: dayColor }}>
                    Day: {dayPL >= 0 ? '+' : ''}{dayPL.toFixed(2)}
                </div>
            </div>
        </div>
    )
}

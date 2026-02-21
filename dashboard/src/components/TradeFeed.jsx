export default function TradeFeed({ recentTrades }) {
    return (
        <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
            <div className="card-title" style={{ justifyContent: 'space-between' }}>
                <span>⚡ Trade Feed</span>
                {recentTrades.length > 0 && (
                    <span className="badge badge-green">{recentTrades.length} trades</span>
                )}
            </div>
            <div style={{ overflowY: 'auto', flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
                {recentTrades.length === 0 ? (
                    <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: 24 }}>
                        Awaiting session open…
                    </div>
                ) : (
                    recentTrades.map((t, i) => {
                        const isBuy = t.direction === 'BUY'
                        const time = t.timestamp ? new Date(t.timestamp).toLocaleTimeString() : '—'
                        return (
                            <div
                                key={i}
                                className="fade-in"
                                style={{
                                    background: isBuy ? '#0a2010' : '#200a0a',
                                    border: `1px solid ${isBuy ? 'var(--green)' : 'var(--red)'}`,
                                    borderRadius: 8,
                                    padding: '10px 14px',
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'space-between',
                                    gap: 8,
                                }}
                            >
                                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                                    <span style={{
                                        fontSize: 18,
                                        filter: `drop-shadow(0 0 4px ${isBuy ? 'var(--green)' : 'var(--red)'})`,
                                    }}>
                                        {isBuy ? '▲' : '▼'}
                                    </span>
                                    <div>
                                        <div style={{ fontWeight: 700, fontFamily: 'var(--font-mono)' }}>{t.symbol}</div>
                                        <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{time}</div>
                                    </div>
                                </div>
                                <div style={{ textAlign: 'right' }}>
                                    <span className={`badge ${isBuy ? 'badge-green' : 'badge-red'}`}>{t.direction}</span>
                                    <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4, fontFamily: 'var(--font-mono)' }}>
                                        {t.lot} lots @ {t.price?.toFixed(5)}
                                    </div>
                                </div>
                            </div>
                        )
                    })
                )}
            </div>
        </div>
    )
}

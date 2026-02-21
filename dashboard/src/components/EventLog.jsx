const TYPE_STYLE = {
    TRADE_EXECUTION: { cls: 'trade', icon: '⚡' },
    SCAN_START: { cls: 'scan', icon: '🔍' },
    SCAN_SUMMARY: { cls: 'scan', icon: '📊' },
    RESEARCH_START: { cls: 'info', icon: '🔬' },
    RESEARCH_RESULT: { cls: 'info', icon: '🧠' },
    POSITION_UPDATE: { cls: 'info', icon: '📂' },
    ACCOUNT_UPDATE: { cls: 'info', icon: '💰' },
    CONNECTED: { cls: 'scan', icon: '🟢' },
    DISCONNECTED: { cls: 'reject', icon: '🔴' },
}

function formatEvent(ev) {
    const t = ev.type || 'UNKNOWN'
    switch (t) {
        case 'TRADE_EXECUTION':
            return `TRADE ${ev.direction} ${ev.symbol} — ${ev.lot} lots @ ${ev.price?.toFixed(5)}`
        case 'SCAN_START':
            return `Scan started — ${ev.count} pairs`
        case 'SCAN_SUMMARY':
            return `Scan complete — ${ev.count} pairs`
        case 'RESEARCH_START':
            return `Research: ${ev.symbol}`
        case 'RESEARCH_RESULT':
            return `${ev.symbol}: ${ev.action} (${ev.confidence}% conf) — ${ev.reason}`
        case 'POSITION_UPDATE':
            return `Positions updated — ${(ev.positions || []).length} open`
        case 'ACCOUNT_UPDATE':
            return `Account: ${ev.account?.equity?.toFixed(2)} equity`
        default:
            return t
    }
}

export default function EventLog({ events }) {
    return (
        <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
            <div className="card-title">📋 Event Log</div>
            <div className="log-container" style={{ flex: 1 }}>
                {events.length === 0 ? (
                    <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: 16 }}>
                        Waiting for events…
                    </div>
                ) : (
                    events.slice(0, 100).map((ev, i) => {
                        const ts = ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : ''
                        const style = TYPE_STYLE[ev.type] || { cls: 'info', icon: '•' }
                        return (
                            <div key={i} className={`log-entry ${style.cls} slide-in`}>
                                <span style={{ opacity: .5, marginRight: 6 }}>{ts}</span>
                                <span style={{ marginRight: 6 }}>{style.icon}</span>
                                {formatEvent(ev)}
                            </div>
                        )
                    })
                )}
            </div>
        </div>
    )
}

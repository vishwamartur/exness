import { useState, useEffect } from 'react'

export default function TradingJournal() {
    const [stats, setStats] = useState({ total_profit: 0, win_rate: 0, total: 0, avg_rr: 0 })
    const [trades, setTrades] = useState([])
    const [confluence, setConfluence] = useState({})
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        let isMounted = true;

        async function fetchJournal() {
            try {
                const host = window.location.hostname || 'localhost';
                const apiBase = `http://${host}:8000/api/journal`;

                const [dRes, cRes, tRes] = await Promise.all([
                    fetch(`${apiBase}/daily`),
                    fetch(`${apiBase}/confluence`),
                    fetch(`${apiBase}/trades?limit=50`)
                ]);

                if (isMounted) {
                    setStats(await dRes.json());
                    setConfluence(await cRes.json());
                    setTrades(await tRes.json());
                    setLoading(false);
                }
            } catch (e) {
                console.error("Failed to fetch journal data:", e);
                // Fail silently, retry on next interval
            }
        }

        fetchJournal();
        const interval = setInterval(fetchJournal, 10000);
        return () => {
            isMounted = false;
            clearInterval(interval);
        };
    }, []);

    if (loading) {
        return <div style={{ padding: 20, color: 'var(--text-muted)' }}>Loading Analytics Engine...</div>
    }

    // Sort confluence factors by win rate
    const sortedConf = Object.keys(confluence)
        .filter(k => confluence[k].total >= 3) // filter noise
        .sort((a, b) => confluence[b].win_rate - confluence[a].win_rate);

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, height: '100%', overflow: 'hidden' }}>

            {/* Top Stats */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
                <StatCard title="Today's P/L" value={`$${stats.total_profit.toFixed(2)}`} color={stats.total_profit >= 0 ? 'var(--green)' : 'var(--red)'} />
                <StatCard title="Win Rate" value={`${stats.win_rate.toFixed(1)}%`} color="#fff" />
                <StatCard title="Trades Taken" value={stats.total} color="var(--blue)" />
                <StatCard title="Avg Reward/Risk" value={stats.avg_rr.toFixed(2)} color="var(--green)" />
            </div>

            <div style={{ display: 'flex', gap: 16, flex: 1, minHeight: 0 }}>
                {/* Edge Analysis */}
                <div className="card" style={{ flex: '0 0 35%', display: 'flex', flexDirection: 'column' }}>
                    <div style={{ padding: '16px 16px 0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-secondary)' }}>🎯 Edge Analysis</div>
                        <div style={{ fontSize: 10, background: 'rgba(30,41,59,0.5)', padding: '2px 6px', borderRadius: 4, color: 'var(--text-muted)' }}>Win Rate by Confluence</div>
                    </div>
                    <div style={{ padding: 16, overflowY: 'auto', flex: 1 }}>
                        {sortedConf.length === 0 ? (
                            <div style={{ textAlign: 'center', color: 'var(--text-muted)', fontSize: 12, marginTop: 40 }}>No edge data yet (requires 3+ trades)</div>
                        ) : (
                            sortedConf.map(factor => (
                                <ConfluenceBar key={factor} name={factor} data={confluence[factor]} />
                            ))
                        )}
                    </div>
                </div>

                {/* Trade History */}
                <div className="card" style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
                    <div style={{ padding: '16px', fontWeight: 600, fontSize: 13, color: 'var(--text-secondary)' }}>
                        📜 Trade History
                    </div>
                    <div style={{ flex: 1, overflowY: 'auto', padding: '0 16px 16px' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
                            <thead style={{ position: 'sticky', top: 0, background: 'var(--bg-card)', zIndex: 10, boxShadow: '0 4px 6px -4px rgba(0,0,0,0.3)' }}>
                                <tr>
                                    <Th>Time</Th>
                                    <Th>Symbol</Th>
                                    <Th>Dir</Th>
                                    <Th>Entry</Th>
                                    <Th>Exit</Th>
                                    <Th align="right">Profit</Th>
                                </tr>
                            </thead>
                            <tbody>
                                {trades.length === 0 ? (
                                    <tr><td colSpan={6} style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)', fontSize: 12 }}>No recent trades</td></tr>
                                ) : (
                                    trades.map(t => <TradeRow key={t.ticket} trade={t} />)
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

        </div>
    )
}

// ─── Subcomponents ───

function StatCard({ title, value, color }) {
    return (
        <div className="card" style={{ padding: 16 }}>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>{title}</div>
            <div style={{ fontSize: 24, fontWeight: 700, color }}>{value}</div>
        </div>
    )
}

function ConfluenceBar({ name, data }) {
    const isGood = data.win_rate >= 50
    const color = isGood ? 'var(--blue)' : 'var(--orange)'

    return (
        <div style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 6 }}>
                <span title={name} style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', paddingRight: 8 }}>
                    {name.replace(/_/g, ' ')}
                </span>
                <span style={{ fontSize: 11, fontWeight: 700, color }}>{data.win_rate.toFixed(1)}%</span>
            </div>
            <div style={{ height: 6, background: 'rgba(30,41,59,0.8)', borderRadius: 4, overflow: 'hidden', boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.2)' }}>
                <div style={{ height: '100%', background: color, width: `${data.win_rate}%`, transition: 'width 1s ease-out' }} />
            </div>
            <div style={{ fontSize: 10, color: 'var(--text-muted)', textAlign: 'right', marginTop: 4, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                {data.wins}W / {data.total}T
            </div>
        </div>
    )
}

function Th({ children, align = 'left' }) {
    return (
        <th style={{ padding: '8px 0 12px', fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', fontWeight: 600, letterSpacing: 0.5, borderBottom: '1px solid var(--border)', textAlign: align }}>
            {children}
        </th>
    )
}

function TradeRow({ trade }) {
    const time = new Date(trade.entry_time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    const isWin = trade.profit >= 0
    const isBuy = trade.direction === 'BUY'

    return (
        <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
            <td style={{ padding: '12px 0', fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>{time}</td>
            <td style={{ padding: '12px 0', fontSize: 13, fontWeight: 600 }}>{trade.symbol}</td>
            <td style={{ padding: '12px 0', fontSize: 11, fontWeight: 700, color: isBuy ? 'var(--green)' : 'var(--red)' }}>{trade.direction}</td>
            <td style={{ padding: '12px 0', fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>{trade.entry_price}</td>
            <td style={{ padding: '12px 0', fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>{trade.exit_price || '-'}</td>
            <td style={{ padding: '12px 0', fontSize: 11, fontFamily: 'var(--font-mono)', fontWeight: 700, textAlign: 'right', color: trade.profit !== null ? (isWin ? 'var(--green)' : 'var(--red)') : 'var(--text-muted)' }}>
                {trade.profit !== null ? `$${trade.profit.toFixed(2)}` : 'OPEN'}
            </td>
        </tr>
    )
}

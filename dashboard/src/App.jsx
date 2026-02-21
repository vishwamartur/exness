import { useState, useEffect } from 'react'
import { useBotWebSocket } from './hooks/useBotWebSocket'
import AccountCard from './components/AccountCard'
import ScannerGrid from './components/ScannerGrid'
import PositionsTable from './components/PositionsTable'
import TradeFeed from './components/TradeFeed'
import EventLog from './components/EventLog'

const VERSION = '2.2'

export default function App() {
    const state = useBotWebSocket()
    const [now, setNow] = useState(new Date())

    // Clock
    useEffect(() => {
        const t = setInterval(() => setNow(new Date()), 1000)
        return () => clearInterval(t)
    }, [])

    const utcTime = now.toUTCString().split(' ')[4] + ' UTC'

    return (
        <div style={{
            minHeight: '100vh',
            display: 'grid',
            gridTemplateRows: 'auto auto 1fr 1fr',
            gridTemplateColumns: '1fr 1fr 320px',
            gap: 12,
            padding: 12,
            maxHeight: '100vh',
            overflow: 'hidden',
        }}>

            {/* ── Header bar ── */}
            <header style={{
                gridColumn: '1 / -1',
                background: 'var(--bg-card)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-lg)',
                padding: '10px 18px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
            }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                    <span style={{ fontSize: 22 }}>📈</span>
                    <div>
                        <div style={{ fontWeight: 700, fontSize: 15 }}>MT5 Algo Trading Dashboard</div>
                        <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>Exness · M1 Scalping · v{VERSION}</div>
                    </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-secondary)' }}>
                        {utcTime}
                    </span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span className={`pulse ${state.connected ? '' : 'red'}`} />
                        <span style={{ fontSize: 12, color: state.connected ? 'var(--green)' : 'var(--red)' }}>
                            {state.connected ? 'Connected' : 'Reconnecting…'}
                        </span>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                        <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>Open Positions</div>
                        <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: state.positions.length > 0 ? 'var(--blue)' : 'var(--text-muted)' }}>
                            {state.positions.length} / 3
                        </div>
                    </div>
                </div>
            </header>

            {/* ── Account Overview ── */}
            <div style={{ gridColumn: '1 / -1' }}>
                <AccountCard account={state.account} connected={state.connected} />
            </div>

            {/* ── Scanner Grid ── */}
            <div style={{ gridColumn: '1 / 3', overflow: 'hidden' }}>
                <ScannerGrid scanSummary={state.scanSummary} lastScan={state.lastScan} />
            </div>

            {/* ── Event Log (right column, spans 2 rows) ── */}
            <div style={{ gridColumn: 3, gridRow: '3 / 5', overflow: 'hidden' }}>
                <EventLog events={state.events} />
            </div>

            {/* ── Open Positions ── */}
            <div style={{ overflow: 'hidden' }}>
                <PositionsTable positions={state.positions} />
            </div>

            {/* ── Trade Feed ── */}
            <div style={{ overflow: 'hidden' }}>
                <TradeFeed recentTrades={state.recentTrades} />
            </div>
        </div>
    )
}

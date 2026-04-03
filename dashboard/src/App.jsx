import { useState, useEffect } from 'react'
import { useBotWebSocket } from './hooks/useBotWebSocket'
import AccountCard from './components/AccountCard'
import ScannerGrid from './components/ScannerGrid'
import PositionsTable from './components/PositionsTable'
import TradeFeed from './components/TradeFeed'
import EventLog from './components/EventLog'
import ForexSessionClocks from './components/ForexSessionClocks'
import TradingJournal from './components/TradingJournal'
import OrderBook from './components/OrderBook'

const VERSION = '2.2'

export default function App() {
    const state = useBotWebSocket()
    const [now, setNow] = useState(new Date())
    const [activeTab, setActiveTab] = useState('live')

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
                <div style={{ display: 'flex', alignItems: 'center', gap: 32 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                        <span style={{ fontSize: 22 }}>📈</span>
                        <div>
                            <div style={{ fontWeight: 700, fontSize: 15 }}>MT5 Algo Trading Dashboard</div>
                            <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>Exness · M1 Scalping · v{VERSION}</div>
                        </div>
                    </div>

                    {/* ── Nav Tabs ── */}
                    <div style={{ display: 'flex', gap: 24, marginTop: 4 }}>
                        <button
                            onClick={() => setActiveTab('live')}
                            style={{
                                background: 'transparent', border: 'none', cursor: 'pointer',
                                fontSize: 13, fontWeight: 600, paddingBottom: 6,
                                color: activeTab === 'live' ? 'var(--blue)' : 'var(--text-secondary)',
                                borderBottom: activeTab === 'live' ? '2px solid var(--blue)' : '2px solid transparent',
                                transition: 'all 0.2s'
                            }}
                        >
                            Live Operations
                        </button>
                        <button
                            onClick={() => setActiveTab('journal')}
                            style={{
                                background: 'transparent', border: 'none', cursor: 'pointer',
                                fontSize: 13, fontWeight: 600, paddingBottom: 6,
                                color: activeTab === 'journal' ? 'var(--blue)' : 'var(--text-secondary)',
                                borderBottom: activeTab === 'journal' ? '2px solid var(--blue)' : '2px solid transparent',
                                transition: 'all 0.2s'
                            }}
                        >
                            Trading Journal
                        </button>
                        <button
                            onClick={() => setActiveTab('mirofish')}
                            style={{
                                background: 'transparent', border: 'none', cursor: 'pointer',
                                fontSize: 13, fontWeight: 600, paddingBottom: 6,
                                color: activeTab === 'mirofish' ? 'var(--blue)' : 'var(--text-secondary)',
                                borderBottom: activeTab === 'mirofish' ? '2px solid var(--blue)' : '2px solid transparent',
                                transition: 'all 0.2s'
                            }}
                        >
                            MiroFish Swarm AI
                        </button>
                    </div>
                </div>

                {/* ── Forex Session Clocks ── */}
                <ForexSessionClocks now={now} />

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

            {/* ── MAIN CONTENT AREA ── */}
            {activeTab === 'live' ? (
                <>
                    {/* ── Account Overview ── */}
                    <div style={{ gridColumn: '1 / -1' }}>
                        <AccountCard account={state.account} connected={state.connected} />
                    </div>

                    {/* ── Scanner Grid ── */}
                    <div style={{ gridColumn: '1 / 3', overflow: 'hidden' }}>
                        <ScannerGrid scanSummary={state.scanSummary} lastScan={state.lastScan} />
                    </div>

                    {/* ── Event Log (right column, top half) ── */}
                    <div style={{ gridColumn: 3, gridRow: 3, overflow: 'hidden' }}>
                        <EventLog events={state.events} />
                    </div>

                    {/* ── OrderBook (right column, bottom half) ── */}
                    <div style={{ gridColumn: 3, gridRow: 4, overflow: 'hidden' }}>
                        <OrderBook symbol="XAUUSDm" />
                    </div>

                    {/* ── Open Positions ── */}
                    <div style={{ overflow: 'hidden' }}>
                        <PositionsTable positions={state.positions} />
                    </div>

                    {/* ── Trade Feed ── */}
                    <div style={{ overflow: 'hidden' }}>
                        <TradeFeed recentTrades={state.recentTrades} />
                    </div>
                </>
            ) : activeTab === 'journal' ? (
                <div style={{ gridColumn: '1 / -1', gridRow: '2 / 5', overflow: 'hidden' }}>
                    <TradingJournal />
                </div>
            ) : (
                <div style={{ gridColumn: '1 / -1', gridRow: '2 / 5', overflow: 'hidden', background: '#fff', borderRadius: 'var(--radius-lg)' }}>
                    <iframe 
                        src="http://localhost:3000" 
                        style={{ width: '100%', height: '100%', border: 'none' }}
                        title="MiroFish Dashboard"
                    />
                </div>
            )}
        </div>
    )
}

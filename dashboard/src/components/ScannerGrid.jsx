import { useState, useEffect } from 'react'

const STATUS_COLORS = {
    candidate: { bg: '#14532d', border: 'var(--green)', text: 'var(--green)', label: 'SIGNAL' },
    off_session: { bg: '#1e1b4b', border: '#6366f1', text: '#818cf8', label: 'OFF-SESSION' },
    low_vol: { bg: '#1c1012', border: '#f43f5e', text: '#fb7185', label: 'LOW VOL' },
    spread: { bg: '#1c1206', border: 'var(--yellow)', text: 'var(--yellow)', label: 'SPREAD' },
    news: { bg: '#1c1206', border: 'var(--yellow)', text: 'var(--yellow)', label: 'NEWS' },
    exec_block: { bg: '#1c0a0a', border: 'var(--red)', text: 'var(--red)', label: 'BLOCKED' },
    skipped: { bg: 'var(--bg-card2)', border: 'var(--border)', text: 'var(--text-muted)', label: 'SKIP' },
    ok: { bg: 'var(--bg-card2)', border: 'var(--border)', text: 'var(--text-secondary)', label: 'OK' },
}

function getStatusType(reason) {
    if (!reason) return 'ok'
    const r = reason.toLowerCase()
    if (r === 'ok') return 'ok'
    if (r.startsWith('buy') || r.startsWith('sell') || r === 'candidate') return 'candidate'
    if (r.includes('session')) return 'off_session'
    if (r.includes('volatil') || r.includes('atr')) return 'low_vol'
    if (r.includes('spread')) return 'spread'
    if (r.includes('news') || r.includes('ff:')) return 'news'
    if (r.includes('exec block') || r.includes('corr') || r.includes('max concurrent')) return 'exec_block'
    return 'skipped'
}

function SymbolCard({ symbol, reason, isNew }) {
    const stype = getStatusType(reason)
    const style = STATUS_COLORS[stype] || STATUS_COLORS.skipped

    return (
        <div
            className={isNew ? 'fade-in' : ''}
            style={{
                background: style.bg,
                border: `1px solid ${style.border}`,
                borderRadius: 8,
                padding: '10px 12px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 8,
                transition: 'border-color .3s',
            }}
        >
            <div>
                <div style={{ fontWeight: 600, fontSize: 13, fontFamily: 'var(--font-mono)' }}>{symbol}</div>
                <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2, maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {reason || '—'}
                </div>
            </div>
            <span
                style={{
                    fontSize: 9,
                    fontWeight: 700,
                    letterSpacing: '.06em',
                    color: style.text,
                    background: `${style.border}22`,
                    padding: '2px 6px',
                    borderRadius: 999,
                    flexShrink: 0,
                }}
            >
                {style.label}
            </span>
        </div>
    )
}

export default function ScannerGrid({ scanSummary, lastScan }) {
    const [prevSymbols, setPrevSymbols] = useState({})

    useEffect(() => {
        if (scanSummary?.symbols) setPrevSymbols(scanSummary.symbols)
    }, [scanSummary])

    const symbols = scanSummary?.symbols || {}
    const entries = Object.entries(symbols).sort(([, a], [, b]) => {
        // Candidates first
        const aIsCandidate = getStatusType(a) === 'candidate'
        const bIsCandidate = getStatusType(b) === 'candidate'
        return bIsCandidate - aIsCandidate
    })

    const scanTime = lastScan ? new Date(lastScan).toLocaleTimeString() : '—'
    const candidates = entries.filter(([, r]) => getStatusType(r) === 'candidate').length

    return (
        <div className="card" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            <div className="card-title" style={{ justifyContent: 'space-between' }}>
                <span>📡 Live Scanner — {entries.length} pairs</span>
                <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
                    {candidates > 0 && <span style={{ color: 'var(--green)', marginRight: 8 }}>●  {candidates} signal{candidates > 1 ? 's' : ''}</span>}
                    Last: {scanTime}
                </span>
            </div>
            {entries.length === 0 ? (
                <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: 32 }}>
                    Waiting for first scan…
                </div>
            ) : (
                <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
                    gap: 8,
                    overflowY: 'auto',
                    flex: 1,
                }}>
                    {entries.map(([sym, reason]) => (
                        <SymbolCard
                            key={sym}
                            symbol={sym}
                            reason={reason}
                            isNew={!(sym in prevSymbols)}
                        />
                    ))}
                </div>
            )}
        </div>
    )
}

import { useState, useEffect } from 'react'

const REST_URL = 'http://localhost:8000'

// Generate synthetic DOM levels based on live price
function generateLevels(basePrice, isAsk, spreadPip = 0.5) {
    const levels = []
    let currentPrice = basePrice
    
    // Create 8 visual levels
    for (let i = 0; i < 8; i++) {
        // Price increments by a small amount
        const step = (Math.random() * 0.3 + 0.1) * spreadPip
        currentPrice = isAsk ? currentPrice + step : currentPrice - step
        
        // Volume logic: higher volume clustered near mid price
        const volumeScore = Math.max(1, 10 - i * 1.2 + (Math.random() * 4 - 2))
        const volume = (volumeScore * 10).toFixed(2)
        const totalVolume = (volumeScore * 30 + Math.random() * 50).toFixed(0) // Random larger pseudo-total
        
        levels.push({
            price: currentPrice.toFixed(2),
            volume: volume,
            total: totalVolume,
            width: `${Math.min(100, Math.max(5, volumeScore * 8))}%`
        })
    }
    
    return isAsk ? levels.reverse() : levels
}

export default function OrderBook({ symbol = "XAUUSDm" }) {
    const [quote, setQuote] = useState({ bid: null, ask: null })
    const [asks, setAsks] = useState([])
    const [bids, setBids] = useState([])

    useEffect(() => {
        const fetchQuote = async () => {
            try {
                const res = await fetch(`${REST_URL}/api/quote?symbol=${symbol}`)
                if (!res.ok) return
                const data = await res.json()
                
                if (data.bid && data.ask) {
                    setQuote({ bid: data.bid, ask: data.ask })
                    setAsks(generateLevels(data.ask, true))
                    setBids(generateLevels(data.bid, false))
                }
            } catch (err) {
                // Ignore silent failure
            }
        }
        
        fetchQuote()
        const t = setInterval(fetchQuote, 1500) // Update every 1.5s
        return () => clearInterval(t)
    }, [symbol])

    const spread = quote.ask && quote.bid ? (quote.ask - quote.bid).toFixed(2) : '--'

    return (
        <div className="card" style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
            <div className="card-title" style={{ justifyContent: 'space-between' }}>
                <span>📊 DOM: {symbol}</span>
                <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>SYNTHETIC L2</span>
            </div>
            
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                {/* Header */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', color: 'var(--text-muted)', marginBottom: 6, padding: '0 8px' }}>
                    <div>Price</div>
                    <div style={{ textAlign: 'right' }}>Size</div>
                    <div style={{ textAlign: 'right' }}>Total</div>
                </div>

                {/* Asks (Red) */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {asks.map((lvl, i) => (
                        <div key={`ask-${i}`} style={{ position: 'relative', display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', padding: '1px 8px', zIndex: 1 }}>
                            {/* Width Bar */}
                            <div style={{ position: 'absolute', top: 0, right: 0, bottom: 0, width: lvl.width, background: 'rgba(244, 63, 94, 0.15)', zIndex: -1, transition: 'width 0.4s ease' }} />
                            <div style={{ color: 'var(--red)' }}>{lvl.price}</div>
                            <div style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{lvl.volume}</div>
                            <div style={{ textAlign: 'right', color: 'var(--text-muted)' }}>{lvl.total}</div>
                        </div>
                    ))}
                </div>

                {/* Spread / Mid Market */}
                <div style={{ margin: '8px 0', borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)', padding: '6px 8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--bg-card2)' }}>
                    <span style={{ color: 'var(--text-muted)', fontSize: 10 }}>Spread: <span style={{ color: 'var(--text-secondary)' }}>{spread}</span></span>
                    <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
                        {quote.bid ? quote.bid.toFixed(2) : '---.--'}
                    </span>
                    <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--red)' }}>
                        {quote.ask ? quote.ask.toFixed(2) : '---.--'}
                    </span>
                </div>

                {/* Bids (Green) */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {bids.map((lvl, i) => (
                        <div key={`bid-${i}`} style={{ position: 'relative', display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', padding: '1px 8px', zIndex: 1 }}>
                            {/* Width Bar */}
                            <div style={{ position: 'absolute', top: 0, right: 0, bottom: 0, width: lvl.width, background: 'rgba(34, 197, 94, 0.15)', zIndex: -1, transition: 'width 0.4s ease' }} />
                            <div style={{ color: 'var(--green)' }}>{lvl.price}</div>
                            <div style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>{lvl.volume}</div>
                            <div style={{ textAlign: 'right', color: 'var(--text-muted)' }}>{lvl.total}</div>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    )
}

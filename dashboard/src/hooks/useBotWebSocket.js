import { useEffect, useReducer, useRef, useCallback } from 'react'

const WS_URL = 'ws://localhost:8000/ws'
const REST_URL = 'http://localhost:8000'
const POLL_MS = 5000   // poll positions + account every 5s

const initialState = {
    connected: false,
    account: {},
    positions: [],
    scanSummary: {},
    recentTrades: [],
    events: [],
    lastScan: null,
}

function reducer(state, action) {
    switch (action.type) {
        case 'CONNECTED':
            return { ...state, connected: true }
        case 'DISCONNECTED':
            return { ...state, connected: false }

        case 'STATE_SNAPSHOT': {
            const d = action.data.data || {}
            return {
                ...state,
                account: d.account || state.account,
                positions: d.positions || state.positions,
                scanSummary: d.scan_summary || state.scanSummary,
                recentTrades: d.recent_trades || state.recentTrades,
                events: d.events || state.events,
            }
        }

        case 'REST_POLL': {
            // Merge REST data without clobbering WS events
            return {
                ...state,
                positions: action.positions ?? state.positions,
                account: action.account ?? state.account,
                recentTrades: action.trades ?? state.recentTrades,
            }
        }

        case 'ACCOUNT_UPDATE':
            return { ...state, account: { ...state.account, ...(action.data.account || {}) } }

        case 'POSITION_UPDATE':
            return { ...state, positions: action.data.positions || [] }

        case 'SCAN_START':
            return {
                ...state,
                lastScan: action.data.timestamp,
                events: [action.data, ...state.events].slice(0, 200),
            }

        case 'SCAN_SUMMARY':
            return {
                ...state,
                scanSummary: {
                    symbols: action.data.symbols || {},
                    timestamp: action.data.timestamp,
                    count: action.data.count || 0,
                },
                events: [action.data, ...state.events].slice(0, 200),
            }

        case 'RESEARCH_START':
        case 'RESEARCH_RESULT':
            return { ...state, events: [action.data, ...state.events].slice(0, 200) }

        case 'TRADE_EXECUTION': {
            return {
                ...state,
                recentTrades: [action.data, ...state.recentTrades].slice(0, 50),
                events: [action.data, ...state.events].slice(0, 200),
            }
        }

        default:
            return { ...state, events: [action.data, ...state.events].slice(0, 200) }
    }
}

async function fetchRest(dispatch) {
    try {
        const [posRes, acctRes, tradeRes] = await Promise.all([
            fetch(`${REST_URL}/api/positions`).then(r => r.ok ? r.json() : null),
            fetch(`${REST_URL}/api/account`).then(r => r.ok ? r.json() : null),
            fetch(`${REST_URL}/api/trades`).then(r => r.ok ? r.json() : null),
        ])
        dispatch({
            type: 'REST_POLL',
            positions: Array.isArray(posRes) ? posRes : null,
            account: acctRes && typeof acctRes === 'object' && !Array.isArray(acctRes) ? acctRes : null,
            trades: Array.isArray(tradeRes) ? tradeRes : null,
        })
    } catch {
        // server not up yet — silent
    }
}

export function useBotWebSocket() {
    const [state, dispatch] = useReducer(reducer, initialState)
    const wsRef = useRef(null)
    const reconnectTimer = useRef(null)
    const pollTimer = useRef(null)

    // ── REST Polling ────────────────────────────────────────────────────────
    const startPolling = useCallback(() => {
        fetchRest(dispatch)                   // immediate first fetch
        pollTimer.current = setInterval(() => fetchRest(dispatch), POLL_MS)
    }, [])

    // ── WebSocket ───────────────────────────────────────────────────────────
    const connect = useCallback(() => {
        if (wsRef.current && wsRef.current.readyState <= 1) return

        const ws = new WebSocket(WS_URL)
        wsRef.current = ws

        ws.onopen = () => {
            dispatch({ type: 'CONNECTED', data: {} })
            if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
        }

        ws.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data)
                dispatch({ type: data.type || 'UNKNOWN', data })
            } catch { /* ignore */ }
        }

        ws.onclose = () => {
            dispatch({ type: 'DISCONNECTED', data: {} })
            reconnectTimer.current = setTimeout(connect, 3000)
        }

        ws.onerror = () => ws.close()
    }, [])

    useEffect(() => {
        connect()
        startPolling()
        return () => {
            if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
            if (pollTimer.current) clearInterval(pollTimer.current)
            wsRef.current?.close()
        }
    }, [connect, startPolling])

    return state
}

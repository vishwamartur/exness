import { useCallback, useEffect, useReducer, useRef } from 'react'
import { resolveServiceEndpoint } from '../lib/serviceEndpoint'

const POLL_MS = 5000

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
            const snapshot = action.data.data || {}
            return {
                ...state,
                account: snapshot.account || state.account,
                positions: snapshot.positions || state.positions,
                scanSummary: snapshot.scan_summary || state.scanSummary,
                recentTrades: snapshot.recent_trades || state.recentTrades,
                events: snapshot.events || state.events,
            }
        }

        case 'REST_POLL':
            return {
                ...state,
                positions: action.positions ?? state.positions,
                account: action.account ?? state.account,
                recentTrades: action.trades ?? state.recentTrades,
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

        case 'TRADE_EXECUTION':
            return {
                ...state,
                recentTrades: [action.data, ...state.recentTrades].slice(0, 50),
                events: [action.data, ...state.events].slice(0, 200),
            }

        default:
            return { ...state, events: [action.data, ...state.events].slice(0, 200) }
    }
}

async function fetchRest(dispatch, apiBase) {
    try {
        const [positionsResponse, accountResponse, tradesResponse] = await Promise.all([
            fetch(`${apiBase}/api/positions`).then(response => response.ok ? response.json() : null),
            fetch(`${apiBase}/api/account`).then(response => response.ok ? response.json() : null),
            fetch(`${apiBase}/api/trades`).then(response => response.ok ? response.json() : null),
        ])

        dispatch({
            type: 'REST_POLL',
            positions: Array.isArray(positionsResponse) ? positionsResponse : null,
            account: accountResponse && typeof accountResponse === 'object' && !Array.isArray(accountResponse) ? accountResponse : null,
            trades: Array.isArray(tradesResponse) ? tradesResponse : null,
        })
    } catch {
        // The API may not be ready yet.
    }
}

export function useBotWebSocket() {
    const [state, dispatch] = useReducer(reducer, initialState)
    const wsRef = useRef(null)
    const reconnectTimer = useRef(null)
    const pollTimer = useRef(null)
    const endpointRef = useRef(null)

    const startPolling = useCallback(() => {
        if (!endpointRef.current) return
        fetchRest(dispatch, endpointRef.current.apiBase)
        pollTimer.current = setInterval(() => fetchRest(dispatch, endpointRef.current.apiBase), POLL_MS)
    }, [])

    const connect = useCallback(() => {
        resolveServiceEndpoint().then(endpoint => {
            endpointRef.current = endpoint
            if (wsRef.current && wsRef.current.readyState <= 1) return

            const ws = new WebSocket(endpoint.wsUrl)
            wsRef.current = ws

            ws.onopen = () => {
                dispatch({ type: 'CONNECTED', data: {} })
                if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
            }

            ws.onmessage = event => {
                try {
                    const data = JSON.parse(event.data)
                    dispatch({ type: data.type || 'UNKNOWN', data })
                } catch {
                    // Ignore malformed payloads.
                }
            }

            ws.onclose = () => {
                dispatch({ type: 'DISCONNECTED', data: {} })
                reconnectTimer.current = setTimeout(connect, 3000)
            }

            ws.onerror = () => ws.close()
        }).catch(() => {
            reconnectTimer.current = setTimeout(connect, 3000)
        })
    }, [])

    useEffect(() => {
        resolveServiceEndpoint().then(endpoint => {
            endpointRef.current = endpoint
            connect()
            startPolling()
        }).catch(() => {
            connect()
        })

        return () => {
            if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
            if (pollTimer.current) clearInterval(pollTimer.current)
            wsRef.current?.close()
        }
    }, [connect, startPolling])

    return state
}

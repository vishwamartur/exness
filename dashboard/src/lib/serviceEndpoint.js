const HOST = window.location.hostname || 'localhost'
const PORTS = Array.from({ length: 10 }, (_, index) => 8000 + index)

let endpointPromise = null

async function probePort(port) {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 700)

    try {
        const response = await fetch(`http://${HOST}:${port}/`, {
            cache: 'no-store',
            signal: controller.signal,
        })

        if (!response.ok) {
            return null
        }

        const payload = await response.json().catch(() => null)
        if (!payload || payload.service !== 'MT5 Stream Server') {
            return null
        }

        return {
            apiBase: `http://${HOST}:${port}`,
            wsUrl: `ws://${HOST}:${port}/ws`,
        }
    } catch {
        return null
    } finally {
        clearTimeout(timeout)
    }
}

export async function resolveServiceEndpoint() {
    if (endpointPromise) {
        return endpointPromise
    }

    endpointPromise = (async () => {
        for (const port of PORTS) {
            const match = await probePort(port)
            if (match) {
                return match
            }
        }

        return {
            apiBase: `http://${HOST}:8000`,
            wsUrl: `ws://${HOST}:8000/ws`,
        }
    })()

    return endpointPromise
}

/**
 * ForexSessionClocks
 * Displays the 4 major Forex market sessions with live status:
 *   Sydney   | Tokyo | London | New York
 * Each clock shows:
 *   - Current local time in that city
 *   - OPEN (green) / CLOSED (dim) / PRE-OPEN (amber) badge
 *   - Countdown: "closes in Xh Xm" or "opens in Xh Xm"
 *
 * All session hours are in UTC to stay DST-safe on the server clock.
 */

const SESSIONS = [
    {
        name: 'Sydney',
        flag: '🇦🇺',
        tz: 'Australia/Sydney',
        // UTC hours [open, close) – standard time. Covers DST roughly.
        // Sydney opens ~22:00 UTC (winter) or 21:00 UTC (summer) and closes ~06:00 / 07:00 UTC.
        // We use the conservative universal window: 21:00 – 06:00 UTC
        openUTC: 21,
        closeUTC: 6,   // next day
        accent: '#60a5fa',   // blue
    },
    {
        name: 'Tokyo',
        flag: '🇯🇵',
        tz: 'Asia/Tokyo',
        // Tokyo: 00:00 – 09:00 UTC
        openUTC: 0,
        closeUTC: 9,
        accent: '#f87171',   // red
    },
    {
        name: 'London',
        flag: '🇬🇧',
        tz: 'Europe/London',
        // London: 07:00 – 16:00 UTC (standard); 06:00 – 15:00 (BST) – use 07:00–16:00 as nominal
        openUTC: 7,
        closeUTC: 16,
        accent: '#a78bfa',   // purple
    },
    {
        name: 'New York',
        flag: '🇺🇸',
        tz: 'America/New_York',
        // New York: 12:00 – 21:00 UTC (EST); 13:00 – 22:00 UTC (EDT) – use 12:00–21:00 nominal
        openUTC: 12,
        closeUTC: 21,
        accent: '#34d399',   // green
    },
]

/**
 * Returns true when the current UTC hour is within the session window.
 * Handles overnight sessions (open > close, e.g. Sydney).
 */
function isOpen(nowUTC, openUTC, closeUTC) {
    const h = nowUTC.getUTCHours() + nowUTC.getUTCMinutes() / 60
    if (openUTC < closeUTC) {
        return h >= openUTC && h < closeUTC
    } else {
        // overnight window, e.g. 21-06 → open if h >= 21 OR h < 6
        return h >= openUTC || h < closeUTC
    }
}

/**
 * Returns seconds until the next open OR next close of the session.
 */
function secsUntilNext(nowUTC, targetHourUTC) {
    const nowTotalSecs = nowUTC.getUTCHours() * 3600 + nowUTC.getUTCMinutes() * 60 + nowUTC.getUTCSeconds()
    const targetSecs = targetHourUTC * 3600
    let diff = targetSecs - nowTotalSecs
    if (diff < 0) diff += 86400
    return diff
}

function formatCountdown(secs) {
    if (secs <= 0) return '—'
    const h = Math.floor(secs / 3600)
    const m = Math.floor((secs % 3600) / 60)
    if (h > 0) return `${h}h ${m}m`
    return `${m}m`
}

function localTimeInZone(date, tz) {
    return date.toLocaleTimeString('en-US', {
        timeZone: tz,
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
    })
}

export default function ForexSessionClocks({ now }) {
    if (!now) return null

    const cards = SESSIONS.map((s) => {
        const open = isOpen(now, s.openUTC, s.closeUTC)
        const secsLeft = open
            ? secsUntilNext(now, s.closeUTC)
            : secsUntilNext(now, s.openUTC)

        // Pre-open: within 30 min of opening
        const preOpen = !open && secsLeft <= 1800

        let badge, badgeColor, countLabel
        if (open) {
            badge = 'OPEN'
            badgeColor = '#10b981'
            countLabel = `closes ${formatCountdown(secsLeft)}`
        } else if (preOpen) {
            badge = 'PRE'
            badgeColor = '#f59e0b'
            countLabel = `opens ${formatCountdown(secsLeft)}`
        } else {
            badge = 'CLOSED'
            badgeColor = '#475569'
            countLabel = `opens ${formatCountdown(secsLeft)}`
        }

        return { ...s, open, preOpen, badge, badgeColor, countLabel }
    })

    return (
        <div style={{
            display: 'flex',
            gap: 8,
            alignItems: 'stretch',
        }}>
            {cards.map((s) => (
                <div key={s.name} style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    gap: 2,
                    padding: '4px 10px',
                    borderRadius: 8,
                    background: s.open
                        ? `${s.accent}18`
                        : 'rgba(30,41,59,0.5)',
                    border: `1px solid ${s.open ? s.accent + '55' : 'rgba(71,85,105,0.4)'}`,
                    minWidth: 78,
                    position: 'relative',
                    transition: 'all 0.4s ease',
                }}>
                    {/* Glowing dot for open sessions */}
                    {s.open && (
                        <span style={{
                            position: 'absolute',
                            top: 5,
                            right: 7,
                            width: 6,
                            height: 6,
                            borderRadius: '50%',
                            background: s.accent,
                            boxShadow: `0 0 6px ${s.accent}`,
                            animation: 'pulse-dot 1.5s ease-in-out infinite',
                        }} />
                    )}

                    {/* Flag + Name */}
                    <div style={{ fontSize: 10, color: s.open ? '#e2e8f0' : '#64748b', fontWeight: 600, display: 'flex', gap: 4, alignItems: 'center' }}>
                        <span>{s.flag}</span>
                        <span>{s.name}</span>
                    </div>

                    {/* Local time */}
                    <div style={{
                        fontFamily: 'var(--font-mono, monospace)',
                        fontSize: 13,
                        fontWeight: 700,
                        color: s.open ? s.accent : '#475569',
                        letterSpacing: 1,
                    }}>
                        {localTimeInZone(now, s.tz)}
                    </div>

                    {/* Badge */}
                    <div style={{
                        fontSize: 9,
                        fontWeight: 700,
                        letterSpacing: 1,
                        padding: '1px 5px',
                        borderRadius: 4,
                        background: `${s.badgeColor}25`,
                        color: s.badgeColor,
                        border: `1px solid ${s.badgeColor}60`,
                    }}>
                        {s.badge}
                    </div>

                    {/* Countdown */}
                    <div style={{
                        fontSize: 9,
                        color: s.preOpen ? '#f59e0b' : (s.open ? '#94a3b8' : '#334155'),
                        fontFamily: 'var(--font-mono, monospace)',
                    }}>
                        {s.countLabel}
                    </div>
                </div>
            ))}
        </div>
    )
}

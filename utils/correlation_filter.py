"""
Correlation Filter — Prevents opening positions on highly correlated pairs.

Major correlation groups (80%+ correlated):
- EUR group: EURUSD, EURGBP, EURJPY, etc.
- USD index: USDJPY, USDCHF, USDCAD
- Risk-on: AUDUSD, NZDUSD, AUDJPY, NZDJPY
- Metals: XAUUSD, XAGUSD

If a position is already open on EURUSD, we won't open on EURGBP
in the same direction (reduces correlated losses).
"""

# Correlation groups — symbols within same group are highly correlated
# Direction: +1 means positively correlated, -1 means inversely correlated
CORRELATION_GROUPS = {
    'eur_usd': {
        'symbols': ['EURUSD', 'EURGBP', 'EURJPY', 'EURAUD', 'EURCAD', 'EURCHF', 'EURNZD'],
        'inverse': ['USDCHF', 'USDCAD'],
    },
    'gbp_usd': {
        'symbols': ['GBPUSD', 'GBPJPY', 'GBPAUD', 'GBPCAD', 'GBPCHF', 'GBPNZD'],
        'inverse': [],
    },
    'risk_on': {
        'symbols': ['AUDUSD', 'NZDUSD', 'AUDJPY', 'NZDJPY', 'AUDCAD', 'AUDNZD'],
        'inverse': ['USDCAD', 'USDJPY'],
    },
    'jpy_safe_haven': {
        'symbols': ['USDJPY', 'EURJPY', 'GBPJPY', 'AUDJPY', 'NZDJPY', 'CADJPY', 'CHFJPY'],
        'inverse': [],
    },
    'metals': {
        'symbols': ['XAUUSD', 'XAGUSD'],
        'inverse': ['USDJPY', 'USDCHF'],
    },
    'crypto': {
        'symbols': ['BTCUSD', 'ETHUSD', 'LTCUSD', 'XRPUSD', 'BCHUSD'],
        'inverse': [],
    },
    'energy': {
        'symbols': ['USOIL', 'UKOIL'],
        'inverse': [],
    },
}


def _strip_suffix(symbol):
    """Strips Exness suffixes (m, c) from symbol name for matching."""
    for suffix in ['m', 'c']:
        if symbol.endswith(suffix) and len(symbol) > 3:
            # Make sure we're not stripping part of the actual name
            base = symbol[:-len(suffix)]
            if len(base) >= 6:  # Minimum forex pair length
                return base
    return symbol


def get_correlated_symbols(symbol):
    """Returns all symbols correlated with the given symbol."""
    base = _strip_suffix(symbol)
    correlated = set()

    for group_name, group in CORRELATION_GROUPS.items():
        all_in_group = group['symbols'] + group.get('inverse', [])
        if base in all_in_group:
            for s in group['symbols']:
                correlated.add(s)
            for s in group.get('inverse', []):
                correlated.add(s)

    # Remove the symbol itself
    correlated.discard(base)
    return correlated


def check_correlation_conflict(candidate_symbol, candidate_direction, open_positions):
    """
    Checks if opening a position would conflict with existing correlated positions.

    Returns:
        (bool, str): (has_conflict, reason)
    """
    if not open_positions:
        return False, ""

    base_candidate = _strip_suffix(candidate_symbol)
    correlated = get_correlated_symbols(candidate_symbol)

    if not correlated:
        return False, ""

    for pos in open_positions:
        pos_symbol = _strip_suffix(pos.symbol if hasattr(pos, 'symbol') else str(pos))
        pos_direction = 'BUY' if (hasattr(pos, 'type') and pos.type == 0) else 'SELL'

        if pos_symbol in correlated:
            # Check if it's a direct correlation (same direction = conflict)
            # or inverse correlation (opposite direction = conflict)
            is_inverse = False
            for group in CORRELATION_GROUPS.values():
                if (base_candidate in group['symbols'] and pos_symbol in group.get('inverse', [])) or \
                   (pos_symbol in group['symbols'] and base_candidate in group.get('inverse', [])):
                    is_inverse = True
                    break

            if is_inverse:
                # Inversely correlated: conflict if OPPOSITE directions
                if candidate_direction != pos_direction:
                    return True, f"Correlated (inverse) with open {pos_direction} {pos_symbol}"
            else:
                # Directly correlated: conflict if SAME direction
                if candidate_direction == pos_direction:
                    return True, f"Correlated with open {pos_direction} {pos_symbol}"

    return False, ""

"""Pure scoring functions: raw responses in, score dicts out.

No module in this package touches the database. That is deliberate (plan §7) —
it is what makes every scorer testable offline and every historical session
rescoreable in bulk (R1).
"""

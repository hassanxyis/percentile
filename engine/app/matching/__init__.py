"""Occupation matching: scored profiles in, ranked occupations out.

No module in this package touches the database, for the same reason the scoring
package does not (plan §7, §8) — matching must be testable offline and every
historical session must be re-matchable in bulk when the rules change (R1).
`scripts/load_onet.py` owns reading the O*NET catalogue into `occupations`;
this package only consumes rows that have already been loaded.
"""

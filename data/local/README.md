# The Pakistan localisation layer

`pk_occupation_map.csv` maps O*NET occupations onto titles and education pathways a Pakistani
student and their parents will recognise. `plan.md` §6.4 calls it the moat: it is the part of the
product that a US career quiz cannot copy, and it is hand-curated, not generated.

**The committed file is a 10-row stub, not the real thing.** It exists so `scripts/load_onet.py`
and the matching tests have something to run against. `plan.md` targets **~120 rows** and budgets
"two weeks of evenings for the first pass, revised after every pilot" — that work is yours, and
these ten rows are only a format example.

## Columns

| Column | Notes |
|---|---|
| `onet_soc_code` | Must exist in `data/onet/<release>/occupation_data.csv` **and** have a Job Zone. `load_onet.py` fails the load on an unknown code rather than skipping it — a typo here silently loses a mapping otherwise. |
| `pk_title` | What the occupation is actually called locally. |
| `pk_pathway` | The concrete education route, e.g. `"FSc Pre-Engineering → BE/BS Mechanical"`. |
| `pk_relevant` | `true` puts it in the report's local section, which is shown before international matches (§8). |
| `entrepreneurial_track` | `true` makes it eligible for the 3-slot carve-out shown when GET2 sets `entrepreneurial_flag` (§7.3, §8). Keep this to genuine ownership/self-employment routes — small business owner, franchise operator, tech founder, agri-business — not merely senior salaried roles. |

## Growing it

When a student profile returns fewer than 5 local matches, `match()` sets `local_match_gap` and
logs it. Those logs are the to-do list: they name exactly which profile shapes have no local
coverage yet. Review them monthly (§8).

After editing this file, re-apply it with:

```powershell
cd engine
.\.venv\Scripts\python.exe scripts\load_onet.py --dry-run   # validates codes, writes nothing
.\.venv\Scripts\python.exe scripts\load_onet.py             # applies to the database
```

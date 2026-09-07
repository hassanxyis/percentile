"""Report rendering (plan §14, M9).

    student.py   what appears on the page — pure, no I/O
    charts.py    hand-written SVG — pure, no I/O
    render.py    Jinja2 -> HTML -> WeasyPrint -> PDF bytes
    templates/   the pages themselves

The database half lives in `app/repository.py` (loading) and
`app/jobs/handlers/render.py` (storage, the `reports` row, the email), following
the same split as scoring: the modules that decide what a student reads touch
nothing, so they can be tested without a Postgres or a font stack.
"""

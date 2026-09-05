"""The job runner (plan §11, §12, M7).

`queue.py` wraps the SQL functions in 0008_job_runner.sql, `runner.py` drives one
tick, and `handlers/` holds one function per job kind.
"""

#!/usr/bin/env bash
#
# The Supabase service-role key bypasses row-level security. It is legitimate in
# the engine and in server-side Next.js code; it is a full database breach in a
# client component or a NEXT_PUBLIC_ variable (plan §4, §17.3).
#
# Two checks, both cheap enough to run on every push:
#   1. No NEXT_PUBLIC_ variable may carry the key.
#   2. No file marked "use client" may reference it.
#
# Run from the repository root.

set -euo pipefail

fail=0

report() {
  echo "::error::$1"
  fail=1
}

# ── 1. NEXT_PUBLIC_ must never carry the service-role key ────────────────────
# Matches NEXT_PUBLIC_…SERVICE_ROLE… in either order, anywhere in web/ or db/.
if matches=$(grep -rniE \
      -e 'NEXT_PUBLIC_[A-Z0-9_]*SERVICE_ROLE' \
      -e 'SERVICE_ROLE[A-Z0-9_]*.*NEXT_PUBLIC_' \
      --include='*.ts' --include='*.tsx' --include='*.js' --include='*.jsx' \
      --include='*.mjs' --include='*.env*' \
      web/ 2>/dev/null); then
  report "Service-role key exposed through a NEXT_PUBLIC_ variable:"
  echo "$matches"
fi

# ── 2. No "use client" file may reference the key ────────────────────────────
# Collect candidates first, then check each one's directive. A file is a client
# component only if "use client" is its first non-comment, non-blank line.
while IFS= read -r file; do
  [ -n "$file" ] || continue

  first_line=$(grep -vE '^\s*(//|/\*|\*|$)' "$file" | head -n 1 || true)
  case "$first_line" in
    *'use client'*)
      report "Service-role key referenced in a client component: $file"
      grep -nE 'SERVICE_ROLE' "$file"
      ;;
  esac
done < <(grep -rlE 'SERVICE_ROLE' \
           --include='*.ts' --include='*.tsx' --include='*.js' --include='*.jsx' \
           web/ 2>/dev/null || true)

if [ "$fail" -eq 0 ]; then
  echo "OK: service-role key is confined to server-side code."
fi

exit "$fail"

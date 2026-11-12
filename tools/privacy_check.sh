#!/bin/bash
# AC-35: search every tracked file and the full git history for personal data.
# Patterns come from the environment so that no real value is written in this file:
#   WFD_PRIVATE_PATTERNS  extended regex of things that must never appear (home path, email, phone)
#   WFD_PRIVATE_STRINGS   optional file of exact strings, one per line (real job IDs, real session IDs)
# Exit code 0 only when nothing matches.
set -u
cd "$(dirname "$0")/.."
: "${WFD_PRIVATE_PATTERNS:?set WFD_PRIVATE_PATTERNS}"
TOKENS='(gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|Bearer [A-Za-z0-9._-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY)'
bad=0
echo "tracked files: $(git ls-files | wc -l | tr -d ' ')   commits: $(git rev-list --all --count)"
for name in private tokens; do
  [ $name = private ] && pat="$WFD_PRIVATE_PATTERNS" || pat="$TOKENS"
  n=$(git grep -I -n -E -e "$pat" -- . | wc -l | tr -d ' ')
  h=$(git log --all -p --no-color | grep -c -E -e "$pat")
  echo "$name: working tree matches=$n   history matches=$h"
  [ "$n" = 0 ] && [ "$h" = 0 ] || bad=1
done
if [ -n "${WFD_PRIVATE_STRINGS:-}" ]; then
  n=$(git grep -I -n -F -f "$WFD_PRIVATE_STRINGS" -- . | wc -l | tr -d ' ')
  h=$(git log --all -p --no-color | grep -c -F -f "$WFD_PRIVATE_STRINGS")
  echo "exact strings ($(wc -l < "$WFD_PRIVATE_STRINGS" | tr -d ' ') real job and session IDs): working tree matches=$n   history matches=$h"
  [ "$n" = 0 ] && [ "$h" = 0 ] || bad=1
fi
for dir in var evidence; do
  n=$(git log --all --name-only --pretty=format: | grep -c "^$dir/")
  echo "$dir/ files ever committed: $n"; [ "$n" = 0 ] || bad=1
done
n=$(git log --all --name-only --pretty=format: | grep -c "Collapse")
echo "Collapse font files ever committed: $n"; [ "$n" = 0 ] || bad=1
echo "author emails in history: $(git log --all --format='%ae%n%ce' | sort -u | tr '\n' ' ')"
[ $bad = 0 ] && echo "PRIVACY OK" || echo "PRIVACY FAILED"
exit $bad

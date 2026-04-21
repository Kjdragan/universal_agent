#!/usr/bin/env bash
# Skill Library Audit Scanner v3
# Produces raw data for skill health analysis
# Usage: bash scripts/audit_skills.sh [project_root]
#
# v3 changelog:
#   - Switched from pipe (|) to tab delimiter to avoid collision with YAML | literal scalar
#   - Added .agents/skills/ description check for canonical-only skills
#   - v2: Fixed multi-line YAML description parsing (> folded, | literal)

set -euo pipefail

ROOT="${1:-/opt/universal_agent}"
CLAUDE_SKILLS="$ROOT/.claude/skills"
AGENTS_SKILLS="$ROOT/.agents/skills"
NESTED_SKILLS="$ROOT/.claude/skills/skills"
TAB=$'\t'

echo "=== SKILL AUDIT SCAN ==="
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Root: $ROOT"
echo "Delimiter: TAB"
echo ""

# --- Helper: extract frontmatter description from a SKILL.md file ---
# Handles inline, > folded, and | literal YAML descriptions
_extract_desc() {
  local file="$1"
  local desc=""
  local in_fm=0
  while IFS= read -r line; do
    if [[ "$line" == "---" ]]; then
      ((in_fm++)) || true
      continue
    fi
    [[ $in_fm -ne 1 ]] && continue
    if [[ "$line" =~ ^description:[[:space:]]*(.*) ]]; then
      local val="${BASH_REMATCH[1]}"
      if [[ "$val" == ">" || "$val" == "|" || -z "$val" ]]; then
        # Read next non-empty indented line for actual content
        while IFS= read -r next; do
          [[ "$next" =~ ^[[:space:]]*$ ]] && continue
          [[ ! "$next" =~ ^[[:space:]] ]] && break
          desc="$next"
          break
        done
      else
        desc="$val"
      fi
      break
    fi
  done < "$file"
  # Strip leading whitespace
  desc="${desc#"${desc%%[![:space:]]*}"}"
  echo "$desc"
}

# 1. Count and list canonical skills (non-symlink) in .claude/skills/
echo "--- CANONICAL SKILLS (.claude/skills/) ---"
for dir in "$CLAUDE_SKILLS"/*/; do
  name=$(basename "$dir")
  [[ "$name" == "skills" ]] && continue  # skip nested parent
  if [ -L "$dir" ] && [ ! -d "$dir" ]; then
    continue  # broken symlink
  fi
  if [ -L "${dir%/}" ]; then
    target=$(readlink -f "${dir%/}")
    echo "SYMLINK${TAB}$name${TAB}$target"
  else
    has_skill="N"
    [ -f "$dir/SKILL.md" ] && has_skill="Y"
    lines=0
    [ -f "$dir/SKILL.md" ] && lines=$(wc -l < "$dir/SKILL.md")
    echo "CANONICAL${TAB}$name${TAB}$has_skill${TAB}$lines"
  fi
done

echo ""

# 2. List .agents/skills/ entries
echo "--- AGENTS SKILLS (.agents/skills/) ---"
if [ -d "$AGENTS_SKILLS" ]; then
  for dir in "$AGENTS_SKILLS"/*/; do
    name=$(basename "$dir")
    if [ -L "${dir%/}" ]; then
      target=$(readlink -f "${dir%/}")
      echo "SYMLINK${TAB}$name${TAB}$target"
    else
      has_skill="N"
      [ -f "$dir/SKILL.md" ] && has_skill="Y"
      lines=0
      [ -f "$dir/SKILL.md" ] && lines=$(wc -l < "$dir/SKILL.md")
      echo "CANONICAL${TAB}$name${TAB}$has_skill${TAB}$lines"
    fi
  done
else
  echo "DIRECTORY_NOT_FOUND"
fi

echo ""

# 3. List nested skills/skills/ entries
echo "--- NESTED SKILLS (.claude/skills/skills/) ---"
if [ -d "$NESTED_SKILLS" ]; then
  for dir in "$NESTED_SKILLS"/*/; do
    name=$(basename "$dir")
    has_skill="N"
    [ -f "$dir/SKILL.md" ] && has_skill="Y"
    lines=0
    [ -f "$dir/SKILL.md" ] && lines=$(wc -l < "$dir/SKILL.md")
    echo "NESTED${TAB}$name${TAB}$has_skill${TAB}$lines"
  done
else
  echo "DIRECTORY_NOT_FOUND"
fi

echo ""

# 4. Frontmatter description check — .claude/skills/ (all entries including symlinks)
echo "--- DESCRIPTION CHECK (.claude/skills/) ---"
for f in "$CLAUDE_SKILLS"/*/SKILL.md; do
  [ ! -f "$f" ] && continue
  name=$(basename "$(dirname "$f")")
  desc=$(_extract_desc "$f")
  if [ -z "$desc" ] || [ "$desc" = "null" ]; then
    echo "NO_DESC${TAB}$name"
  else
    short=$(echo "$desc" | head -c 120)
    echo "HAS_DESC${TAB}$name${TAB}$short"
  fi
done

echo ""

# 5. Frontmatter description check — .agents/skills/ (canonical only, skip symlinks)
echo "--- DESCRIPTION CHECK (.agents/skills/) ---"
if [ -d "$AGENTS_SKILLS" ]; then
  for dir in "$AGENTS_SKILLS"/*/; do
    [ -L "${dir%/}" ] && continue  # skip symlinks — they point back to .claude/skills/
    f="$dir/SKILL.md"
    [ ! -f "$f" ] && continue
    name=$(basename "$dir")
    desc=$(_extract_desc "$f")
    if [ -z "$desc" ] || [ "$desc" = "null" ]; then
      echo "NO_DESC${TAB}$name"
    else
      short=$(echo "$desc" | head -c 120)
      echo "HAS_DESC${TAB}$name${TAB}$short"
    fi
  done
fi

echo ""
echo "=== SCAN COMPLETE ==="

#!/usr/bin/env bash
# health-crawl.sh — Crawl health audit
set +o pipefail
set -e

DOMAIN="${1:?Usage: health-crawl.sh <domain> [--sitemap URL] [--limit 50]}"
SITEMAP="https://${DOMAIN}/sitemap.xml"
LIMIT=50

while [[ $# -gt 1 ]]; do
  case "$2" in
    --sitemap) SITEMAP="$3"; shift 2 ;;
    --limit) LIMIT="$3"; shift 2 ;;
    *) shift ;;
  esac
done

UA="Mozilla/5.0 (compatible; BTSA-SEOKit/1.0)"

echo "🕷️ Crawl Health Audit: $DOMAIN"
echo "Sitemap: $SITEMAP"
echo "======================================"

# Check robots.txt
echo ""
echo "📋 robots.txt"
ROBOTS=$(curl -sS -L -A "$UA" -o /dev/null -w "%{http_code}" "https://${DOMAIN}/robots.txt" || true)
if [[ "$ROBOTS" == "200" ]]; then
  echo "  ✅ robots.txt exists"
  SITEMAP_IN_ROBOTS=$(curl -sS -L -A "$UA" "https://${DOMAIN}/robots.txt" | grep -i "^sitemap:" || true)
  if [[ -n "$SITEMAP_IN_ROBOTS" ]]; then
    echo "  ✅ Sitemap referenced in robots.txt"
    ROBOTS_SITEMAP=$(echo "$SITEMAP_IN_ROBOTS" | head -1 | sed -E 's/^[Ss]itemap:[[:space:]]*//')
  else
    echo "  ⚠️ No sitemap reference in robots.txt"
    ROBOTS_SITEMAP=""
  fi
else
  echo "  ❌ robots.txt not found ($ROBOTS)"
  ROBOTS_SITEMAP=""
fi

# Resolve sitemap, following redirects.
echo ""
echo "🗺️ Sitemap"
if [[ -n "$ROBOTS_SITEMAP" ]]; then
  SITEMAP="$ROBOTS_SITEMAP"
fi
SITEMAP_STATUS=$(curl -sS -L -A "$UA" -o /dev/null -w "%{http_code}" "$SITEMAP" || true)
SITEMAP_BODY=$(curl -sS -L -A "$UA" "$SITEMAP" 2>/dev/null || true)

if [[ "$SITEMAP_STATUS" != "200" || -z "$SITEMAP_BODY" ]]; then
  FALLBACK="https://${DOMAIN}/sitemap_index.xml"
  FALLBACK_STATUS=$(curl -sS -L -A "$UA" -o /dev/null -w "%{http_code}" "$FALLBACK" || true)
  if [[ "$FALLBACK_STATUS" == "200" ]]; then
    SITEMAP="$FALLBACK"
    SITEMAP_STATUS="$FALLBACK_STATUS"
    SITEMAP_BODY=$(curl -sS -L -A "$UA" "$SITEMAP" 2>/dev/null || true)
  fi
fi

if [[ "$SITEMAP_STATUS" != "200" ]]; then
  echo "  ❌ Sitemap not accessible after redirects ($SITEMAP_STATUS)"
  exit 1
fi

LOCS=$(echo "$SITEMAP_BODY" | grep -oP '<loc>\s*\K[^<]+' | sed 's/&amp;/\&/g' || true)
if [[ -z "$LOCS" ]]; then
  echo "  ❌ Sitemap returned 200 but contained no <loc> entries"
  exit 1
fi

# If the sitemap contains child XML files, treat it as an index and expand them.
if echo "$LOCS" | grep -qiE '\.xml([?#].*)?$'; then
  URLS=""
  while IFS= read -r SUB; do
    [[ -z "$SUB" ]] && continue
    SUB_BODY=$(curl -sS -L -A "$UA" "$SUB" 2>/dev/null || true)
    SUB_URLS=$(echo "$SUB_BODY" | grep -oP '<loc>\s*\K[^<]+' | sed 's/&amp;/\&/g' | grep -viE '\.xml([?#].*)?$' || true)
    if [[ -n "$SUB_URLS" ]]; then
      URLS="${URLS}${SUB_URLS}"$'\n'
    fi
    CURRENT=$(printf "%s" "$URLS" | sed '/^$/d' | wc -l)
    [[ "$CURRENT" -ge "$LIMIT" ]] && break
  done <<< "$LOCS"
  URLS=$(printf "%s" "$URLS" | sed '/^$/d' | head -"$LIMIT")
  URL_COUNT=$(printf "%s\n" "$URLS" | sed '/^$/d' | wc -l)
  echo "  ✅ Sitemap index accessible ($URL_COUNT page URLs collected)"
else
  URLS=$(echo "$LOCS" | grep -viE '\.xml([?#].*)?$' | head -"$LIMIT")
  URL_COUNT=$(printf "%s\n" "$URLS" | sed '/^$/d' | wc -l)
  echo "  ✅ Sitemap accessible ($URL_COUNT page URLs collected)"
fi

[[ -z "$URLS" ]] && { echo "No page URLs found. Exiting."; exit 1; }

# Crawl pages
BROKEN=0
MISSING_TITLE=0
MISSING_DESC=0
MISSING_CANONICAL=0
MIXED_CONTENT=0
PAGES_CHECKED=0

echo ""
echo "🔍 Checking pages..."

while IFS= read -r PAGE; do
  [[ -z "$PAGE" ]] && continue
  PAGES_CHECKED=$((PAGES_CHECKED + 1))
  RESPONSE=$(curl -sS -A "$UA" -L --max-time 15 -w "\n%{http_code}" "$PAGE" 2>/dev/null || printf '\n000')
  STATUS=$(echo "$RESPONSE" | tail -1)
  BODY=$(echo "$RESPONSE" | sed '$d')
  SLUG=$(echo "$PAGE" | sed "s|https://${DOMAIN}||")

  if [[ "$STATUS" != "200" ]]; then
    echo "  ❌ $SLUG → HTTP $STATUS"
    BROKEN=$((BROKEN + 1))
    continue
  fi

  TITLE=$(echo "$BODY" | grep -oiP '<title[^>]*>\K[^<]*' | head -1 || true)
  DESC=$(echo "$BODY" | grep -oiP '<meta[^>]+name=["'"']description["'"'][^>]+content=["'"']\K[^"'"']*' | head -1 || true)
  [[ -z "$DESC" ]] && DESC=$(echo "$BODY" | grep -oiP '<meta[^>]+content=["'"']\K[^"'"']*(?=["'"'][^>]+name=["'"']description["'"'])' | head -1 || true)
  CANONICAL=$(echo "$BODY" | grep -oiP '<link[^>]+rel=["'"']canonical["'"'][^>]+href=["'"']\K[^"'"']*' | head -1 || true)
  [[ -z "$CANONICAL" ]] && CANONICAL=$(echo "$BODY" | grep -oiP '<link[^>]+href=["'"']\K[^"'"']*(?=["'"'][^>]+rel=["'"']canonical["'"'])' | head -1 || true)

  ISSUES=""
  [[ -z "$TITLE" ]] && { ISSUES="${ISSUES} no-title"; MISSING_TITLE=$((MISSING_TITLE + 1)); }
  [[ -z "$DESC" ]] && { ISSUES="${ISSUES} no-description"; MISSING_DESC=$((MISSING_DESC + 1)); }
  [[ -z "$CANONICAL" ]] && { ISSUES="${ISSUES} no-canonical"; MISSING_CANONICAL=$((MISSING_CANONICAL + 1)); }

  MIXED=$(echo "$BODY" | grep -Eoc '(src|href)=["'"']http://' 2>/dev/null || true)
  [[ "$MIXED" -gt 0 ]] && { ISSUES="${ISSUES} mixed-content"; MIXED_CONTENT=$((MIXED_CONTENT + 1)); }

  if [[ -n "$ISSUES" ]]; then
    echo "  ⚠️ $SLUG:$ISSUES"
  fi
done <<< "$URLS"

echo ""
echo "======================================"
echo "📊 Summary:"
echo "  Pages checked: $PAGES_CHECKED"
echo "  Broken pages (non-200): $BROKEN"
echo "  Missing title: $MISSING_TITLE"
echo "  Missing meta description: $MISSING_DESC"
echo "  Missing canonical: $MISSING_CANONICAL"
echo "  Mixed content: $MIXED_CONTENT"

TOTAL_ISSUES=$((BROKEN + MISSING_TITLE + MISSING_DESC + MISSING_CANONICAL + MIXED_CONTENT))
if [[ "$TOTAL_ISSUES" -eq 0 ]]; then
  echo ""
  echo "  ✅ No issues found!"
else
  echo ""
  echo "  ⚠️ $TOTAL_ISSUES total issues found"
fi

mkdir -p workspace/seo/health 2>/dev/null || true
echo "{\"date\":\"$(date +%Y-%m-%d)\",\"domain\":\"${DOMAIN}\",\"pages_checked\":${PAGES_CHECKED},\"broken\":${BROKEN},\"missing_title\":${MISSING_TITLE},\"missing_desc\":${MISSING_DESC},\"missing_canonical\":${MISSING_CANONICAL},\"mixed_content\":${MIXED_CONTENT}}" > "workspace/seo/health/crawl-$(date +%Y-%m-%d).json" 2>/dev/null || true

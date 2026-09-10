#!/usr/bin/env bash
# health-speed.sh — PageSpeed Insights + Core Web Vitals check
set -e

URL="${1:?Usage: health-speed.sh <url> [--mobile|--desktop|--both]}"
STRATEGY="${2:---both}"
API_KEY="${PAGESPEED_API_KEY:-}"

check_speed() {
  local url="$1" strategy="$2"
  local encoded_url
  encoded_url=$(python - <<'PY' "$url"
import sys, urllib.parse
print(urllib.parse.quote(sys.argv[1], safe=''))
PY
)
  local api_url="https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url=${encoded_url}&strategy=${strategy}&category=performance&category=seo&category=accessibility"
  [[ -n "$API_KEY" ]] && api_url="${api_url}&key=${API_KEY}"

  echo "📱 Checking ${strategy}..."
  local response http_code body error_message
  response=$(curl -sS -A "Mozilla/5.0 (compatible; BTSA-SEOKit/1.0)" -w $'\n%{http_code}' "$api_url" || true)
  http_code=$(echo "$response" | tail -1)
  body=$(echo "$response" | sed '$d')

  if [[ "$http_code" != "200" ]]; then
    error_message=$(echo "$body" | jq -r '.error.message // empty' 2>/dev/null || true)
    echo "  ❌ PageSpeed API failed (HTTP ${http_code})"
    [[ -n "$error_message" ]] && echo "  Error: $error_message"
    return 1
  fi

  if ! echo "$body" | jq -e '.lighthouseResult.categories.performance.score != null and .lighthouseResult.categories.seo.score != null' >/dev/null 2>&1; then
    error_message=$(echo "$body" | jq -r '.error.message // "No Lighthouse result returned"' 2>/dev/null || echo "No Lighthouse result returned")
    echo "  ❌ PageSpeed returned no usable Lighthouse data"
    echo "  Error: $error_message"
    return 1
  fi

  local lcp inp cls perf_score fcp speed_index ttfb seo_score
  lcp=$(echo "$body" | jq -r '.loadingExperience.metrics.LARGEST_CONTENTFUL_PAINT_MS.percentile // "n/a"')
  inp=$(echo "$body" | jq -r '.loadingExperience.metrics.INTERACTION_TO_NEXT_PAINT.percentile // "n/a"')
  cls=$(echo "$body" | jq -r '.loadingExperience.metrics.CUMULATIVE_LAYOUT_SHIFT_SCORE.percentile // "n/a"')
  perf_score=$(echo "$body" | jq -r '(.lighthouseResult.categories.performance.score * 100) | floor')
  fcp=$(echo "$body" | jq -r '.lighthouseResult.audits["first-contentful-paint"].numericValue // "n/a"')
  speed_index=$(echo "$body" | jq -r '.lighthouseResult.audits["speed-index"].numericValue // "n/a"')
  ttfb=$(echo "$body" | jq -r '.lighthouseResult.audits["server-response-time"].numericValue // "n/a"')
  seo_score=$(echo "$body" | jq -r '(.lighthouseResult.categories.seo.score * 100) | floor')

  echo ""
  echo "  Performance Score: ${perf_score}/100"
  echo "  SEO Score: ${seo_score}/100"
  echo ""
  echo "  Core Web Vitals (field data):"

  if [[ "$lcp" != "n/a" ]]; then
    local lcp_s lcp_status="✅ GOOD"
    lcp_s=$(echo "scale=2; $lcp / 1000" | bc 2>/dev/null || echo "$lcp")
    [[ "$lcp" -gt 2500 && "$lcp" -le 4000 ]] && lcp_status="⚠️ NEEDS WORK"
    [[ "$lcp" -gt 4000 ]] && lcp_status="❌ POOR"
    echo "    LCP: ${lcp_s}s ${lcp_status} (target < 2.5s)"
  else
    echo "    LCP: No field data"
  fi

  if [[ "$inp" != "n/a" ]]; then
    local inp_status="✅ GOOD"
    [[ "$inp" -gt 200 && "$inp" -le 500 ]] && inp_status="⚠️ NEEDS WORK"
    [[ "$inp" -gt 500 ]] && inp_status="❌ POOR"
    echo "    INP: ${inp}ms ${inp_status} (target < 200ms)"
  else
    echo "    INP: No field data"
  fi

  if [[ "$cls" != "n/a" ]]; then
    local cls_fmt cls_status="✅ GOOD"
    cls_fmt=$(echo "scale=2; $cls / 100" | bc 2>/dev/null || echo "$cls")
    [[ "$cls" -gt 10 && "$cls" -le 25 ]] && cls_status="⚠️ NEEDS WORK"
    [[ "$cls" -gt 25 ]] && cls_status="❌ POOR"
    echo "    CLS: ${cls_fmt} ${cls_status} (target < 0.1)"
  else
    echo "    CLS: No field data"
  fi

  echo ""
  echo "  Lab data:"
  [[ "$fcp" != "n/a" ]] && echo "    FCP: $(echo "scale=2; $fcp / 1000" | bc 2>/dev/null || echo "$fcp")s"
  [[ "$speed_index" != "n/a" ]] && echo "    Speed Index: $(echo "scale=2; $speed_index / 1000" | bc 2>/dev/null || echo "$speed_index")s"
  [[ "$ttfb" != "n/a" ]] && echo "    TTFB: $(echo "scale=0; $ttfb" | bc 2>/dev/null || echo "$ttfb")ms"

  echo ""
  echo "  Top opportunities:"
  local opportunities
  opportunities=$(echo "$body" | jq -r '[.lighthouseResult.audits | to_entries[] | select(.value.details.overallSavingsMs? > 0) | {name: .value.title, savings: .value.details.overallSavingsMs}] | sort_by(-.savings) | .[0:5][] | "    → \(.name) (save ~\(.savings|floor)ms)"' 2>/dev/null || true)
  if [[ -n "$opportunities" ]]; then
    echo "$opportunities"
  else
    echo "    (none detected)"
  fi

  mkdir -p workspace/seo/health 2>/dev/null || true
  echo "$body" | jq '{
    date: now | strftime("%Y-%m-%d"),
    strategy: "'"$strategy"'",
    url: "'"$url"'",
    performance: .lighthouseResult.categories.performance.score,
    seo: .lighthouseResult.categories.seo.score,
    lcp: (.loadingExperience.metrics.LARGEST_CONTENTFUL_PAINT_MS.percentile // null),
    inp: (.loadingExperience.metrics.INTERACTION_TO_NEXT_PAINT.percentile // null),
    cls: (.loadingExperience.metrics.CUMULATIVE_LAYOUT_SHIFT_SCORE.percentile // null)
  }' > "workspace/seo/health/speed-$(date +%Y-%m-%d)-${strategy}.json" 2>/dev/null || true
}

echo "🏥 PageSpeed + Core Web Vitals Check"
echo "URL: $URL"
echo "======================================"

FAILURES=0
case "$STRATEGY" in
  --mobile)  check_speed "$URL" "mobile" || FAILURES=$((FAILURES + 1)) ;;
  --desktop) check_speed "$URL" "desktop" || FAILURES=$((FAILURES + 1)) ;;
  --both|*)  check_speed "$URL" "mobile" || FAILURES=$((FAILURES + 1)); echo ""; check_speed "$URL" "desktop" || FAILURES=$((FAILURES + 1)) ;;
esac

echo ""
echo "======================================"
if [[ "$FAILURES" -gt 0 ]]; then
  echo "PageSpeed check incomplete: $FAILURES request(s) failed."
  exit 1
fi
echo "Done. Snapshots saved to workspace/seo/health/"

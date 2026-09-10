def score_gsc_rows(rows, strike_min=5, strike_max=20):
    scored = []
    for row in rows:
        position = float(row.get("position") or 0)
        impressions = int(row.get("impressions") or 0)
        ctr = float(row.get("ctr") or 0)
        position_factor = (
            2.0 if 5 <= position <= 10
            else 1.5 if 10 < position <= strike_max
            else 0.4 if 0 < position < 5
            else 0.8
        )
        ctr_gap = max(0.0, 0.10 - ctr)
        score = round((impressions / 10.0) * position_factor * (1 + ctr_gap * 5), 1)
        item = dict(row)
        item.update({
            "strike_zone": strike_min <= position <= strike_max,
            "opportunity_score": score,
            "reason": _reason(position, impressions, ctr),
        })
        scored.append(item)
    return sorted(scored, key=lambda x: x["opportunity_score"], reverse=True)

def _reason(position, impressions, ctr):
    if 5 <= position <= 10:
        return "Page-one striking distance; improve the existing ranking page first."
    if 10 < position <= 20:
        return "Page-two striking distance; assess intent, content depth and internal links."
    if position < 5 and ctr < 0.05 and impressions >= 50:
        return "Already ranks well but CTR is weak; review title and meta description."
    if position > 20 and impressions >= 100:
        return "Meaningful impressions but weak position; validate intent before expanding content."
    return "Monitor; lower priority than stronger commercial opportunities."

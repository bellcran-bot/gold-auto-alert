#!/usr/bin/env python3
"""Daily gold market monitor.

Creates a Markdown report and JSONL observation with:
- international gold futures price
- USD/KRW rate
- estimated KRW gold price per gram
- optional KRX public-data payload
- optional central-bank gold CSV summary
- latest news from GDELT
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


TROY_OUNCE_GRAMS = 31.1034768
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
NEWS_KEYWORDS = (
    "gold",
    "bullion",
    "central bank",
    "reserve",
    "fed",
    "federal reserve",
    "dollar",
    "treasury",
    "inflation",
    "geopolitical",
    "금",
    "중앙은행",
)


DEFAULT_CONFIG: dict[str, Any] = {
    "report_timezone": "Asia/Seoul",
    "news_query": '(gold OR "gold price" OR "central bank gold" OR "gold reserves")',
    "news_rss_query": "gold price central bank gold reserves",
    "news_max_records": 3,
    "alert_threshold_pct": 1.5,
    "central_bank_csv": "",
    "central_bank_download": {
        "enabled": False,
        "url": "",
        "target_path": "data/central_bank_gold.csv",
        "min_interval_hours": 24,
        "headers": {},
    },
    "central_bank_columns": {
        "date": "date",
        "country": "country",
        "tonnes": "tonnes",
        "change": "",
    },
    "krx": {
        "enabled": False,
        "api_url": "",
        "service_key_env": "DATA_GO_KR_SERVICE_KEY",
        "params": {"resultType": "json"},
    },
}


@dataclass
class PricePoint:
    symbol: str
    label: str
    currency: str
    latest: float | None
    previous: float | None
    change_pct: float | None
    closes: list[float]
    timestamp: str | None
    error: str | None = None


def load_config(path: Path | None) -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path is None:
        return config
    with path.open("r", encoding="utf-8") as f:
        user_config = json.load(f)
    return deep_merge(config, user_config)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def fetch_json(url: str, params: dict[str, Any] | None = None, timeout: int = 20) -> Any:
    if params:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{url}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "gold-monitor/0.1 (+personal research)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def fetch_yahoo_chart(symbol: str, label: str, currency: str) -> PricePoint:
    params = {"range": "10d", "interval": "1d"}
    try:
        payload = fetch_json(YAHOO_CHART_URL.format(symbol=urllib.parse.quote(symbol)), params)
        result = payload["chart"]["result"][0]
        timestamps = result.get("timestamp") or []
        quote = result["indicators"]["quote"][0]
        closes = [float(x) for x in quote.get("close", []) if x is not None and math.isfinite(float(x))]
        latest = closes[-1] if closes else None
        previous = closes[-2] if len(closes) >= 2 else None
        change_pct = percent_change(latest, previous)
        timestamp = None
        if timestamps:
            timestamp = dt.datetime.fromtimestamp(timestamps[-1], dt.UTC).isoformat()
        return PricePoint(symbol, label, currency, latest, previous, change_pct, closes, timestamp)
    except Exception as exc:  # noqa: BLE001 - report partial failures instead of stopping.
        return PricePoint(symbol, label, currency, None, None, None, [], None, error=str(exc))


def percent_change(latest: float | None, previous: float | None) -> float | None:
    if latest is None or previous in (None, 0):
        return None
    return (latest - previous) / previous * 100


def trend_from_closes(closes: list[float]) -> str:
    if len(closes) < 3:
        return "데이터 부족"
    start = closes[0]
    end = closes[-1]
    change = percent_change(end, start)
    if change is None:
        return "데이터 부족"
    if change >= 1:
        return f"상승({change:.2f}%)"
    if change <= -1:
        return f"하락({change:.2f}%)"
    return f"횡보({change:.2f}%)"


def fetch_news(config: dict[str, Any]) -> list[dict[str, str]]:
    query = str(config.get("news_query") or DEFAULT_CONFIG["news_query"])
    rss_query = str(config.get("news_rss_query") or "gold price")
    max_records = int(config.get("news_max_records") or 3)
    gdelt_news = fetch_gdelt_news(query, max_records)
    if gdelt_news and not gdelt_news[0].get("error"):
        relevant = filter_relevant_news(gdelt_news)
        if len(relevant) >= max_records:
            return relevant[:max_records]

    fallback_news = fetch_google_news_rss(rss_query, max_records)
    if fallback_news:
        relevant = filter_relevant_news(fallback_news)
        return (relevant or fallback_news)[:max_records]
    return gdelt_news


def filter_relevant_news(articles: list[dict[str, str]]) -> list[dict[str, str]]:
    relevant = []
    for article in articles:
        haystack = " ".join(
            [
                article.get("title", ""),
                article.get("source", ""),
            ]
        ).lower()
        if any(keyword in haystack for keyword in NEWS_KEYWORDS):
            relevant.append(article)
    return relevant


def fetch_gdelt_news(query: str, max_records: int) -> list[dict[str, str]]:
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": max_records,
        "sort": "datedesc",
    }
    try:
        payload = fetch_json(GDELT_DOC_URL, params)
    except Exception as exc:  # noqa: BLE001
        return [{"title": "뉴스 수집 실패", "source": "GDELT", "url": "", "seen": "", "error": str(exc)}]

    articles = payload.get("articles") or []
    news = []
    seen_urls = set()
    for article in articles:
        url = str(article.get("url") or "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        news.append(
            {
                "title": str(article.get("title") or "(제목 없음)"),
                "source": str(article.get("domain") or article.get("sourceCountry") or "unknown"),
                "url": url,
                "seen": str(article.get("seendate") or ""),
            }
        )
        if len(news) >= max_records:
            break
    return news


def fetch_google_news_rss(query: str, max_records: int) -> list[dict[str, str]]:
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    url = f"{GOOGLE_NEWS_RSS_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; gold-monitor/0.1)",
            "Accept": "application/rss+xml, application/xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            root = ET.fromstring(response.read())
    except Exception:  # noqa: BLE001
        return []

    articles = []
    for item in root.findall("./channel/item"):
        source = item.find("source")
        articles.append(
            {
                "title": item.findtext("title") or "(제목 없음)",
                "source": source.text if source is not None and source.text else "Google News",
                "url": item.findtext("link") or "",
                "seen": item.findtext("pubDate") or "",
            }
        )
        if len(articles) >= max_records:
            break
    return articles


def fetch_krx_optional(config: dict[str, Any]) -> dict[str, Any]:
    krx_config = config.get("krx") or {}
    if not krx_config.get("enabled"):
        return {"enabled": False, "status": "disabled"}
    api_url = krx_config.get("api_url")
    if not api_url:
        return {"enabled": True, "status": "missing api_url"}

    params = dict(krx_config.get("params") or {})
    service_key_env = krx_config.get("service_key_env") or "DATA_GO_KR_SERVICE_KEY"
    service_key = os.getenv(service_key_env)
    if service_key:
        params.setdefault("serviceKey", service_key)

    try:
        payload = fetch_json(api_url, params)
        return {"enabled": True, "status": "ok", "payload": payload}
    except Exception as exc:  # noqa: BLE001
        return {"enabled": True, "status": "error", "error": str(exc)}


def maybe_download_central_bank_csv(
    config: dict[str, Any],
    state: dict[str, Any],
    state_path: Path,
    force: bool = False,
) -> dict[str, Any]:
    download_config = config.get("central_bank_download") or {}
    if not download_config.get("enabled"):
        return {"enabled": False, "status": "disabled"}

    url = str(download_config.get("url") or "")
    target_path = Path(str(download_config.get("target_path") or "data/central_bank_gold.csv")).expanduser()
    if not url:
        return {"enabled": True, "status": "missing url"}

    source_state = state.setdefault("central_bank_download", {})
    now = dt.datetime.now(dt.UTC)
    last_checked = parse_datetime(source_state.get("last_checked_at"))
    min_interval = float(download_config.get("min_interval_hours") or 24)
    if not force and last_checked is not None:
        elapsed_hours = (now - last_checked).total_seconds() / 3600
        if elapsed_hours < min_interval:
            return {
                "enabled": True,
                "status": "skipped",
                "reason": f"checked {elapsed_hours:.1f}h ago",
                "target_path": str(target_path),
            }

    headers = {
        "User-Agent": "gold-monitor/0.1 (+personal research)",
        "Accept": "text/csv, application/csv, text/plain, */*",
    }
    headers.update(download_config.get("headers") or {})
    if source_state.get("etag"):
        headers["If-None-Match"] = source_state["etag"]
    if source_state.get("last_modified"):
        headers["If-Modified-Since"] = source_state["last_modified"]

    request = urllib.request.Request(url, headers=headers)
    source_state["last_checked_at"] = now.isoformat()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            etag = response.headers.get("ETag")
            last_modified = response.headers.get("Last-Modified")
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            save_state(state_path, state)
            return {"enabled": True, "status": "not modified", "target_path": str(target_path)}
        source_state["last_error"] = f"HTTP {exc.code}: {exc.reason}"
        save_state(state_path, state)
        return {"enabled": True, "status": "error", "error": source_state["last_error"], "target_path": str(target_path)}
    except Exception as exc:  # noqa: BLE001
        source_state["last_error"] = str(exc)
        save_state(state_path, state)
        return {"enabled": True, "status": "error", "error": str(exc), "target_path": str(target_path)}

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(body)
    source_state.update(
        {
            "etag": etag,
            "last_modified": last_modified,
            "last_downloaded_at": now.isoformat(),
            "last_size_bytes": len(body),
            "content_type": content_type,
            "target_path": str(target_path),
        }
    )
    source_state.pop("last_error", None)
    state["central_bank_download"] = source_state
    save_state(state_path, state)
    return {
        "enabled": True,
        "status": "downloaded",
        "target_path": str(target_path),
        "size_bytes": len(body),
        "content_type": content_type,
    }


def parse_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def summarize_central_bank_csv(config: dict[str, Any]) -> dict[str, Any]:
    csv_path = config.get("central_bank_csv") or ""
    if not csv_path:
        return {"configured": False, "status": "no csv configured"}
    path = Path(csv_path).expanduser()
    if not path.exists():
        return {"configured": True, "status": f"csv not found: {path}"}

    columns = config.get("central_bank_columns") or {}
    date_col = columns.get("date") or "date"
    country_col = columns.get("country") or "country"
    tonnes_col = columns.get("tonnes") or "tonnes"
    change_col = columns.get("change") or ""

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "status": "read error", "error": str(exc)}

    cleaned = []
    for row in rows:
        date_value = row.get(date_col, "")
        country = row.get(country_col, "")
        tonnes = parse_float(row.get(tonnes_col))
        change = parse_float(row.get(change_col)) if change_col else None
        if date_value and country and tonnes is not None:
            cleaned.append({"date": date_value, "country": country, "tonnes": tonnes, "change": change})

    if not cleaned:
        return {"configured": True, "status": "no usable rows"}

    if change_col:
        latest_date = max(row["date"] for row in cleaned)
        latest = [row for row in cleaned if row["date"] == latest_date and row["change"] is not None]
    else:
        latest = compute_changes_from_holdings(cleaned)
        latest_date = latest[0]["date"] if latest else max(row["date"] for row in cleaned)

    buys = sorted([row for row in latest if row["change"] and row["change"] > 0], key=lambda r: r["change"], reverse=True)[:5]
    sells = sorted([row for row in latest if row["change"] and row["change"] < 0], key=lambda r: r["change"])[:5]
    return {
        "configured": True,
        "status": "ok" if latest else "not enough dates",
        "latest_date": latest_date,
        "top_buys": buys,
        "top_sells": sells,
    }


def compute_changes_from_holdings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dates = sorted({row["date"] for row in rows})
    if len(dates) < 2:
        return []
    previous_date, latest_date = dates[-2], dates[-1]
    previous = {row["country"]: row["tonnes"] for row in rows if row["date"] == previous_date}
    latest_rows = [row for row in rows if row["date"] == latest_date]
    changes = []
    for row in latest_rows:
        old = previous.get(row["country"])
        if old is None:
            continue
        changes.append({**row, "change": row["tonnes"] - old})
    return changes


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        text = str(value).replace(",", "").strip()
        if not text:
            return None
        return float(text)
    except ValueError:
        return None


def estimate_krw_gold_per_gram(gold_usd_oz: float | None, usdkrw: float | None) -> float | None:
    if gold_usd_oz is None or usdkrw is None:
        return None
    return gold_usd_oz * usdkrw / TROY_OUNCE_GRAMS


def build_signals(
    gold: PricePoint,
    usdkrw: PricePoint,
    krw_gold_change_pct: float | None,
    central_bank: dict[str, Any],
    threshold_pct: float,
) -> list[str]:
    signals = []
    if gold.change_pct is not None and abs(gold.change_pct) >= threshold_pct:
        signals.append(f"국제 금 가격 변동률이 {gold.change_pct:.2f}%로 임계값 {threshold_pct:.2f}%를 넘었습니다.")
    if krw_gold_change_pct is not None and abs(krw_gold_change_pct) >= threshold_pct:
        signals.append(f"원화 환산 금 가격 변동률이 {krw_gold_change_pct:.2f}%로 임계값 {threshold_pct:.2f}%를 넘었습니다.")
    if usdkrw.change_pct is not None and abs(usdkrw.change_pct) >= 1:
        signals.append(f"원/달러 환율 변동률이 {usdkrw.change_pct:.2f}%입니다.")

    for row in central_bank.get("top_buys") or []:
        if row.get("change") is not None and row["change"] >= 5:
            signals.append(f"{row['country']} 중앙은행 금 순매입이 {row['change']:.1f}t입니다.")

    if not signals:
        signals.append("설정된 임계값을 넘는 신호는 없습니다.")
    return signals


def fmt_money(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.{digits}f}"


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}%"


def render_report(observation: dict[str, Any]) -> str:
    gold = observation["prices"]["gold_usd_oz"]
    usdkrw = observation["prices"]["usdkrw"]
    krw = observation["prices"]["estimated_krw_gold_per_gram"]
    cb = observation["central_bank"]
    cb_download = observation.get("central_bank_download") or {}
    news = observation["news"]

    lines = [
        f"# 금시세 일일 모니터링 - {observation['report_date']}",
        "",
        f"- 생성 시각: {observation['generated_at']}",
        f"- 국제 금 가격: {fmt_money(gold['latest'])} USD/oz ({fmt_pct(gold['change_pct'])})",
        f"- 원/달러 환율: {fmt_money(usdkrw['latest'])} KRW/USD ({fmt_pct(usdkrw['change_pct'])})",
        f"- 원화 환산 금 가격 추정: {fmt_money(krw['latest'], 0)} KRW/g ({fmt_pct(krw['change_pct'])})",
        f"- 7일 국제 금 추세: {observation['trend']}",
        "",
        "## Signals",
        "",
    ]
    lines.extend(f"- {signal}" for signal in observation["signals"])

    lines.extend(["", "## 중앙은행 금 동향", ""])
    if cb_download.get("enabled"):
        detail = cb_download.get("reason") or cb_download.get("error") or cb_download.get("target_path") or ""
        lines.append(f"- CSV 다운로드 상태: {cb_download.get('status')} {detail}".rstrip())
    if cb.get("status") == "ok":
        lines.append(f"- 기준월: {cb.get('latest_date')}")
        lines.append("- 순매입 상위:")
        lines.extend(format_cb_rows(cb.get("top_buys") or []))
        lines.append("- 순매도 상위:")
        lines.extend(format_cb_rows(cb.get("top_sells") or []))
    else:
        lines.append(f"- {cb.get('status', '데이터 없음')}")

    lines.extend(["", "## 관련 뉴스", ""])
    for idx, article in enumerate(news, 1):
        title = article.get("title", "(제목 없음)")
        source = article.get("source", "unknown")
        url = article.get("url", "")
        seen = article.get("seen", "")
        if url:
            lines.append(f"{idx}. [{title}]({url}) - {source} {seen}")
        else:
            lines.append(f"{idx}. {title} - {source} {article.get('error', '')}")

    lines.extend(
        [
            "",
            "## 해석",
            "",
            render_interpretation(gold, usdkrw, krw, observation["signals"]),
            "",
            "> 참고: 이 리포트는 투자 조언이 아니라 개인 모니터링 자동화 결과입니다.",
            "",
        ]
    )
    return "\n".join(lines)


def format_cb_rows(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["  - 데이터 없음"]
    return [f"  - {row['country']}: {row['change']:+.1f}t, 보유 {row['tonnes']:.1f}t" for row in rows]


def render_interpretation(gold: dict[str, Any], usdkrw: dict[str, Any], krw: dict[str, Any], signals: list[str]) -> str:
    pieces = []
    if gold.get("change_pct") is not None:
        direction = "상승" if gold["change_pct"] > 0 else "하락" if gold["change_pct"] < 0 else "보합"
        pieces.append(f"국제 금 가격은 전일 대비 {direction}했습니다.")
    if gold.get("change_pct") is not None and usdkrw.get("change_pct") is not None and krw.get("change_pct") is not None:
        if abs(krw["change_pct"]) > abs(gold["change_pct"]):
            pieces.append("원화 기준 움직임은 환율 영향까지 더해져 국제 금 가격보다 크게 나타났습니다.")
        else:
            pieces.append("원화 기준 움직임은 국제 금 가격 변화와 비슷하거나 더 완만했습니다.")
    if signals and signals[0] != "설정된 임계값을 넘는 신호는 없습니다.":
        pieces.append("오늘은 신호 섹션의 항목을 우선 확인하는 편이 좋습니다.")
    return " ".join(pieces) if pieces else "충분한 가격 데이터를 가져오지 못해 해석을 만들 수 없습니다."


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a daily gold monitoring report.")
    parser.add_argument("--config", type=Path, default=None, help="JSON config path.")
    parser.add_argument("--output-dir", type=Path, default=Path("reports"), help="Report output directory.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="JSONL observation directory.")
    parser.add_argument("--state-path", type=Path, default=Path("data/source_state.json"), help="Download state path.")
    parser.add_argument("--force-download", action="store_true", help="Ignore download interval checks.")
    parser.add_argument("--dry-run", action="store_true", help="Print report without writing files.")
    args = parser.parse_args()

    config = load_config(args.config)
    state = load_state(args.state_path)
    timezone = ZoneInfo(config.get("report_timezone") or "Asia/Seoul")
    now = dt.datetime.now(timezone)

    central_bank_download = maybe_download_central_bank_csv(config, state, args.state_path, args.force_download)
    if central_bank_download.get("status") == "downloaded":
        download_config = config.get("central_bank_download") or {}
        config["central_bank_csv"] = download_config.get("target_path") or config.get("central_bank_csv")

    gold = fetch_yahoo_chart("GC=F", "Gold futures", "USD/oz")
    usdkrw = fetch_yahoo_chart("KRW=X", "USD/KRW", "KRW/USD")
    news = fetch_news(config)
    central_bank = summarize_central_bank_csv(config)
    krx = fetch_krx_optional(config)

    krw_latest = estimate_krw_gold_per_gram(gold.latest, usdkrw.latest)
    krw_previous = estimate_krw_gold_per_gram(gold.previous, usdkrw.previous)
    krw_change_pct = percent_change(krw_latest, krw_previous)
    threshold = float(config.get("alert_threshold_pct") or 1.5)
    signals = build_signals(gold, usdkrw, krw_change_pct, central_bank, threshold)

    observation = {
        "generated_at": now.isoformat(),
        "report_date": now.date().isoformat(),
        "prices": {
            "gold_usd_oz": pricepoint_to_dict(gold),
            "usdkrw": pricepoint_to_dict(usdkrw),
            "estimated_krw_gold_per_gram": {
                "latest": krw_latest,
                "previous": krw_previous,
                "change_pct": krw_change_pct,
            },
        },
        "trend": trend_from_closes(gold.closes[-7:]),
        "central_bank_download": central_bank_download,
        "central_bank": central_bank,
        "krx": krx,
        "news": news,
        "signals": signals,
    }

    report = render_report(observation)
    if args.dry_run:
        print(report)
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / f"gold_report_{now.date().isoformat()}.md"
    data_path = args.data_dir / "observations.jsonl"
    report_path.write_text(report, encoding="utf-8")
    with data_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(observation, ensure_ascii=False, sort_keys=True) + "\n")

    print(f"Wrote {report_path}")
    print(f"Appended {data_path}")
    return 0


def pricepoint_to_dict(point: PricePoint) -> dict[str, Any]:
    return {
        "symbol": point.symbol,
        "label": point.label,
        "currency": point.currency,
        "latest": point.latest,
        "previous": point.previous,
        "change_pct": point.change_pct,
        "timestamp": point.timestamp,
        "error": point.error,
    }


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        raise SystemExit(130)

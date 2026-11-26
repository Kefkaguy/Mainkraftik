"""Private, versioned information ingestion; never broker or redistribution APIs."""

import hashlib
import re
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup

from intelligence.calendar import EASTERN
from intelligence.config import settings
from intelligence.db import connection
from intelligence.ingestion import ingest_prices, resource_check
from intelligence.providers import ProviderError, get_json
from intelligence.storage import jsonb, store_document
from intelligence.util import digest, now

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
IR_HOSTS = {"investor.axon.com", "www.motorolasolutions.com", "newsroom.motorolasolutions.com"}


def stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ProviderError("Information timestamps require a timezone")
    return result.astimezone(UTC)


def tags(text):
    text = re.sub(r"\s+", " ", text).lower()
    guidance = None
    if not re.search(r"\b(not|no|unchanged from prior)\b", text):
        if re.search(r"\b(raises?|raised|increases?|increased)\b.{0,55}\b(guidance|outlook)\b", text):
            guidance = 1
        elif re.search(r"\b(lowers?|lowered|reduces?|reduced)\b.{0,55}\b(guidance|outlook)\b", text):
            guidance = -1
        elif re.search(r"\b(reaffirms?|reaffirmed|maintains?)\b.{0,55}\b(guidance|outlook)\b", text):
            guidance = 0
    return {
        "earnings": bool(re.search(r"\b(earnings|financial results|reports?.{0,35}revenue)\b", text)),
        "contract": bool(re.search(r"\b(contract|selected|selects|award|awarded)\b", text)),
        "acquisition": bool(re.search(r"\b(acqui(?:re|res|red|sition)|merger)\b", text)),
        "guidance_direction": guidance,
    }


def news_metadata(row):
    created, updated = stamp(row["created_at"]), stamp(row["updated_at"])
    if updated < created:
        raise ProviderError("Article update precedes creation")
    # Never retain full article bodies or summaries. Headline is private metadata.
    title = str(row.get("headline", ""))[:1000]
    return {
        "external_id": str(row["id"]),
        "created_at": created,
        "updated_at": updated,
        "available_at": updated,
        "symbols": sorted(set(row.get("symbols", []))),
        "source": row.get("source"),
        "author": row.get("author"),
        "url": row.get("url"),
        "headline": title,
        "flags": tags(title),
        "original_payload_hash": digest(row),
        "availability_basis": "Provider latest-version update timestamp; historical archive is reconstructed",
    }


def persist_information(conn, provider, metadata, source_id, acquired):
    identity = digest({"provider": provider, **metadata})
    if provider == "official_ir":
        identity = digest(
            [
                provider,
                metadata["external_id"],
                metadata["headline"],
                metadata["flags"],
                metadata.get("publication_original"),
            ]
        )
    conn.execute(
        """INSERT INTO information_versions VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT DO NOTHING""",
        (
            identity,
            provider,
            metadata["external_id"],
            jsonb(metadata["symbols"]),
            metadata["created_at"],
            metadata["updated_at"],
            metadata["available_at"],
            acquired,
            source_id,
            jsonb(metadata),
        ),
    )
    return identity


def ingest_news(start=None, end=None, client=None):
    start = start or datetime(2021, 7, 1, tzinfo=UTC)
    end = end or now() - timedelta(minutes=20)
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("An ordered aware news interval is required")
    resource_check(64 * 1024**2)
    headers = {"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret}
    params = {
        "symbols": "MSI,AXON",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sort": "asc",
        "limit": 50,
        "include_content": "false",
        "exclude_contentless": "false",
    }
    owned = client is None
    client = client or httpx.Client(timeout=40, follow_redirects=False)
    seen, count, pages, status, error = set(), 0, 0, "complete", None
    try:
        for _ in range(600):
            payload = get_json(client, NEWS_URL, params=params, headers=headers)
            acquired = now()
            metadata = [news_metadata(row) for row in payload.get("news", [])]
            with connection() as conn:
                sid = store_document(
                    conn,
                    "alpaca_news_metadata",
                    NEWS_URL,
                    {
                        "request": {k: v for k, v in params.items() if k != "page_token"},
                        "articles": metadata,
                        "page_hash": digest(payload),
                    },
                    acquired,
                )
                for row in metadata:
                    persist_information(conn, "alpaca_news", row, sid, acquired)
            count += len(metadata)
            pages += 1
            token = payload.get("next_page_token")
            if not token:
                break
            if token in seen:
                raise ProviderError("Repeated news page token; coverage not complete")
            seen.add(token)
            params["page_token"] = token
            time.sleep(0.12)
        else:
            raise ProviderError("News pagination budget exceeded; coverage not complete")
    except (ValueError, httpx.HTTPError, KeyError) as exc:
        status, error = (
            ("partial" if pages else "unavailable"),
            f"{type(exc).__name__}: news ingestion incomplete",
        )
    finally:
        if owned:
            client.close()
    with connection() as conn:
        conn.execute(
            "INSERT INTO information_fetches VALUES(%s,'alpaca_news',%s,%s,%s,%s,%s,%s)",
            (
                uuid4(),
                jsonb(["MSI", "AXON"]),
                start,
                end,
                now(),
                status,
                jsonb({"rows": count, "pages": pages, "error": error}),
            ),
        )
    return {
        "provider": "alpaca_news",
        "status": status,
        "articles_returned": count,
        "pages": pages,
        "error": error,
    }


def ingest_filings():
    count = 0
    with connection() as conn:
        sources = conn.execute(
            "SELECT * FROM source_documents WHERE provider='sec_submissions' ORDER BY acquired_at"
        ).fetchall()
        for source in sources:
            payload = source["payload"]
            symbol = next((s for s in payload.get("tickers", []) if s in {"MSI", "AXON"}), None)
            if not symbol:
                continue
            recent = payload.get("filings", {}).get("recent", {})
            for i, accession in enumerate(recent.get("accessionNumber", [])):
                form = recent["form"][i]
                if form not in {"8-K", "8-K/A", "10-K", "10-K/A", "10-Q", "10-Q/A"}:
                    continue
                accepted = (recent.get("acceptanceDateTime") or [""] * len(recent["accessionNumber"]))[i]
                filed = recent["filingDate"][i]
                published = (
                    stamp(accepted)
                    if accepted
                    else datetime.combine(
                        datetime.fromisoformat(filed).date() + timedelta(days=1), datetime.min.time(), EASTERN
                    ).astimezone(UTC)
                )
                items = (recent.get("items") or [""] * len(recent["accessionNumber"]))[i]
                data = {
                    "accession": accession,
                    "form": form,
                    "items": items,
                    "published_at": published,
                    "filed_date": filed,
                    "acceptance_original": accepted,
                    "earnings_filing": form.startswith("8-K") and "2.02" in items,
                    "availability_basis": "SEC acceptance"
                    if accepted
                    else "Conservative next midnight after filing date",
                }
                conn.execute(
                    "INSERT INTO filing_events VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (
                        digest([symbol, data]),
                        symbol,
                        accession,
                        form,
                        items,
                        published,
                        source["acquired_at"],
                        source["id"],
                        jsonb(data),
                    ),
                )
                count += 1
    return {
        "status": "complete",
        "filing_versions_seen": count,
        "coverage": "Archived recent-submission arrays; older archive files not assumed complete",
    }


def ir_get(client, url):
    for _ in range(4):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in IR_HOSTS
            or parsed.username
            or parsed.port not in {None, 443}
        ):
            raise ProviderError("Official IR host is not permitted")
        response = client.get(url)
        if response.status_code in {301, 302, 307, 308}:
            url = urljoin(url, response.headers.get("location", ""))
            continue
        if response.status_code not in {200, 404} or len(response.content) > 3 * 1024**2:
            raise ProviderError("Official IR page unavailable or exceeds size budget")
        return response
    raise ProviderError("Official IR redirect budget exceeded")


def ingest_ir():
    results = []
    with httpx.Client(
        timeout=30,
        follow_redirects=False,
        headers={"User-Agent": settings.sec_user_agent or "KefTrade personal research"},
    ) as client:
        for symbol, listing in [
            ("AXON", "https://investor.axon.com/press-releases/"),
            ("MSI", "https://www.motorolasolutions.com/newsroom.html"),
        ]:
            try:
                robots = ir_get(client, urljoin(listing, "/robots.txt"))
                parser = RobotFileParser()
                parser.parse(robots.text.splitlines() if robots.status_code == 200 else [])
                if robots.status_code == 200 and not parser.can_fetch("*", listing):
                    raise ProviderError("Official listing disallowed by robots")
                response = ir_get(client, listing)
                if response.status_code != 200:
                    raise ProviderError("Official IR listing missing")
                acquired = now()
                soup = BeautifulSoup(response.text, "html.parser")
                links = {}
                for a in soup.find_all("a", href=True):
                    url = urljoin(listing, a["href"])
                    title = a.get_text(" ", strip=True)
                    relevant = (
                        bool(re.search(r"/20\d\d-\d\d-\d\d-", url))
                        if symbol == "AXON"
                        else "/newsroom/press-releases/" in url
                    )
                    if relevant and urlsplit(url).hostname in IR_HOSTS and not url.endswith("/de.html"):
                        links[url] = title
                metadata = []
                for url, title in list(links.items())[:12]:
                    if not parser.can_fetch("*", url) and robots.status_code == 200:
                        continue
                    page = ir_get(client, url)
                    if page.status_code != 200:
                        continue
                    time.sleep(0.25)
                    article = BeautifulSoup(page.text, "html.parser")
                    heading = article.find("meta", property="og:title") or article.find("h1")
                    if heading:
                        title = heading.get("content") or heading.get_text(" ", strip=True)
                    published = article.find("meta", attrs={"name": "press_release_publish_date"})
                    original = published.get("content") if published else None
                    hint = re.search(r"/(20\d\d-\d\d-\d\d)-", url)
                    created = (
                        stamp(original)
                        if original
                        else datetime.combine(
                            datetime.fromisoformat(hint[1]).date(),
                            datetime.min.time(),
                            EASTERN,
                        ).astimezone(UTC)
                        if hint
                        else acquired
                    )
                    flags = tags(title)
                    evidence = next(
                        (
                            p.get_text(" ", strip=True)
                            for p in article.find_all("p")
                            if len(p.get_text()) < 700
                            and tags(p.get_text())["guidance_direction"] is not None
                        ),
                        None,
                    )
                    if evidence:
                        flags["guidance_direction"] = tags(evidence)["guidance_direction"]
                    # Listing text can be edited without an update timestamp. Do not backdate it.
                    row = {
                        "external_id": url,
                        "symbols": [symbol],
                        "created_at": created,
                        "updated_at": max(acquired, created),
                        "available_at": max(acquired, created),
                        "headline": title,
                        "url": url,
                        "source": "official_ir",
                        "flags": flags,
                        "publication_original": original or (hint[1] if hint else None),
                        "creation_timestamp_basis": "Official publication timestamp"
                        if original
                        else "Date-only URL lower bound; availability is acquisition"
                        if hint
                        else "First observation; original publication unavailable",
                        "guidance_evidence_hash": digest(evidence) if evidence else None,
                        "original_update_timestamp": None,
                        "availability_basis": "Observed acquisition; original publication/edit time unverified",
                    }
                    metadata.append(row)
                with connection() as conn:
                    sid = store_document(
                        conn,
                        "official_ir_metadata",
                        str(response.url),
                        {
                            "page_hash": hashlib.sha256(response.content).hexdigest(),
                            "announcements": metadata,
                        },
                        acquired,
                    )
                    for row in metadata:
                        persist_information(conn, "official_ir", row, sid, acquired)
                results.append(
                    {
                        "symbol": symbol,
                        "status": "complete",
                        "announcements": len(metadata),
                        "historical_eligible": False,
                    }
                )
            except (ValueError, httpx.HTTPError):
                results.append(
                    {
                        "symbol": symbol,
                        "status": "unavailable",
                        "reason": "Official IR access or robots restriction; no substitute feed",
                    }
                )
    return results


def refresh_information(backfill=False):
    news = ingest_news(start=None if backfill else now() - timedelta(days=45))
    filings = ingest_filings()
    ir = ingest_ir()
    markets = []
    for symbol in ("XLK", "XLI"):
        try:
            markets.append(ingest_prices(symbol))
        except ValueError as exc:
            markets.append({"symbol": symbol, "status": "unavailable", "reason": str(exc)})
    return {"news": news, "filings": filings, "official_ir": ir, "markets": markets}

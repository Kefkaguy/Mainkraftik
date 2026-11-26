"""Append exact publication metadata found on official pages; keep edits acquisition-dated.

This source enrichment does not change frozen features, models, trials or historical
snapshots. IR remains ineligible before actual acquisition.
"""

import time
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from intelligence.config import settings
from intelligence.db import connection
from intelligence.information import ir_get, persist_information, stamp
from intelligence.storage import store_document
from intelligence.util import digest, now


def publication_metadata(html):
    soup = BeautifulSoup(html, "html.parser")
    for meta in soup.find_all("meta"):
        key = (meta.get("name") or meta.get("property") or "").lower()
        if key in {"press_release_publish_date", "article:published_time", "datepublished", "pubdate"}:
            value = meta.get("content", "")
            try:
                return value, stamp(value)
            except ValueError:
                continue
    return None, None


def enrich_ir_publications():
    with connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ON(external_id) * FROM information_versions WHERE provider='official_ir' ORDER BY external_id,acquired_at DESC"
        ).fetchall()
    corrected = 0
    with httpx.Client(
        timeout=30, follow_redirects=False, headers={"User-Agent": settings.sec_user_agent}
    ) as client:
        permissions = {}
        for row in rows:
            url = row["payload"]["url"]
            host = str(httpx.URL(url).host)
            try:
                if host not in permissions:
                    robot = ir_get(client, urljoin(url, "/robots.txt"))
                    parser = RobotFileParser()
                    parser.parse(robot.text.splitlines() if robot.status_code == 200 else [])
                    permissions[host] = (robot.status_code, parser)
                status, parser = permissions[host]
                if status == 200 and not parser.can_fetch("*", url):
                    continue
                response = ir_get(client, url)
                if response.status_code != 200:
                    continue
                original, published = publication_metadata(response.text)
                if not published:
                    continue
                acquired = now()
                metadata = {
                    **row["payload"],
                    "created_at": published,
                    "updated_at": max(acquired, published),
                    "available_at": max(acquired, published),
                    "publication_original": original,
                    "creation_timestamp_basis": "Exact official publication metadata",
                    "availability_basis": "Observed acquisition; edit history unverified",
                    "source_adapter_version": "official-publication-v1",
                }
                with connection() as conn:
                    source = store_document(
                        conn,
                        "official_ir_publication_metadata",
                        url,
                        {
                            "publication_original": original,
                            "page_hash": digest(response.text),
                            "adapter": "official-publication-v1",
                        },
                        acquired,
                    )
                    persist_information(conn, "official_ir", metadata, source, acquired)
                corrected += 1
                time.sleep(0.25)
            except (ValueError, httpx.HTTPError):
                continue
    return {"exact_publication_records": corrected, "historical_training_eligible": False}

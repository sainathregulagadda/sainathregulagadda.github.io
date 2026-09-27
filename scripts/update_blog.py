#!/usr/bin/env python3
"""Refresh the public validation and quality briefing from official sources."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape

SOURCE_URL = "https://www.fda.gov/medical-devices/medical-devices-news-and-events/cdrh-new-news-and-updates"
ALLOWED_HOSTS = {"fda.gov", "www.fda.gov", "federalregister.gov", "www.federalregister.gov"}
KEYWORDS = (
    "validation", "quality", "software", "digital health", "artificial intelligence",
    "machine learning", "cybersecurity", "data integrity", "risk management",
    "human factors", "guidance", "standard", "computerized", "electronic submission",
    "qmsr", "quality management system",
)


class NewsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_heading = False
        self.in_link = False
        self.heading_parts: list[str] = []
        self.link_parts: list[str] = []
        self.href = ""
        self.current_date: datetime | None = None
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h2":
            self.in_heading = True
            self.heading_parts = []
        elif tag == "a":
            self.in_link = True
            self.link_parts = []
            self.href = dict(attrs).get("href") or ""

    def handle_data(self, data: str) -> None:
        if self.in_heading:
            self.heading_parts.append(data)
        if self.in_link:
            self.link_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2" and self.in_heading:
            text = clean_text(" ".join(self.heading_parts))
            try:
                self.current_date = datetime.strptime(text, "%B %d, %Y")
            except ValueError:
                pass
            self.in_heading = False
        elif tag == "a" and self.in_link:
            title = clean_text(" ".join(self.link_parts))
            if title and self.href and self.current_date:
                self.links.append({
                    "title": title,
                    "href": self.href,
                    "date": self.current_date.strftime("%Y-%m-%d"),
                })
            self.in_link = False


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_allowed(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS


def classify(title: str) -> str:
    lower = title.lower()
    if any(word in lower for word in ("software", "digital", "artificial intelligence", "machine learning", "cyber")):
        return "Digital health"
    if any(word in lower for word in ("quality", "qms", "validation", "risk", "standard", "human factors")):
        return "Quality systems"
    return "FDA guidance"


def candidate_entries(html: str) -> list[dict[str, str]]:
    parser = NewsParser()
    parser.feed(html)
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parser.links:
        title = clean_text(item["title"])
        if not any(keyword in title.lower() for keyword in KEYWORDS):
            continue
        url = urljoin(SOURCE_URL, item["href"])
        if not is_allowed(url) or url in seen:
            continue
        seen.add(url)
        date = datetime.strptime(item["date"], "%Y-%m-%d")
        found.append({
            "date": item["date"],
            "displayDate": date.strftime("%b %-d, %Y") if sys.platform != "win32" else date.strftime("%b %#d, %Y"),
            "title": title,
            "summary": "FDA CDRH posted or updated this public resource. Review the source for its status, scope, and applicability to your quality or validation process.",
            "url": url,
            "source": "FDA CDRH",
            "topic": classify(title),
        })
    return sorted(found, key=lambda entry: entry["date"], reverse=True)


def render_feed(entries: list[dict[str, str]]) -> str:
    items = []
    for entry in entries[:20]:
        published = datetime.strptime(entry["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        items.append(
            "    <item>\n"
            f"      <title>{escape(entry['title'])}</title>\n"
            f"      <link>{escape(entry['url'])}</link>\n"
            f"      <guid isPermaLink=\"true\">{escape(entry['url'])}</guid>\n"
            f"      <pubDate>{format_datetime(published)}</pubDate>\n"
            f"      <description>{escape(entry['summary'])}</description>\n"
            "    </item>"
        )
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<rss version=\"2.0\">\n  <channel>\n"
        "    <title>Sainath Regulagadda — Validation &amp; Quality Briefing</title>\n"
        "    <link>https://sainathregulagadda.github.io/blog/</link>\n"
        "    <description>Source-linked public updates relevant to validation and quality.</description>\n"
        "    <language>en-us</language>\n"
        + "\n".join(items)
        + "\n  </channel>\n</rss>\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-file", type=Path)
    parser.add_argument("--entries-path", type=Path, default=Path("blog/data/entries.json"))
    parser.add_argument("--feed-path", type=Path, default=Path("blog/feed.xml"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.source_file:
        html = args.source_file.read_text(encoding="utf-8")
    else:
        request = Request(SOURCE_URL, headers={"User-Agent": "SainathRegulagaddaBriefing/1.0 (+https://sainathregulagadda.github.io/blog/)"})
        with urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", errors="replace")

    data = json.loads(args.entries_path.read_text(encoding="utf-8"))
    existing_urls = {entry["url"] for entry in data.get("entries", [])}
    additions = [entry for entry in candidate_entries(html) if entry["url"] not in existing_urls][:8]
    print(f"Found {len(additions)} new relevant item(s).")
    if args.dry_run or not additions:
        return 0

    entries = sorted(data.get("entries", []) + additions, key=lambda entry: entry["date"], reverse=True)[:80]
    data["entries"] = entries
    data["generatedAt"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    args.entries_path.parent.mkdir(parents=True, exist_ok=True)
    args.feed_path.parent.mkdir(parents=True, exist_ok=True)
    args.entries_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.feed_path.write_text(render_feed(entries), encoding="utf-8")
    print(f"Added {len(additions)} new blog entr{'y' if len(additions) == 1 else 'ies'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

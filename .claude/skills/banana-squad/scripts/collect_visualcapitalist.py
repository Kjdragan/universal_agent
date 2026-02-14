#!/usr/bin/env python3
"""
Collect a small capped set of VisualCapitalist "style inspiration" images + attribution metadata.

This is intentionally conservative:
- capped downloads (default <= 50)
- rate-limited (sleep between requests)
- stores attribution metadata in `sources.json`

Run:
  uv run .claude/skills/banana-squad/scripts/collect_visualcapitalist.py --help
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


BASE = "https://www.visualcapitalist.com"
ROBOTS = urllib.parse.urljoin(BASE, "/robots.txt")
SITEMAP_INDEX = urllib.parse.urljoin(BASE, "/sitemap_index.xml")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _http_get(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "universal_agent_banana_squad/1.0 (+local style inspiration collector)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_head(url: str, timeout: float = 30.0) -> dict[str, str]:
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "universal_agent_banana_squad/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return {k.lower(): v for k, v in resp.headers.items()}


def _parse_robots_disallows(robots_txt: str) -> list[str]:
    disallows: list[str] = []
    ua_star = False
    for raw in robots_txt.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("user-agent:"):
            ua = line.split(":", 1)[1].strip()
            ua_star = (ua == "*")
            continue
        if ua_star and line.lower().startswith("disallow:"):
            path = line.split(":", 1)[1].strip() or "/"
            disallows.append(path)
    return disallows


def _is_path_allowed(path: str, disallows: list[str]) -> bool:
    # Very simple robots handling (good enough for a conservative collector).
    if not path.startswith("/"):
        path = "/" + path
    for d in disallows:
        if d == "/":
            return False
        if d and path.startswith(d):
            return False
    return True


def _parse_sitemap_urls(xml_bytes: bytes) -> list[str]:
    root = ET.fromstring(xml_bytes)
    urls: list[str] = []
    # Handle both sitemapindex and urlset.
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag == "loc" and elem.text:
            urls.append(elem.text.strip())
    return urls


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.og_image: str | None = None
        self.og_title: str | None = None
        self.canonical: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta" and tag.lower() != "link":
            return
        d = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "meta":
            prop = d.get("property", "").lower()
            name = d.get("name", "").lower()
            content = d.get("content", "")
            if prop == "og:image" and content and not self.og_image:
                self.og_image = content
            if prop == "og:title" and content and not self.og_title:
                self.og_title = content
            if name == "title" and content and not self.og_title:
                self.og_title = content
        if tag.lower() == "link":
            rel = d.get("rel", "").lower()
            href = d.get("href", "")
            if rel == "canonical" and href and not self.canonical:
                self.canonical = href


def _extract_article_meta(html_bytes: bytes) -> tuple[str | None, str | None, str | None]:
    parser = _MetaParser()
    try:
        parser.feed(html_bytes.decode("utf-8", errors="ignore"))
    except Exception:
        return None, None, None
    return parser.og_image, parser.og_title, parser.canonical


def _guess_ext_from_headers(headers: dict[str, str], url: str) -> str:
    ct = headers.get("content-type", "").split(";")[0].strip().lower()
    if ct == "image/jpeg":
        return ".jpg"
    if ct == "image/png":
        return ".png"
    if ct == "image/webp":
        return ".webp"
    # Fallback: use URL path extension if present.
    path = urllib.parse.urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".img"


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


@dataclass
class SourceItem:
    url: str
    title: str | None
    image_url: str
    saved_as: str
    collected_at: str


def main() -> int:
    ap = argparse.ArgumentParser(description="Collect VisualCapitalist style inspiration images (capped).")
    ap.add_argument("--max-images", type=int, default=40, help="Max images to download (1-50).")
    ap.add_argument("--sleep-seconds", type=float, default=1.0, help="Sleep between requests.")
    ap.add_argument("--seed", type=int, default=1337, help="Sampling seed for variety.")
    ap.add_argument(
        "--out-dir",
        default="Banana_Squad/reference_images/style_inspiration/visualcapitalist/downloads",
        help="Directory to write downloaded images.",
    )
    ap.add_argument(
        "--sources-file",
        default="Banana_Squad/reference_images/style_inspiration/visualcapitalist/sources.json",
        help="JSON metadata output file.",
    )
    args = ap.parse_args()

    max_images = max(1, min(int(args.max_images), 50))
    sleep_s = max(0.0, float(args.sleep_seconds))
    random.seed(int(args.seed))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sources_path = Path(args.sources_file)
    sources_path.parent.mkdir(parents=True, exist_ok=True)

    # Robots check (conservative)
    robots_txt = _http_get(ROBOTS).decode("utf-8", errors="ignore")
    disallows = _parse_robots_disallows(robots_txt)
    if not _is_path_allowed("/sitemap_index.xml", disallows):
        raise SystemExit("Robots policy disallows sitemap access; refusing to proceed.")

    # Build candidate article URL list from sitemap index.
    index_xml = _http_get(SITEMAP_INDEX)
    sitemap_urls = [u for u in _parse_sitemap_urls(index_xml) if u.endswith(".xml")]

    # Prefer post/page sitemaps; fall back to any.
    preferred = [u for u in sitemap_urls if re.search(r"(post|page).*sitemap", u)]
    if preferred:
        sitemap_urls = preferred

    random.shuffle(sitemap_urls)
    sitemap_urls = sitemap_urls[: min(8, len(sitemap_urls))]

    article_urls: list[str] = []
    for sm in sitemap_urls:
        time.sleep(sleep_s)
        if not _is_path_allowed(urllib.parse.urlparse(sm).path, disallows):
            continue
        try:
            sm_xml = _http_get(sm)
        except Exception:
            continue
        for u in _parse_sitemap_urls(sm_xml):
            if u.startswith(BASE) and "/author/" not in u:
                article_urls.append(u)

    # De-dupe and sample for variety
    article_urls = list(dict.fromkeys(article_urls))
    random.shuffle(article_urls)
    article_urls = article_urls[: min(400, len(article_urls))]

    downloaded: list[SourceItem] = []
    seen_images: set[str] = set()

    for url in article_urls:
        if len(downloaded) >= max_images:
            break

        time.sleep(sleep_s)
        path = urllib.parse.urlparse(url).path
        if not _is_path_allowed(path, disallows):
            continue

        try:
            html = _http_get(url)
        except Exception:
            continue
        img_url, title, canonical = _extract_article_meta(html)
        if not img_url:
            continue
        if img_url in seen_images:
            continue

        # Normalize image url
        img_url = urllib.parse.urljoin(url, img_url)
        img_path = urllib.parse.urlparse(img_url).path
        if not _is_path_allowed(img_path, disallows):
            continue

        try:
            headers = _http_head(img_url)
        except Exception:
            continue
        if not headers.get("content-type", "").lower().startswith("image/"):
            continue

        ext = _guess_ext_from_headers(headers, img_url)
        # Stable filename by image URL hash; avoids duplicates.
        fn = f"vc_{_sha1(img_url)[:12]}{ext}"
        dest = out_dir / fn
        if dest.exists():
            seen_images.add(img_url)
            downloaded.append(
                SourceItem(
                    url=canonical or url,
                    title=title,
                    image_url=img_url,
                    saved_as=str(dest),
                    collected_at=_utc_now_iso(),
                )
            )
            continue

        try:
            img_bytes = _http_get(img_url)
        except Exception:
            continue
        # Hard cap individual downloads (~12MB) to avoid huge payloads.
        if len(img_bytes) > 12 * 1024 * 1024:
            continue

        dest.write_bytes(img_bytes)
        seen_images.add(img_url)
        downloaded.append(
            SourceItem(
                url=canonical or url,
                title=title,
                image_url=img_url,
                saved_as=str(dest),
                collected_at=_utc_now_iso(),
            )
        )

    sources_obj = {
        "version": 1,
        "collected_at": _utc_now_iso(),
        "site": BASE,
        "max_images": max_images,
        "download_dir": str(out_dir),
        "items": [
            {
                "url": it.url,
                "title": it.title,
                "image_url": it.image_url,
                "saved_as": it.saved_as,
                "collected_at": it.collected_at,
            }
            for it in downloaded
        ],
    }
    sources_path.write_text(json.dumps(sources_obj, indent=2) + "\n", encoding="utf-8")

    print(f"downloaded={len(downloaded)} sources={sources_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


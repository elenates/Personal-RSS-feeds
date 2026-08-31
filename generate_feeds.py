from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin
from xml.etree.ElementTree import Element, SubElement, ElementTree

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser


USER_AGENT = (
    "Mozilla/5.0 (compatible; PersonalRSSFeed/1.0; "
    "+https://github.com/elenates/Personal-RSS-feeds)"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "cs,en;q=0.8",
}


@dataclass
class Item:
    title: str
    url: str
    description: str = ""
    published: datetime | None = None


def fetch(url: str) -> BeautifulSoup:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def absolute(base: str, url: str) -> str:
    return urljoin(base, url)


def guid(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def parse_date(text: str) -> datetime | None:
    if not text:
        return None

    try:
        result = date_parser.parse(
            clean(text),
            dayfirst=True,
            fuzzy=True,
        )

        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)

        return result.astimezone(timezone.utc)

    except (ValueError, OverflowError):
        return None


def add_item(
    items: list[Item],
    title: str,
    url: str,
    description: str = "",
    published: datetime | None = None,
):
    title = clean(title)
    url = url.strip()

    if not title or not url:
        return

    if url.startswith("#"):
        return

    if url.lower().startswith("javascript:"):
        return

    if any(item.url == url for item in items):
        return

    items.append(
        Item(
            title=title,
            url=url,
            description=clean(description),
            published=published,
        )
    )


# ---------------------------------------------------------------------------
# MZV
# ---------------------------------------------------------------------------

def parse_mzv() -> tuple[str, str, list[Item]]:
    source = (
        "https://mzv.gov.cz/jnp/cz/"
        "informace_pro_cizince/aktuality/index.html"
    )

    soup = fetch(source)
    items: list[Item] = []

    article_pattern = re.compile(
        r"/informace_pro_cizince/aktuality/"
        r"[^/?#]+\.html$",
        re.IGNORECASE,
    )

    for link in soup.find_all("a", href=True):

        href = absolute(source, link["href"]).split("#")[0]

        if not article_pattern.search(href):
            continue

        if href.endswith("/index.html"):
            continue

        title = clean(link.get_text(" ", strip=True))

        if not title:
            continue

        try:
            article = fetch(href)
        except requests.RequestException:
            continue

        # MZV has navigation headings before the actual article H1.
        # Find the H1 that matches the article title or the page URL.
        article_title = ""

        for heading in article.find_all("h1"):

            candidate = clean(
                heading.get_text(" ", strip=True)
            )

            if not candidate:
                continue

            if candidate.lower() == title.lower():
                article_title = candidate
                break

            # Reject obvious navigation/interface headings.
            if candidate.lower() in {
                "jazyk",
                "hledat",
                "přejít na obsah",
                "přejít na menu",
            }:
                continue

            # A real article H1 is normally a reasonably short title.
            if len(candidate) < 200:
                article_title = candidate
                break

        if not article_title:
            article_title = title

        article_text = clean(
            article.get_text(" ", strip=True)
        )

        # Published/updated date.
        updated_match = re.search(
            r"Aktualizováno:\s*"
            r"(\d{1,2}\.\d{1,2}\.\d{4})"
            r"\s*/\s*(\d{1,2}:\d{2})",
            article_text,
            re.IGNORECASE,
        )

        published_match = re.search(
            r"(\d{1,2}\.\d{1,2}\.\d{4})"
            r"\s*/\s*(\d{1,2}:\d{2})",
            article_text,
        )

        if updated_match:
            date_text = (
                f"{updated_match.group(1)} "
                f"{updated_match.group(2)}"
            )
        elif published_match:
            date_text = (
                f"{published_match.group(1)} "
                f"{published_match.group(2)}"
            )
        else:
            date_text = ""

        published = parse_date(date_text)

        # Main article content.
        description = ""

        for selector in [
            "#content",
            ".content",
            "#page_content",
            ".article",
            ".article-content",
        ]:

            node = article.select_one(selector)

            if node:
                candidate = clean(
                    node.get_text(" ", strip=True)
                )

                if len(candidate) > len(description):
                    description = candidate

        if not description:
            description = article_text

        # Remove the breadcrumb/title/date prefix when possible.
        if article_title in description:
            description = description.split(
                article_title,
                1
            )[1]

        description = clean(description)

        add_item(
            items,
            article_title,
            href,
            description[:3000],
            published,
        )

    print(f"MZV: found {len(items)} items")

    return (
        "MZV - Aktuality pro cizince",
        source,
        items[:50],
    )


# ---------------------------------------------------------------------------
# TMBK / Seznam Zprávy
# ---------------------------------------------------------------------------

def parse_tmbk() -> tuple[str, str, list[Item]]:
    source = "https://www.seznamzpravy.cz/autor/tmbk-1312"

    soup = fetch(source)
    items: list[Item] = []

    for link in soup.find_all("a", href=True):

        title = clean(link.get_text(" ", strip=True))

        if not title.startswith("TMBK:"):
            continue

        href = absolute(source, link["href"])

        if "seznamzpravy.cz" not in href:
            continue

        container = link

        for _ in range(5):
            if not container.parent:
                break

            container = container.parent
            text = clean(container.get_text(" ", strip=True))

            if len(text) > len(title) + 20:
                break

        date_match = re.search(
            r"\d{1,2}\.\s*\d{1,2}\.\s*\d{4}"
            r"(?:\s+\d{1,2}:\d{2})?",
            text,
        )

        published = (
            parse_date(date_match.group(0))
            if date_match
            else None
        )

        add_item(
            items,
            title,
            href,
            "",
            published,
        )

    print(f"TMBK: found {len(items)} items")

    return (
        "TMBK - Seznam Zprávy",
        source,
        items[:50],
    )


# ---------------------------------------------------------------------------
# Skalní mlýn
# ---------------------------------------------------------------------------

def parse_skalni_mlyn() -> tuple[str, str, list[Item]]:
    source = "https://www.skalnimlyn.cz/akce-a-novinky"

    soup = fetch(source)
    items: list[Item] = []

    # The event section starts with "Nadcházející akce".
    heading = None

    for h in soup.find_all(["h2", "h3"]):
        if clean(h.get_text(" ", strip=True)) == "Nadcházející akce":
            heading = h
            break

    if not heading:
        print("Skalní mlýn: event section not found")
        return (
            "Hotel Skalní mlýn - Nadcházející akce",
            source,
            [],
        )

    # In the current Webflow page each event is represented by:
    #
    #   day
    #   month/year
    #   title
    #   time
    #   location
    #   description
    #
    # We find the date markers and then take the following meaningful
    # text as the title.

    date_pattern = re.compile(
        r"^\d{1,2}/20\d{2}$"
    )

    current = heading

    while current:

        current = current.find_next()

        if not current:
            break

        text = clean(
            current.get_text(" ", strip=True)
        )

        # Stop when we reach the "Novinky" section.
        if text == "Novinky":
            break

        if not date_pattern.fullmatch(text):
            continue

        month_year = text

        # The previous element contains the day number.
        previous = current.find_previous()

        day = clean(
            previous.get_text(" ", strip=True)
        ) if previous else ""

        if not re.fullmatch(r"\d{1,2}", day):
            continue

        # The title is the next reasonably short text element.
        title = ""
        title_node = None

        node = current.find_next()

        for _ in range(15):

            if not node:
                break

            candidate = clean(
                node.get_text(" ", strip=True)
            )

            if (
                candidate
                and candidate != month_year
                and not re.fullmatch(
                    r"\d{1,2}",
                    candidate
                )
                and not re.fullmatch(
                    r"\d{1,2}:\d{2}",
                    candidate
                )
                and len(candidate) <= 150
            ):
                title = candidate
                title_node = node
                break

            node = node.find_next()

        if not title:
            continue

        if any(item.title == title for item in items):
            continue

        month, year = month_year.split("/")

        published = parse_date(
            f"{day}.{month}.{year}"
        )

        # Find the surrounding event card.
        container = title_node
        description = ""

        for _ in range(10):

            if not container:
                break

            candidate = clean(
                container.get_text(" ", strip=True)
            )

            if (
                len(candidate) > len(title) + 80
                and len(candidate) < 5000
            ):
                description = candidate
                break

            container = container.parent

        if title in description:
            description = description.replace(
                title,
                "",
                1,
            )

        description = clean(description)

        add_item(
            items,
            title,
            source,
            description[:2500],
            published,
        )

    print(f"Skalní mlýn: found {len(items)} items")

    return (
        "Hotel Skalní mlýn - Nadcházející akce",
        source,
        items[:50],
    )


# ---------------------------------------------------------------------------
# Svět energie
# ---------------------------------------------------------------------------

def parse_svet_energie() -> tuple[str, str, list[Item]]:
    source = "https://www.svetenergie.cz/cs/kalendar-akci"

    soup = fetch(source)
    items: list[Item] = []

    for link in soup.find_all("a", href=True):

        label = clean(link.get_text(" ", strip=True))

        if label.upper() != "ZJISTIT VÍCE":
            continue

        href = absolute(source, link["href"])

        if href == source:
            continue

        container = link

        for _ in range(8):
            if not container.parent:
                break

            container = container.parent

            text = clean(
                container.get_text(" ", strip=True)
            )

            if len(text) > 100:
                break

        title = ""

        for heading in container.find_all(
            ["h2", "h3", "h4"]
        ):

            candidate = clean(
                heading.get_text(" ", strip=True)
            )

            if candidate:
                title = candidate
                break

        if not title:
            continue

        date_patterns = [
            r"\d{1,2}\.\s*\d{1,2}\.\s*[-–]\s*"
            r"\d{1,2}\.\s*\d{1,2}\.\s*\d{4}",

            r"\d{1,2}\.\s*\d{1,2}\.\s*\d{4}",
        ]

        date_text = ""

        for pattern in date_patterns:

            match = re.search(pattern, text)

            if match:
                date_text = match.group(0)
                break

        published = parse_date(date_text)

        description = re.sub(
            r"ZJISTIT VÍCE",
            "",
            text,
            flags=re.IGNORECASE,
        )

        description = description.replace(
            title,
            "",
            1,
        )

        add_item(
            items,
            title,
            href,
            description[:2500],
            published,
        )

    print(f"Svět energie: found {len(items)} items")

    return (
        "Svět energie - Kalendář akcí",
        source,
        items[:50],
    )


# ---------------------------------------------------------------------------
# RSS writer
# ---------------------------------------------------------------------------

def write_feed(
    path: Path,
    title: str,
    source: str,
    items: list[Item],
):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    items = sorted(
        items,
        key=lambda item: (
            item.published
            or datetime.min.replace(
                tzinfo=timezone.utc
            )
        ),
        reverse=True,
    )

    rss = Element(
        "rss",
        {"version": "2.0"},
    )

    channel = SubElement(
        rss,
        "channel",
    )

    SubElement(
        channel,
        "title",
    ).text = title

    SubElement(
        channel,
        "link",
    ).text = source

    SubElement(
        channel,
        "description",
    ).text = "Generated personal RSS feed"

    SubElement(
        channel,
        "generator",
    ).text = "Personal RSS Feeds"

    for item in items:

        element = SubElement(
            channel,
            "item",
        )

        SubElement(
            element,
            "title",
        ).text = item.title

        SubElement(
            element,
            "link",
        ).text = item.url

        SubElement(
            element,
            "guid",
            {"isPermaLink": "false"},
        ).text = guid(item.url)

        if item.description:

            SubElement(
                element,
                "description",
            ).text = item.description

        if item.published:

            SubElement(
                element,
                "pubDate",
            ).text = format_datetime(
                item.published
            )

    ElementTree(rss).write(
        path,
        encoding="utf-8",
        xml_declaration=True,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():

    output = Path("docs/feeds")

    parsers = [
        ("mzv", parse_mzv),
        ("tmbk", parse_tmbk),
        ("skalni-mlyn", parse_skalni_mlyn),
        ("svet-energie", parse_svet_energie),
    ]

    for name, parser in parsers:

        print()
        print("=" * 60)
        print(name)
        print("=" * 60)

        try:

            title, source, items = parser()

            path = output / f"{name}.xml"

            write_feed(
                path,
                title,
                source,
                items,
            )

            print(f"Written: {path}")
            print(f"Items: {len(items)}")

        except Exception as error:

            print(
                f"ERROR while processing {name}: "
                f"{type(error).__name__}: {error}"
            )

            raise


if __name__ == "__main__":
    main()

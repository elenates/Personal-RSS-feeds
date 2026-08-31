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

    # MZV's listing page contains the real articles as h2 > a.
    # We deliberately use the listing page itself instead of trying
    # to discover articles among all navigation/mobile links.
    for heading in soup.find_all(["h2", "h3"]):
        link = heading.find("a", href=True)

        if not link:
            continue

        title = clean(link.get_text(" ", strip=True))
        raw_href = link.get("href", "").strip()

        if not title or not raw_href:
            continue

        # Ignore generic navigation/interface links.
        if title.lower() in {
            "přejít na obsah",
            "přejít na menu",
            "jazyk",
            "hledat",
        }:
            continue

        # Build the absolute URL.
        href = absolute(source, raw_href)

        # Remove fragments and query strings.
        href = href.split("#", 1)[0]
        href = href.split("?", 1)[0]

        # We only want actual article pages.
        # MZV also has pagination/navigation links such as:
        #   index.mobi?page=4
        #   index.html?page=4
        #   index$2548.mobi
        #
        # These are not articles and must never become RSS items.
        if "/informace_pro_cizince/aktuality/" not in href:
            continue

        if not href.lower().endswith(".html"):
            continue

        # Never accept the listing/index page itself.
        filename = href.rsplit("/", 1)[-1].lower()

        if filename in {
            "index.html",
            "index.htm",
        }:
            continue

        # Article links should have a real article slug.
        # This also protects us from navigation pages.
        if filename.startswith("index"):
            continue

        # Do not accidentally include the listing page itself.
        if href.rstrip("/") == source.rstrip("/"):
            continue

        # ------------------------------------------------------------------
        # The listing page already contains the article date and summary.
        # Find the nearest useful container around the h2/h3.
        # ------------------------------------------------------------------

        container = heading.parent
        best_text = ""

        for _ in range(5):
            if not container:
                break

            text = clean(
                container.get_text(" ", strip=True)
            )

            # We want a reasonably sized article card, not the whole page.
            if len(text) >= len(title) + 20:
                best_text = text

            if len(text) >= 100:
                break

            container = container.parent

        if not best_text:
            best_text = clean(
                heading.parent.get_text(" ", strip=True)
                if heading.parent
                else ""
            )

        # ------------------------------------------------------------------
        # Date
        # ------------------------------------------------------------------

        updated_match = re.search(
            r"Aktualizováno:\s*"
            r"(\d{1,2}\.\d{1,2}\.\d{4})"
            r"\s*/\s*(\d{1,2}:\d{2})",
            best_text,
            re.IGNORECASE,
        )

        published_match = re.search(
            r"(\d{1,2}\.\d{1,2}\.\d{4})"
            r"\s*/\s*(\d{1,2}:\d{2})",
            best_text,
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

        # ------------------------------------------------------------------
        # Description
        # ------------------------------------------------------------------

        description = best_text

        # Remove the title from the beginning.
        if description.startswith(title):
            description = description[len(title):].strip()

        # Remove date information from the beginning.
        description = re.sub(
            r"^\d{1,2}\.\d{1,2}\.\d{4}\s*/\s*\d{1,2}:\d{2}"
            r"\s*\|\s*Aktualizováno:\s*"
            r"\d{1,2}\.\d{1,2}\.\d{4}\s*/\s*\d{1,2}:\d{2}",
            "",
            description,
            flags=re.IGNORECASE,
        )

        # Remove "více ►" if it is included in the card text.
        description = re.sub(
            r"\s*více\s*►\s*$",
            "",
            description,
            flags=re.IGNORECASE,
        )

        description = clean(description)

        add_item(
            items,
            title,
            href,
            description[:5000],
            published,
        )

    # Remove duplicate URLs.
    unique_items = []
    seen_urls = set()

    for item in items:
        if item.url in seen_urls:
            continue

        seen_urls.add(item.url)
        unique_items.append(item)

    items = unique_items

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

    # Find the "Nadcházející akce" heading.
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

    # Get everything from "Nadcházející akce" until "Novinky".
    #
    # We deliberately use text with newline separators instead of
    # walking through individual HTML elements. The page is a Webflow
    # page where event cards contain many nested elements, and walking
    # the DOM produces duplicate/incorrect text nodes.

    lines = []

    node = heading

    while True:
        node = node.find_next()

        if not node:
            break

        text = clean(node.get_text(" ", strip=True))

        if text == "Novinky":
            break

        # Only collect leaf-ish textual elements.
        if not node.find_all():
            if text:
                lines.append(text)

    # The previous method may still produce duplicates because of the
    # Webflow structure. A more reliable source is the text of the
    # parent section containing the heading.

    section = heading.parent

    while section and len(
        clean(section.get_text(" ", strip=True))
    ) < 500:
        section = section.parent

    if not section:
        print("Skalní mlýn: event container not found")
        return (
            "Hotel Skalní mlýn - Nadcházející akce",
            source,
            [],
        )

    # Extract visible text using newline separators.
    text_lines = [
        clean(line)
        for line in section.get_text(
            "\n",
            strip=True,
        ).splitlines()
        if clean(line)
    ]

    # Find "Nadcházející akce" in the text and discard everything
    # before it.
    try:
        start = text_lines.index("Nadcházející akce") + 1
        text_lines = text_lines[start:]
    except ValueError:
        pass

    # Stop before "Novinky".
    if "Novinky" in text_lines:
        text_lines = text_lines[
            :text_lines.index("Novinky")
        ]

    month_pattern = re.compile(
        r"^(0?[1-9]|1[0-2])/20\d{2}$"
    )

    day_pattern = re.compile(
        r"^\d{1,2}$"
    )

    i = 0

    while i < len(text_lines) - 1:

        day = text_lines[i]
        month_year = text_lines[i + 1]

        if not (
            day_pattern.fullmatch(day)
            and month_pattern.fullmatch(month_year)
        ):
            i += 1
            continue

        # Expected structure:
        #
        # day
        # month/year
        # title
        # time
        # location
        # description...
        #
        if i + 2 >= len(text_lines):
            break

        title = text_lines[i + 2]

        if not title:
            i += 2
            continue

        # Collect everything until the next day/month pair.
        event_lines = []

        j = i + 3

        while j < len(text_lines):

            if (
                j + 1 < len(text_lines)
                and day_pattern.fullmatch(text_lines[j])
                and month_pattern.fullmatch(
                    text_lines[j + 1]
                )
            ):
                break

            event_lines.append(text_lines[j])
            j += 1

        if not event_lines:
            i = j
            continue

        # First line = time
        # Second line = location
        # Remaining lines = description.
        time_text = (
            event_lines[0]
            if len(event_lines) >= 1
            else ""
        )

        location = (
            event_lines[1]
            if len(event_lines) >= 2
            else ""
        )

        description_parts = []

        if time_text:
            description_parts.append(time_text)

        if location:
            description_parts.append(location)

        if len(event_lines) > 2:
            description_parts.extend(
                event_lines[2:]
            )

        description = clean(
            " ".join(description_parts)
        )

        month, year = month_year.split("/")

        published = parse_date(
            f"{day}.{month}.{year}"
        )

        add_item(
            items,
            title,
            source,
            description[:3000],
            published,
        )

        i = j

    # Remove duplicate events.
    unique_items = []
    seen_titles = set()

    for item in items:

        if item.title in seen_titles:
            continue

        seen_titles.add(item.title)
        unique_items.append(item)

    items = unique_items

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

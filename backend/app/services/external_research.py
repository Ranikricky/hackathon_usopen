"""
External research discovery for graph and agent context.

The graph is durable memory, not an oracle. This service collects a small,
auditable research packet from broad web sources so ontology generation,
graph build, and agent setup can start from more than the user prompt.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from html.parser import HTMLParser
from typing import Dict, List, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

from ..config import Config
from ..services.text_processor import TextProcessor
from ..utils.logger import get_logger

logger = get_logger("horizonxl.external_research")


@dataclass
class ResearchSource:
    """A single external source pointer plus a short extracted text sample."""

    query: str
    title: str
    url: str
    source_type: str
    snippet: str = ""
    extracted_text: str = ""
    caveat: str = "External web result. Treat as provisional until checked for date, source quality, and future leakage."

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


class _SearchResultParser(HTMLParser):
    """Extract links from DuckDuckGo's HTML endpoint without extra deps."""

    def __init__(self):
        super().__init__()
        self.results: List[Dict[str, str]] = []
        self._current_href: Optional[str] = None
        self._current_text: List[str] = []
        self._capture = False

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrs_dict = dict(attrs)
        href = attrs_dict.get("href", "")
        klass = attrs_dict.get("class", "")
        if "result__a" in klass or "/l/?" in href:
            self._current_href = href
            self._current_text = []
            self._capture = True

    def handle_data(self, data):
        if self._capture:
            text = data.strip()
            if text:
                self._current_text.append(text)

    def handle_endtag(self, tag):
        if tag != "a" or not self._capture:
            return
        title = html.unescape(" ".join(self._current_text)).strip()
        url = _normalize_search_url(self._current_href or "")
        if title and url:
            self.results.append({"title": title, "url": url})
        self._current_href = None
        self._current_text = []
        self._capture = False


class _ReadableTextParser(HTMLParser):
    """Extract readable text from article/blog/research pages."""

    def __init__(self):
        super().__init__()
        self._parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg", "nav", "footer"}:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg", "nav", "footer"} and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag in {"p", "br", "div", "li", "section", "article", "tr", "h1", "h2", "h3", "h4"}:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        cleaned = data.strip()
        if cleaned:
            self._parts.append(cleaned)
            self._parts.append(" ")

    def get_text(self) -> str:
        return TextProcessor.preprocess_text("".join(self._parts))


def _normalize_search_url(href: str) -> str:
    """Resolve DuckDuckGo redirect URLs into their destination URL."""
    if not href:
        return ""
    decoded = html.unescape(href)
    if decoded.startswith("//"):
        decoded = f"https:{decoded}"
    parsed = urlparse(decoded)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return decoded
    return ""


def _source_type(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "reddit.com" in host:
        return "reddit"
    if "arxiv.org" in host or url.lower().endswith(".pdf"):
        return "research_paper"
    if any(name in host for name in ["news", "times", "bbc", "reuters", "apnews", "guardian", "hindu", "telegraph", "indianexpress"]):
        return "news"
    if any(name in host for name in ["substack", "medium", "blog"]):
        return "blog"
    return "web"


def _is_safe_public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = parsed.hostname or ""
    blocked = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
    if host.lower() in blocked:
        return False
    if host.endswith(".local") or host.startswith("10.") or host.startswith("192.168."):
        return False
    if re.match(r"^172\.(1[6-9]|2\d|3[0-1])\.", host):
        return False
    return True


class ExternalResearchService:
    """Collect small, auditable research packets for simulation context."""

    def __init__(
        self,
        enabled: Optional[bool] = None,
        max_queries: Optional[int] = None,
        max_results: Optional[int] = None,
        max_chars_per_source: int = 1400,
    ):
        self.enabled = Config.EXTERNAL_RESEARCH_ENABLED if enabled is None else enabled
        self.max_queries = max_queries or Config.EXTERNAL_RESEARCH_MAX_QUERIES
        self.max_results = max_results or Config.EXTERNAL_RESEARCH_MAX_RESULTS
        self.max_chars_per_source = max_chars_per_source

    def collect(self, prompt: str, additional_context: str = "") -> Dict[str, object]:
        """Discover and fetch a bounded research packet."""
        if not self.enabled:
            return {"enabled": False, "queries": [], "sources": [], "markdown": ""}

        queries = self.build_queries(prompt, additional_context)[: self.max_queries]
        sources: List[ResearchSource] = []
        seen_urls = set()

        for query in queries:
            for result in self.search(query, max_results=max(2, self.max_results)):
                url = result.get("url", "")
                if not url or url in seen_urls or not _is_safe_public_url(url):
                    continue
                seen_urls.add(url)
                text = self.fetch_text(url)
                sources.append(
                    ResearchSource(
                        query=query,
                        title=result.get("title", url),
                        url=url,
                        source_type=_source_type(url),
                        extracted_text=text[: self.max_chars_per_source],
                    )
                )
                if len(sources) >= self.max_results:
                    break
            if len(sources) >= self.max_results:
                break

        markdown = self.to_markdown(sources, queries, prompt)
        return {
            "enabled": True,
            "queries": queries,
            "sources": [source.to_dict() for source in sources],
            "markdown": markdown,
        }

    def build_queries(self, prompt: str, additional_context: str = "") -> List[str]:
        """Create source-diverse searches from the user question.

        Keep this generic. Domain-specific terms should come from the prompt or
        uploaded context itself, not from hardcoded query branches.
        """
        text = TextProcessor.preprocess_text(f"{prompt or ''}\n{additional_context or ''}")
        compact = " ".join(text.split())[:180]
        base = compact or "future scenario analysis"
        queries = [
            f"{base} latest evidence data numbers source",
            f"{base} research paper literature review",
            f"{base} news analysis recent context",
            f"{base} expert blog analysis",
            f"{base} site:reddit.com public discussion",
            f"{base} dataset statistics historical context",
        ]

        cutoff = self._extract_cutoff_hint(text)
        if cutoff:
            queries = [f"{query} before:{cutoff}" for query in queries]
        return queries

    def search(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """Search using DuckDuckGo's HTML endpoint as a no-key fallback."""
        try:
            search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
            req = Request(
                search_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; Horizon XL Research/1.0)",
                    "Accept": "text/html,*/*;q=0.8",
                },
            )
            with urlopen(req, timeout=6) as response:
                html_text = response.read(900_000).decode("utf-8", errors="ignore")
            parser = _SearchResultParser()
            parser.feed(html_text)
            return parser.results[:max_results]
        except Exception as exc:
            logger.warning("External search failed for query=%s error=%s", query, exc)
            return []

    def fetch_text(self, url: str) -> str:
        """Fetch a small readable text sample from a source URL."""
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; Horizon XL Research/1.0)",
                    "Accept": "text/html,text/plain,application/pdf;q=0.8,*/*;q=0.4",
                },
            )
            with urlopen(req, timeout=8) as response:
                raw = response.read(1_000_000)
                content_type = (response.headers.get("Content-Type") or "").lower()
                charset = response.headers.get_content_charset() or "utf-8"
            if not raw:
                return ""
            if "application/pdf" in content_type:
                return "[PDF source discovered; text extraction is not performed during lightweight web discovery.]"
            decoded = raw.decode(charset, errors="ignore")
            if "text/html" in content_type or "<html" in decoded[:2000].lower():
                parser = _ReadableTextParser()
                parser.feed(decoded)
                return parser.get_text()
            return TextProcessor.preprocess_text(decoded)
        except Exception as exc:
            logger.debug("External source fetch failed url=%s error=%s", url, exc)
            return ""

    def to_markdown(self, sources: List[ResearchSource], queries: List[str], prompt: str) -> str:
        """Render a research packet that can be fed into ontology/graph/agents."""
        if not sources:
            return ""
        lines = [
            "# External Research Packet",
            f"Generated at: {datetime.utcnow().isoformat()}Z",
            "",
            "Purpose: provisional source discovery for graph construction, agent background context, and debate truth-checking.",
            "Rule: this packet is not ground truth; agents must treat it as evidence to audit, compare, and challenge.",
            "",
            "## Discovery Queries",
        ]
        lines.extend([f"- {query}" for query in queries])
        lines.append("")
        lines.append("## Source Notes")
        for idx, source in enumerate(sources, start=1):
            excerpt = source.extracted_text or source.snippet or "No readable excerpt fetched."
            lines.extend([
                f"### Source {idx}: {source.title}",
                f"- URL: {source.url}",
                f"- Type: {source.source_type}",
                f"- Query: {source.query}",
                f"- Caveat: {source.caveat}",
                "",
                excerpt[: self.max_chars_per_source],
                "",
            ])
        return "\n".join(lines)

    def _extract_cutoff_hint(self, text: str) -> str:
        """Best-effort cutoff hint for blind simulations."""
        match = re.search(r"(?:up to|as of|before|cut[- ]?off[: ]+)\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
        if not match:
            return ""
        raw = match.group(1)
        try:
            if re.match(r"\d{4}-\d{2}-\d{2}", raw):
                return raw
            return datetime.strptime(raw, "%B %d, %Y").strftime("%Y-%m-%d")
        except Exception:
            return ""

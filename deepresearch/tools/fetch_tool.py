# deepresearch/tools/fetch_tool.py
# -*- coding: utf-8 -*-
"""
Fetcher tool:
- Fetch HTML and extract main text
- Fetch PDF and extract first few pages
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from ..schemas import Document


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


class SimpleFetcher:
    def __init__(self, timeout_s: float = 20.0, max_chars: int = 12000):
        self.timeout_s = timeout_s
        self.max_chars = max_chars

    async def fetch(self, url: str) -> Document:
        async with httpx.AsyncClient(
            timeout=self.timeout_s, follow_redirects=True, verify=False
        ) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
            try:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
            except Exception as e:
                print(f"[Fetch Warning] first try failed {url}: {e}. retrying...")
                try:
                    await asyncio.sleep(1)
                    resp = await client.get(url, headers=headers)
                    resp.raise_for_status()
                except Exception as e2:
                    print(f"[Fetch Error] retry failed {url}: {e2}")
                    raise e2

            content_type = (resp.headers.get("content-type") or "").lower()

            # PDF
            if "pdf" in content_type or url.lower().endswith(".pdf"):
                return await self._parse_pdf(url, resp.content)

            # HTML
            html = resp.text
            title, text = self._extract_main_text(html)
            if len(text) > self.max_chars:
                text = text[: self.max_chars] + "\n\n[TRUNCATED]"

            return Document(url=url, title=title, content=text)

    async def _parse_pdf(self, url: str, data: bytes) -> Document:
        from pypdf import PdfReader

        reader = PdfReader(_BytesIO(data))
        texts = []
        max_pages = min(5, len(reader.pages))
        for i in range(max_pages):
            page = reader.pages[i]
            t = page.extract_text() or ""
            texts.append(t)

        content = _clean_text("\n\n".join(texts))
        if len(content) > self.max_chars:
            content = content[: self.max_chars] + "\n\n[TRUNCATED]"

        return Document(url=url, title="PDF Document", content=content)

    def _extract_main_text(self, html: str) -> Tuple[Optional[str], str]:
        # Try readability-lxml if available
        try:
            from readability import Document as ReadabilityDocument

            doc = ReadabilityDocument(html)
            title = (doc.short_title() or "").strip() or None
            summary_html = doc.summary(html_partial=True)
            soup = BeautifulSoup(summary_html, "html.parser")
            text = _clean_text(soup.get_text("\n"))
            if len(text) > 200:
                return title, text
        except Exception:
            pass

        # Fallback: simple BeautifulSoup extraction
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
            tag.decompose()

        title = None
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

        main_content = ""
        for tag_name in ["main", "article", 'div[class*="content"]', 'div[class*="body"]']:
            found = soup.select(tag_name)
            if found:
                for f in found:
                    main_content += f.get_text("\n") + "\n"

        if len(main_content) < 100:
            text = soup.get_text("\n")
        else:
            text = main_content

        return title, _clean_text(text)


class _BytesIO:
    def __init__(self, data: bytes):
        import io

        self._bio = io.BytesIO(data)

    def read(self, *args, **kwargs):
        return self._bio.read(*args, **kwargs)

    def seek(self, *args, **kwargs):
        return self._bio.seek(*args, **kwargs)

    def tell(self):
        return self._bio.tell()


if __name__ == "__main__":
    async def _demo() -> None:
        fetcher = SimpleFetcher()
        test_url = "https://www.python.org/"
        try:
            doc = await fetcher.fetch(test_url)
        except Exception as exc:
            print(f"fetch failed: {exc}")
            return

        print(f"fetched url: {doc.url}")
        print(f"title: {doc.title}")
        preview = (doc.content or "")[:200].replace("\n", " ")
        print(f"content preview (200 chars): {preview}")

    asyncio.run(_demo())

#!/usr/bin/env python3
"""Embed Markdown into docs HTML pages for offline file:// viewing."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
TEMPLATE_PATH = DOCS_DIR / "_doc-page.template.html"

DOC_PAGES = [
    {
        "html": "api-docs.html",
        "md": "api-docs.md",
        "title": "Digital Employee API 接口文档",
        "heading": "Digital Employee API 接口文档",
        "toc_key": "api-docs-toc-expanded",
    },
    {
        "html": "missing-docs.html",
        "md": "missing-docs.md",
        "title": "项目缺失文档梳理",
        "heading": "项目缺失文档梳理",
        "toc_key": "missing-docs-toc-expanded",
    },
    {
        "html": "README.html",
        "md": "README.md",
        "title": "Nesquena Hermes API Service",
        "heading": "Nesquena Hermes API Service",
        "toc_key": "readme-toc-expanded",
    },
    {
        "html": "Nesquena_Hermes_Agentic_Fabric_README_CN.html",
        "md": "Nesquena_Hermes_Agentic_Fabric_README_CN.md",
        "title": "Nesquena Hermes — 企业级 Agentic Fabric",
        "heading": "Nesquena Hermes — 企业级 Agentic Fabric",
        "toc_key": "agentic-fabric-readme-toc-expanded",
    },
]


def escape_markdown_for_script_tag(markdown: str) -> str:
    """Prevent embedded markdown from prematurely closing the script element."""
    return re.sub(r"</(script)>", r"<\\/\\1>", markdown, flags=re.IGNORECASE)


def render_page(template: str, page: dict, markdown: str) -> str:
    embedded = escape_markdown_for_script_tag(markdown)
    return (
        template.replace("{{PAGE_TITLE}}", page["title"])
        .replace("{{HEADING}}", page["heading"])
        .replace("{{MD_FILE}}", page["md"])
        .replace("{{TOC_STATE_KEY}}", page["toc_key"])
        .replace("{{EMBEDDED_MARKDOWN}}", embedded)
    )


def build_all() -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for page in DOC_PAGES:
        md_path = DOCS_DIR / page["md"]
        html_path = DOCS_DIR / page["html"]
        markdown = md_path.read_text(encoding="utf-8")
        html_path.write_text(render_page(template, page, markdown), encoding="utf-8")
        print(f"built {html_path.relative_to(ROOT)}")


if __name__ == "__main__":
    build_all()
from __future__ import annotations

import uuid
import zipfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from .engine import atomic_text


def export_txt(path: Path, title, chapters, note=""):
    blocks = [title, note] if note else [title]
    for chapter in chapters:
        blocks.append(chapter["title"] + "\n\n" + chapter["text"])
    atomic_text(path, "\n\n".join(blocks) + "\n")


def export_epub(path: Path, title, chapters, author="", note=""):
    def xhtml(heading, body):
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN"><head>'
            f'<title>{escape(heading)}</title><meta charset="utf-8"/></head>'
            f"<body><h1>{escape(heading)}</h1>{body}</body></html>"
        )

    entries = [{"title": title, "text": note or title}, *chapters]
    items, spine, nav = [], [], []
    temporary = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        for index, chapter in enumerate(entries):
            name = f"chapter-{index:05d}.xhtml"
            items.append(f'<item id="c{index}" href="{name}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{index}"/>')
            nav.append(f'<li><a href="{name}">{escape(chapter["title"])}</a></li>')
            body = "".join(f"<p>{escape(line)}</p>" for line in chapter["text"].splitlines() if line)
            archive.writestr("OEBPS/" + name, xhtml(chapter["title"], body))
        archive.writestr(
            "OEBPS/nav.xhtml",
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            '<head><title>目录</title></head><body><nav epub:type="toc"><h1>目录</h1><ol>' + "".join(nav) + "</ol></nav></body></html>",
        )
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        archive.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="utf-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:identifier id="book-id">urn:uuid:{uuid.uuid4()}</dc:identifier>'
            f"<dc:title>{escape(title)}</dc:title><dc:creator>{escape(author)}</dc:creator>"
            f'<dc:language>zh-CN</dc:language><meta property="dcterms:modified">{timestamp}</meta>'
            '</metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
            + "".join(items)
            + "</manifest><spine>"
            + "".join(spine)
            + "</spine></package>",
        )
    temporary.replace(path)

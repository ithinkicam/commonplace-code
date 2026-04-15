"""Programmatic fixture factories for library tests.

Produces tiny epub and pdf files in memory — no binaries committed to the repo.
"""

from __future__ import annotations

from pathlib import Path

SAMPLE_TITLE = "Test Book"
SAMPLE_AUTHOR = "Test Author"
SAMPLE_TEXT = (
    "This is the first paragraph of the test book. "
    "It contains enough text to form at least one chunk.\n\n"
    "This is the second paragraph. "
    "It expands the content so chunking and embedding are exercised.\n\n"
    "A third paragraph for good measure, ensuring multiple sentences appear "
    "in the extracted text for realistic testing of the pipeline."
)


def make_epub(dest: Path) -> Path:
    """Write a minimal epub to *dest* and return the path."""
    from ebooklib import epub  # type: ignore[import-untyped]

    book = epub.EpubBook()
    book.set_identifier("test-book-001")
    book.set_title(SAMPLE_TITLE)
    book.set_language("en")
    book.add_author(SAMPLE_AUTHOR)

    chapter = epub.EpubHtml(
        title="Chapter 1",
        file_name="chap1.xhtml",
        lang="en",
    )
    chapter.content = (
        "<html><body>"
        "<p>This is the first paragraph of the test book. "
        "It contains enough text to form at least one chunk.</p>"
        "<p>This is the second paragraph. "
        "It expands the content so chunking and embedding are exercised.</p>"
        "<p>A third paragraph for good measure, ensuring multiple sentences appear "
        "in the extracted text for realistic testing of the pipeline.</p>"
        "</body></html>"
    )
    book.add_item(chapter)
    book.toc = (epub.Link("chap1.xhtml", "Chapter 1", "chap1"),)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]

    epub.write_epub(str(dest), book)
    return dest


def make_pdf(dest: Path) -> Path:
    """Write a minimal pdf to *dest* and return the path."""
    from pypdf import PdfWriter  # type: ignore[import-untyped]

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)

    # Embed metadata
    writer.add_metadata(
        {
            "/Title": SAMPLE_TITLE,
            "/Author": SAMPLE_AUTHOR,
        }
    )

    with dest.open("wb") as fh:
        writer.write(fh)

    return dest

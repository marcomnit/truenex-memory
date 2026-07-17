"""
Build script: converte i 4 documenti Truenex Memory da MD a HTML pronti per PDF.

Uso:
    python build-pdf.py          # genera tutti e 4 gli HTML
    python build-pdf.py --open   # genera e apre nel browser

Poi da browser: Ctrl+P → Salva come PDF → Margini: Nessuno (o Predefinito)
"""

import re
import sys
from pathlib import Path

try:
    from markdown_it import MarkdownIt
except ImportError:
    print("ERRORE: pip install markdown-it-py")
    sys.exit(1)

DOCS_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = DOCS_DIR / "print"
IMG_SRC_RE = re.compile(r'<img\s+src="([^"]+)"', re.IGNORECASE)

CSS = """
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  color: #1a1a2e;
  background: #fff;
  line-height: 1.7;
  font-size: 11pt;
  max-width: 800px;
  margin: 0 auto;
  padding: 40px 40px 60px;
}

h1 {
  font-size: 28pt;
  font-weight: 800;
  color: #0d0d2b;
  margin: 0 0 8px;
  letter-spacing: -0.5px;
  border-bottom: 3px solid #7C4DFF;
  padding-bottom: 12px;
}

h2 {
  font-size: 16pt;
  font-weight: 700;
  color: #1a1a2e;
  margin: 36px 0 12px;
  padding-left: 10px;
  border-left: 4px solid #00BCD4;
}

h3 {
  font-size: 13pt;
  font-weight: 600;
  color: #333;
  margin: 24px 0 8px;
}

p {
  margin: 0 0 12px;
}

blockquote {
  background: #f0edff;
  border-left: 4px solid #7C4DFF;
  padding: 16px 20px;
  margin: 20px 0;
  border-radius: 0 8px 8px 0;
  font-style: italic;
  color: #4a3b8c;
}

blockquote p {
  margin: 0;
}

strong {
  color: #0d0d2b;
}

img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 28px auto;
  border-radius: 6px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  page-break-inside: avoid;
}

table {
  width: 100%;
  border-collapse: collapse;
  margin: 20px 0;
  font-size: 10pt;
  page-break-inside: avoid;
}

thead th {
  background: #7C4DFF;
  color: white;
  padding: 10px 14px;
  text-align: left;
  font-weight: 600;
}

thead th:first-child { border-radius: 6px 0 0 0; }
thead th:last-child { border-radius: 0 6px 0 0; }

tbody td {
  padding: 9px 14px;
  border-bottom: 1px solid #e8e8f0;
  vertical-align: top;
}

tbody tr:nth-child(even) { background: #fafaff; }
tbody tr:last-child td:first-child { border-radius: 0 0 0 6px; }
tbody tr:last-child td:last-child { border-radius: 0 0 6px 0; }

code {
  background: #f0f0f8;
  padding: 2px 6px;
  border-radius: 4px;
  font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
  font-size: 9.5pt;
  color: #d63384;
}

pre {
  background: #0d0d2b;
  color: #e0e0f0;
  padding: 16px 20px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 16px 0;
  font-size: 9pt;
  line-height: 1.5;
  page-break-inside: avoid;
}

pre code {
  background: none;
  color: inherit;
  padding: 0;
  font-size: inherit;
}

ul, ol { margin: 0 0 12px 24px; }
li { margin: 4px 0; }

hr {
  border: none;
  border-top: 1px solid #e0e0f0;
  margin: 32px 0;
}

/* Cover page */
.cover {
  text-align: center;
  padding: 80px 0 40px;
  page-break-after: always;
}

.cover h1 {
  font-size: 36pt;
  border: none;
  margin-bottom: 20px;
}

.cover .subtitle {
  font-size: 14pt;
  color: #7C4DFF;
  margin-bottom: 40px;
}

.cover .badge {
  display: inline-block;
  background: #00BCD4;
  color: white;
  padding: 6px 18px;
  border-radius: 20px;
  font-size: 10pt;
  font-weight: 600;
  margin: 4px;
}

.cover .badge.purple { background: #7C4DFF; }

/* Print rules */
@media print {
  body {
    padding: 30px 40px;
    max-width: 100%;
  }

  img {
    max-width: 95%;
    box-shadow: none;
  }

  h2 { page-break-before: always; }
  h2:first-of-type { page-break-before: avoid; }

  pre, table, img { page-break-inside: avoid; }

  @page {
    size: A4;
    margin: 15mm;
  }
}
"""


def _resolve_image_paths(body_html: str, doc_dir: Path) -> str:
    """Convert relative image src to absolute file:// URLs resolved from doc_dir."""
    def _replace(m: re.Match) -> str:
        src = m.group(1)
        if src.startswith(("http://", "https://", "file:///", "/")):
            return m.group(0)
        abs_path = (doc_dir / src).resolve()
        # Drive letter (D:) + path with forward slashes, encode only spaces
        forward = str(abs_path).replace("\\", "/").replace(" ", "%20")
        return f'<img src="file:///{forward}"'
    return IMG_SRC_RE.sub(_replace, body_html)


def md_to_html(md_path: Path) -> str:
    md = MarkdownIt("commonmark", {"breaks": True, "html": True})
    raw = md_path.read_text(encoding="utf-8")
    body = md.render(raw)
    return body


def build_one(md_path: Path, output_path: Path) -> None:
    body_html = md_to_html(md_path)
    body_html = _resolve_image_paths(body_html, md_path.parent)
    title = md_path.stem.replace("truenex-memory-", "").replace("-", " ").title()

    full_html = f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>Truenex Memory — {title}</title>
<style>{CSS}</style>
</head>
<body>
{body_html}
</body>
</html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(full_html, encoding="utf-8")
    print(f"  [OK] {output_path.name}")


FILES = [
    "one-liner.md",
    "tecnica.md",
    "narrativa.md",
    "marketing.md",
]


def main():
    should_open = "--open" in sys.argv

    print("\n=== Truenex Memory -- Build PDF ===\n")
    print(f"    Output: {OUTPUT_DIR}\n")

    for filename in FILES:
        md_path = DOCS_DIR / filename
        if not md_path.exists():
            print(f"  [!] {filename} non trovato, skip")
            continue
        html_path = OUTPUT_DIR / md_path.with_suffix(".html").name
        build_one(md_path, html_path)

    print(f"\n[OK] Generati {len(FILES)} file HTML in print/\n")
    print("--> Apri ciascun .html nel browser -> Ctrl+P -> Salva come PDF")
    print("    Imposta margini 'Nessuno' per il miglior risultato.\n")

    if should_open:
        import webbrowser
        for filename in FILES:
            html_path = OUTPUT_DIR / Path(filename).with_suffix(".html").name
            if html_path.exists():
                webbrowser.open(str(html_path))


if __name__ == "__main__":
    main()

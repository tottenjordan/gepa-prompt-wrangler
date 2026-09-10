"""Render the experiment report as a single file you can open anywhere.

`experiment_report.md` references its charts relatively (`charts/radar.png`), which only
resolves if you have the whole `reports/` tree on local disk. In GCS — where every
pipeline run actually publishes — the console does not render markdown at all, and
downloading the one `.md` gives you a report with seven broken images.

So this emits an HTML sibling with **no external references whatsoever**: every image is
inlined as a `data:` URI and the stylesheet is inline. One file, correct wherever it lands
— console preview, a download, an email attachment, a signed URL.

The markdown stays the source of truth. It is greppable, diffable and small; this is a
rendering of it, regenerated from it every time.

`markdown-it-py` is imported lazily so that a container missing it degrades to "no HTML"
rather than taking down the analysis stage — the same reasoning as the try/except around
report rendering in `pipeline/components.py`.
"""

from __future__ import annotations

import base64
import html as _html
import mimetypes
import re
from pathlib import Path

MISSING_IMAGE_NOTE = "chart not available"

# Inline so the file has no external references. Deliberately plain: this is read by
# people skimming for a number, not a design artefact.
_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  font: 15px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  max-width: 60rem; margin: 0 auto; padding: 2.5rem 1.5rem 6rem;
  color: #1a1a1a; background: #fff;
}
h1 { font-size: 1.9rem; margin: 0 0 1.5rem; letter-spacing: -0.02em; }
h2 {
  font-size: 1.3rem; margin: 2.75rem 0 0.9rem;
  padding-bottom: 0.35rem; border-bottom: 2px solid #e6e6e6; letter-spacing: -0.01em;
}
h3 { font-size: 1.05rem; margin: 1.75rem 0 0.6rem; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 0.9rem; }
th, td { border: 1px solid #e0e0e0; padding: 0.45rem 0.65rem; text-align: left; }
th { background: #f6f6f6; font-weight: 600; }
tr:nth-child(even) td { background: #fbfbfb; }
code {
  font: 0.86em/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: #f2f2f2; padding: 0.1em 0.35em; border-radius: 3px;
}
pre { background: #f6f6f6; padding: 0.9rem 1rem; overflow-x: auto; border-radius: 6px; }
pre code { background: none; padding: 0; }
img { max-width: 100%; height: auto; display: block; margin: 1.1rem 0; border-radius: 6px; }
details { margin: 0.9rem 0; padding: 0.6rem 0.9rem; background: #fafafa; border: 1px solid #e6e6e6; border-radius: 6px; }
summary { cursor: pointer; font-weight: 600; }
blockquote { margin: 1rem 0; padding: 0.1rem 1rem; border-left: 3px solid #d0d0d0; color: #555; }
.missing-chart {
  display: block; padding: 0.75rem 1rem; margin: 1.1rem 0;
  border: 1px dashed #c9c9c9; border-radius: 6px; color: #8a6d3b; background: #fdf9ef;
  font-size: 0.9rem;
}
@media (prefers-color-scheme: dark) {
  body { color: #e6e6e6; background: #16181c; }
  h2 { border-bottom-color: #2e3238; }
  th { background: #22262c; } td, th { border-color: #2e3238; }
  tr:nth-child(even) td { background: #1b1e23; }
  code, pre { background: #22262c; }
  details { background: #1b1e23; border-color: #2e3238; }
  .missing-chart { background: #241f16; color: #d6b370; border-color: #4a4132; }
  img { background: #fff; }
}
"""

_IMG_RE = re.compile(r'<img\s+([^>]*?)src="([^"]+)"([^>]*?)>', re.IGNORECASE)


def _is_remote(src: str) -> bool:
    return src.startswith(("http://", "https://", "data:", "//"))


def _resolve_within(base_dir: Path, src: str) -> Path | None:
    """Resolve `src` under `base_dir`, refusing anything that escapes it.

    A report is rendered inside the pipeline container next to real credentials; a `../`
    in an image path must not turn the renderer into a file-exfiltration primitive.
    """
    try:
        base = base_dir.resolve()
        candidate = (base / src).resolve()
        candidate.relative_to(base)
    except (ValueError, OSError):
        return None
    return candidate


def _inline_images(html_body: str, base_dir: Path) -> str:
    def repl(m: re.Match[str]) -> str:
        pre, src, post = m.group(1), m.group(2), m.group(3)
        if _is_remote(src):
            return m.group(0)

        path = _resolve_within(base_dir, src)
        if path is None or not path.is_file():
            # Visible gap, not a silent omission: chart generation is allowed to fail
            # without failing the run, so a missing image is a real and meaningful state.
            return (
                f'<span class="missing-chart">{_html.escape(MISSING_IMAGE_NOTE)}: '
                f"<code>{_html.escape(src)}</code></span>"
            )

        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'<img {pre}src="data:{mime};base64,{payload}"{post}>'

    return _IMG_RE.sub(repl, html_body)


def render_self_contained_html(
    markdown_text: str,
    base_dir: Path | str,
    title: str = "GEPA Prompt Wrangler report",
) -> str:
    """Markdown plus its local images, as one HTML document with no external refs.

    `base_dir` is the directory the markdown's relative paths are written against — in
    practice `reporter.REPORTS_DIR`.
    """
    from markdown_it import MarkdownIt

    # 'commonmark' rather than 'gfm-like': the latter turns on linkify, which needs the
    # optional `linkify-it-py` and raises at render time when it is absent. Tables and
    # strikethrough are the only GFM features the reporter emits.
    md = MarkdownIt("commonmark", {"html": True}).enable("table").enable("strikethrough")
    body = _inline_images(md.render(markdown_text), Path(base_dir))

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_html.escape(title)}</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def write_self_contained_html(
    markdown_path: Path,
    out_path: Path | None = None,
    title: str | None = None,
) -> Path:
    """Render `markdown_path` beside itself as `.html`. Returns the path written."""
    markdown_path = Path(markdown_path)
    out_path = Path(out_path) if out_path else markdown_path.with_suffix(".html")
    out_path.write_text(
        render_self_contained_html(
            markdown_path.read_text(),
            markdown_path.parent,
            title or markdown_path.stem.replace("_", " "),
        )
    )
    return out_path

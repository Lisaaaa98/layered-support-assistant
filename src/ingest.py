"""Parse DBS public card documents into retrievable chunks with provenance metadata.

Three parsers are needed because the source pages differ:
  pdf      - agreement PDFs, clean text layer, split by numbered clause
  static   - server-rendered support pages, content sits in the HTML
  nextdata - Next.js pages where the body lives in the __NEXT_DATA__ JSON blob
"""
import html
import json
import re
from pathlib import Path

import domain
import pdfplumber
from bs4 import BeautifulSoup

from scopes import txn_patterns

ROOT = Path(__file__).resolve().parent.parent
RAW = None  # resolved per domain at call time
OUT = None

MAX_CHARS = 1800  # split threshold; keeps a clause retrievable without losing context

# A heading that got separated from its body carries no information but does
# carry an embedding, and short vectors sit near the centre of the space where
# they match everything weakly. Dropping them removes a class of false hit.
MIN_CHARS = 60


def find_effective_date(text):
    """Pull the document's own stated revision date, if it declares one.

    Absence is meaningful: an undated agreement cannot be trusted over a dated
    one, so we record None rather than guessing.
    """
    m = re.search(r"Last updated:\s*([0-9]{1,2}\s+\w+\s+[0-9]{4})", text, re.I)
    return m.group(1) if m else None


def infer_product_scope(doc_id):
    """Which card a chunk speaks for. Product pages are card-specific;
    agreements and the fee table apply across the portfolio."""
    for key in domain.known_products():
        if key in doc_id:
            return [key]
    return ["all"]


def infer_txn_scope(section, text):
    """Which transaction types a chunk speaks for.

    This is what separates a genuine contradiction from a general/special-case
    pair: 27.8% retail and 28.5% cash advance are both correct.
    """
    blob = f"{section} {text[:600]}".lower()
    hits = [name for name, pat in txn_patterns() if re.search(pat, blob)]
    return hits or ["general"]


def date_info(stated, freshness, retrieved_at):
    """Grade how much we trust this document's currency.

    A live page carries an implicit 'as of today'; an undated offline PDF
    carries nothing and must lose to both.
    """
    if stated:
        return stated, "stated"
    if freshness == "live":
        return retrieved_at, "inferred"
    return None, "unknown"


def split_long(text, section):
    """Break an oversized block, trying structure first and sentences last."""
    if len(text) <= MAX_CHARS:
        return [(section, text)]

    # Preferred: numbered sub-clauses (4.1, 4.2, ...).
    parts = re.split(r"(?m)^(?=\s*\d{1,2}\.\d{1,2}\.?\s)", text)
    if len(parts) == 1:
        # Nothing structural to cut on, fall back to sentence boundaries so a
        # chunk never straddles two unrelated fee amounts.
        parts = re.split(r"(?<=[.;])\s+(?=[A-Z(])", text)

    out, buf = [], ""
    for p in parts:
        if len(buf) + len(p) > MAX_CHARS and buf:
            out.append((section, buf.strip()))
            buf = p
        else:
            buf += (" " if buf and not buf.endswith("\n") else "") + p
    if buf.strip():
        out.append((section, buf.strip()))
    return out


def parse_pdf(path):
    with pdfplumber.open(path) as pdf:
        full = "\n".join(p.extract_text() or "" for p in pdf.pages)
    eff = find_effective_date(full)

    # Clause headings look like "4. PAYMENT" on their own line.
    heads = list(re.finditer(r"(?m)^\s*(\d{1,2})\.\s+([A-Z][A-Z /&\-']{3,60})\s*$", full))
    chunks = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(full)
        section = f"{h.group(1)}. {h.group(2).strip()}"
        body = full[h.start():end].strip()
        chunks.extend(split_long(body, section))
    if not chunks:  # no clause structure detected, fall back to whole document
        chunks = split_long(full, "full text")
    return chunks, eff


def parse_static(path):
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "lxml")
    for t in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        t.decompose()
    text = re.sub(r"\n{2,}", "\n", soup.get_text("\n", strip=True))
    title = soup.title.get_text(strip=True) if soup.title else path.stem
    return split_long(text, title), find_effective_date(text)


def table_to_text(table):
    """Flatten a table but keep row/column association readable."""
    lines = []
    for tr in table.find_all("tr"):
        cells = [re.sub(r"\s*;\s*$", "", c.get_text(" ", strip=True))
                 for c in tr.find_all(["th", "td"])]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def split_by_headings(html, fallback_section):
    """Cut a rich-text block on its own h2/h3 headings.

    The fee pages put one charge type under each heading, so heading-level
    splitting is what keeps 'late payment charge' from landing in the same
    chunk as 'cash advance fee'.
    """
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["style", "script"]):
        t.decompose()
    # <br> carries the line break inside a table cell. get_text() drops it,
    # which glues "Supplementary Card: Free" onto the sentence that follows
    # and produces text no reader, human or model, can parse correctly.
    for br in soup.find_all("br"):
        br.replace_with("; ")

    sections, section, buf = [], fallback_section, []

    def flush():
        body = "\n".join(b for b in buf if b).strip()
        if body:
            sections.append((section, f"{section}\n{body}" if section != fallback_section else body))

    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "table"]):
        if el.name in ("h1", "h2", "h3", "h4"):
            flush()
            section, buf = el.get_text(" ", strip=True), []
        elif el.name == "table":
            buf.append(table_to_text(el))
        else:
            if el.find_parent("table"):
                continue  # already captured by the table above
            buf.append(el.get_text(" ", strip=True))
    flush()
    return sections or [(fallback_section, soup.get_text(" ", strip=True))]


def _chunks_from_json(blobs, fallback_section):
    """Turn rich-text strings found in embedded JSON into chunks.

    Shared by the two client-rendered sites in this project. Both hide their
    prose in JSON and both store it as HTML, so once the JSON is located the
    rest of the work is identical.
    """
    found = []

    def walk(node, path_str=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path_str}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path_str}[{i}]")
        elif isinstance(node, str):
            plain = re.sub(r"<[^>]+>", " ", node)
            plain = re.sub(r"\s+", " ", plain).strip()
            if len(plain) > 120 and " " in plain:
                found.append((path_str.split(".")[-1] or fallback_section, node))

    for blob in blobs:
        walk(blob)

    seen, chunks = set(), []
    for field, raw_html in found:
        for section, text in split_by_headings(raw_html, field):
            text = re.sub(r"[ \t]+", " ", text).strip()
            if text in seen or len(text) < 40:
                continue
            seen.add(text)
            chunks.extend(split_long(text, section))
    plain_all = " ".join(re.sub(r"<[^>]+>", " ", h) for _, h in found)
    return chunks, find_effective_date(plain_all)


def parse_aem(path):
    """Pages that keep their prose in JSON inside element attributes.

    Singtel's support articles render client-side. The served HTML yields only
    a few hundred characters of visible text, and the charges a customer
    actually asks about are not among them: the late-payment article shows
    none of the two fees it exists to explain.

    The content sits in `datamodel` attributes on custom elements, JSON-encoded
    and HTML-escaped. Unescaping the whole page and reading the text back is
    the obvious shortcut and a bad one, because the surrounding configuration
    unescapes too and ends up interleaved with the prose. Parsing the
    attributes as JSON keeps the copy and leaves the configuration behind.
    """
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "lxml")
    blobs = []
    for element in soup.find_all(True):
        for value in (element.attrs or {}).values():
            if isinstance(value, str) and value.strip().startswith(("{", "[")) and len(value) > 200:
                try:
                    blobs.append(json.loads(value))
                except ValueError:
                    continue

    title = soup.title.get_text(strip=True) if soup.title else path.stem
    chunks, eff = _chunks_from_json(blobs, title)
    if chunks:
        return chunks, eff
    return parse_static(path)


def parse_nextdata(path):
    """Walk the __NEXT_DATA__ JSON and keep the prose-bearing string fields."""
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "lxml")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag:
        return parse_static(path)
    data = json.loads(tag.string)

    found = []

    def walk(node, path_str=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path_str}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path_str}[{i}]")
        elif isinstance(node, str):
            plain = re.sub(r"<[^>]+>", " ", node)
            plain = re.sub(r"\s+", " ", plain).strip()
            if len(plain) > 120 and " " in plain:
                found.append((path_str.split(".")[-1] or "content", node))

    walk(data)

    seen, chunks = set(), []
    for field, html in found:
        for section, text in split_by_headings(html, field):
            text = re.sub(r"[ \t]+", " ", text).strip()
            if text in seen or len(text) < 40:
                continue
            seen.add(text)
            chunks.extend(split_long(text, section))
    plain_all = " ".join(re.sub(r"<[^>]+>", " ", h) for _, h in found)
    return chunks, find_effective_date(plain_all)


PARSERS = {"pdf": parse_pdf, "static": parse_static, "nextdata": parse_nextdata,
           "aem": parse_aem}


def main():
    manifest = json.loads(domain.sources_path().read_text(encoding="utf-8"))
    retrieved_at = manifest["retrieved_at"]
    out_dir = domain.processed_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    all_chunks = []

    for doc in manifest["docs"]:
        path = domain.raw_dir() / doc["file"]
        chunks, stated = PARSERS[doc["parser"]](path)
        doc_id = path.stem
        eff_date, confidence = date_info(stated, doc["freshness"], retrieved_at)
        product_scope = infer_product_scope(doc_id)

        kept = [(sec, txt) for sec, txt in chunks if len(txt) >= MIN_CHARS]
        dropped = len(chunks) - len(kept)
        title = doc["title"]
        for i, (section, text) in enumerate(kept):
            # Re-attach the document subject to every chunk. A fee table that
            # says only "this card" is unusable once split off from the page it
            # came from: the model cannot tell which product it describes, and
            # correctly refuses to answer. Both retrieval and generation read
            # this field, so the subject travels with the text.
            all_chunks.append({
                "doc_title": title,
                "chunk_id": f"{doc_id}#{i:03d}",
                "doc_id": doc_id,
                "doc_type": doc["doc_type"],
                "source_url": doc["url"],
                "authority": doc["authority"],
                "effective_date": eff_date,
                "date_confidence": confidence,
                "product_scope": product_scope,
                "txn_scope": infer_txn_scope(section, text),
                "section": section[:120],
                "text": f"{title} — {section}\n{text}" if title not in text else text,
            })
        note = f" (剔除 {dropped} 个残块)" if dropped else ""
        print(f"  {doc['file']:<28} {len(kept):>3} chunks  auth={doc['authority']} "
              f"date={eff_date or '-'} ({confidence}){note}")

    out_file = out_dir / "chunks.jsonl"
    with out_file.open("w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"\n总计 {len(all_chunks)} chunks -> {out_file.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

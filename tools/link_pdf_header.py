"""Add a Portfolio link to the resume PDF's contact line, and make every link on it clickable.

Word's print-to-PDF keeps the blue underlined text of a hyperlink but drops the link
itself, so LinkedIn and GitHub on the header look clickable and are not. This:

  * appends "  •  Portfolio" after GitHub, styled like the links before it,
  * shifts the whole line left by half the width added, so it stays centred,
  * puts a URI annotation over LinkedIn, GitHub and Portfolio,
  * drops any /Highlight annotation, a Preview markup that prints as a colored blob.

The header's own font subset has no "P" or "f", so "Portfolio" is set in the other
Calibri regular subset on the page, which has the full alphabet.

Run after tools/strip_pdf_phone.py, on its output.

Usage: python link_pdf_header.py <in.pdf> <out.pdf> <portfolio_url> <linkedin_url> <github_url>
"""
from __future__ import annotations

import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, ByteStringObject, ContentStream, DictionaryObject,
                           FloatObject, NameObject, NumberObject, TextStringObject)

sys.path.insert(0, str(Path(__file__).parent))
from strip_pdf_phone import raw, tounicode, widths  # noqa: E402

BLUE, BLACK = [0, 0, 1], [0, 0, 0]


def main(src: Path, dst: Path, portfolio: str, linkedin: str, github: str) -> int:
    writer = PdfWriter(clone_from=str(src))
    page = writer.pages[0]
    fonts = {n: f.get_object() for n, f in page["/Resources"]["/Font"].items()}
    umap = {n: tounicode(f) for n, f in fonts.items()}
    wmap = {n: widths(f) for n, f in fonts.items()}
    cs = ContentStream(page.get_contents(), writer)
    ops = cs.operations

    def advance(font: str, size: float, tc: float, text: str) -> float:
        first, ws = wmap[font]
        rev = {v: k for k, v in umap[font].items()}
        return sum((ws[rev[c] - first] / 1000.0 + tc) * size for c in text)

    def encode(font: str, text: str) -> ByteStringObject:
        rev = {v: k for k, v in umap[font].items()}
        missing = [c for c in text if c not in rev]
        if missing:
            raise SystemExit(f"{font} has no glyph for {missing}; nothing changed")
        return ByteStringObject(bytes(rev[c] for c in text))

    # --- the runs, as in strip_pdf_phone -------------------------------------
    runs, tm_i, font, size, tc, cm = [], None, None, 1.0, 0.0, None
    for i, (args, op) in enumerate(ops):
        if op == b"cm":
            cm = [float(a) for a in args]
        elif op == b"Q":
            tc = 0.0
        elif op == b"Tc":
            tc = float(args[0])
        elif op == b"Tm":
            tm_i, size = i, float(args[0])
        elif op == b"Tf":
            font = args[0]
        elif op in (b"Tj", b"TJ"):
            items = args[0] if op == b"TJ" else [args[0]]
            text = "".join(umap.get(font, {}).get(b, "?")
                           for it in items if not isinstance(it, (int, float)) for b in raw(it))
            runs.append({"tm": tm_i, "i": i, "x": float(ops[tm_i][0][4]), "y": float(ops[tm_i][0][5]),
                         "font": font, "size": size, "tc": tc, "text": text, "cm": cm})

    def find(word: str) -> dict:
        r = next((r for r in runs if r["text"].strip() == word), None)
        if r is None:
            raise SystemExit(f"no {word!r} run on the page; nothing changed")
        return r

    if any("Portfolio" in r["text"] for r in runs):
        raise SystemExit("the PDF already has a Portfolio run; nothing changed")
    li, gh = find("LinkedIn"), find("GitHub")
    line = [r for r in runs if abs(r["y"] - gh["y"]) < 0.5]
    bullet = next(r for r in line if r["text"] == "•" and li["x"] < r["x"] < gh["x"])
    end = lambda r: r["x"] + advance(r["font"], r["size"], r["tc"], r["text"].rstrip())  # noqa: E731

    # Reuse the LinkedIn to GitHub spacing so the new separator matches the old one.
    gap, bullet_off = gh["x"] - end(li), bullet["x"] - end(li)
    sep_x, pf_x = end(gh) + bullet_off, end(gh) + gap
    pf_font = next(n for n in fonts if all(c in umap[n].values() for c in "Portfolio")
                   and fonts[n]["/BaseFont"].endswith("+Calibri"))
    pf_w = advance(pf_font, gh["size"], 0.0, "Portfolio")
    added = pf_x + pf_w - end(gh)
    shift = -added / 2.0

    # --- move the existing line and its underlines left by half the width added
    for r in line:
        ops[r["tm"]][0][4] = FloatObject(round(r["x"] + shift, 4))
    scale, ox, oy = gh["cm"][0], gh["cm"][4], gh["cm"][5]
    base_y = scale * gh["y"] + oy
    path_cm, stack, last_path = [1.0, 0, 0, 1.0, 0, 0], [], None
    for i, (args, op) in enumerate(ops):
        if op == b"q":
            stack.append(path_cm)
        elif op == b"Q":
            path_cm = stack.pop() if stack else path_cm
        elif op == b"cm":
            path_cm = [float(a) for a in args]
        elif op in (b"m", b"l") and path_cm == [1.0, 0, 0, 1.0, 0, 0]:
            if base_y - 4 <= float(args[1]) <= base_y + 2:
                args[0] = FloatObject(round(float(args[0]) + shift * scale, 4))
                last_path = i

    # --- the new runs, plus an underline, after the line's last underline ----
    def text_block(x: float, fnt: str, txt: str) -> list:
        return [([], b"q"), ([FloatObject(v) for v in gh["cm"]], b"cm"), ([], b"BT"),
                ([NumberObject(gh["size"]), NumberObject(0), NumberObject(0), NumberObject(gh["size"]),
                  FloatObject(round(x + shift, 4)), FloatObject(gh["y"])], b"Tm"),
                ([NameObject(fnt), NumberObject(1)], b"Tf"),
                ([encode(fnt, txt)], b"Tj"), ([], b"ET"), ([], b"Q")]

    u0 = scale * (pf_x + shift) + ox
    u1 = u0 + scale * pf_w
    y_hi, y_lo = base_y - 1.2, base_y - 1.92
    # Each run ends in a space, as Word's do: it is invisible at the line's end, and without
    # it text extraction reads "GitHub •PortfolioPROFILE", which loses the tagline on import.
    new = ([([FloatObject(v) for v in BLACK], b"sc")] + text_block(sep_x, gh["font"], "• ")
           + [([FloatObject(v) for v in BLUE], b"sc")] + text_block(pf_x, pf_font, "Portfolio ")
           + [([FloatObject(u0), FloatObject(y_hi)], b"m"), ([FloatObject(u1), FloatObject(y_hi)], b"l"),
              ([FloatObject(u1), FloatObject(y_lo)], b"l"), ([FloatObject(u0), FloatObject(y_lo)], b"l"),
              ([], b"h"), ([], b"f"), ([FloatObject(v) for v in BLACK], b"sc")])
    at = (last_path + 2) if last_path is not None else gh["i"] + 3  # after its h and f
    cs.operations = ops[:at] + new + ops[at:]
    page.replace_contents(cs)

    # --- link annotations, and no highlights ---------------------------------
    def link(r: dict, text: str, url: str) -> DictionaryObject:
        x0 = scale * (r["x"] + shift) + ox
        x1 = x0 + scale * advance(r["font"], r["size"], r["tc"], text)
        return DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Link"),
            NameObject("/Rect"): ArrayObject([FloatObject(round(v, 3)) for v in
                                              (x0, base_y - 3, x1, base_y + 9)]),
            NameObject("/Border"): ArrayObject([NumberObject(0)] * 3),
            NameObject("/A"): DictionaryObject({NameObject("/S"): NameObject("/URI"),
                                                NameObject("/URI"): TextStringObject(url)})})

    pf_run = {"x": pf_x, "font": pf_font, "size": gh["size"], "tc": 0.0}
    annots = [a for a in (page.get("/Annots") or []) if a.get_object().get("/Subtype") != "/Highlight"]
    for r, text, url in ((li, "LinkedIn", linkedin), (gh, "GitHub", github), (pf_run, "Portfolio", portfolio)):
        annots.append(writer._add_object(link(r, text, url)))
    page[NameObject("/Annots")] = ArrayObject(annots)

    with dst.open("wb") as fh:
        writer.write(fh)
    print(f"added Portfolio -> {portfolio}, linked LinkedIn and GitHub, shifted line {shift * scale:.1f}pt, wrote {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2]), *sys.argv[3:6]))

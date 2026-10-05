"""Remove the phone number from the resume PDF's header line.

The header is a run of absolutely positioned text ops sharing one baseline:

    Portland,   OR •   (503) 555  -  0142  •  email  •  LinkedIn • GitHub

so deleting the phone leaves a hole rather than closing it. This drops the phone
runs, pulls everything after them left by the width removed, and shifts the whole
line right by half of that so it stays centred under the name.

Usage: python tools/strip_pdf_phone.py <in.pdf> <out.pdf>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, FloatObject, NumberObject

PHONE = re.compile(r"\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4}")
DROP_AFTER_PHONE = "  •  "  # the separator the phone used to own


def tounicode(font) -> dict[int, str]:
    tu = font.get("/ToUnicode")
    if tu is None:
        return {}
    data = tu.get_data().decode("latin-1")
    m: dict[int, str] = {}
    for blk in re.findall(r"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            m[int(src, 16)] = "".join(chr(int(dst[i:i + 4], 16)) for i in range(0, len(dst), 4))
    for blk in re.findall(r"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            base = int(dst, 16)
            for k, c in enumerate(range(int(lo, 16), int(hi, 16) + 1)):
                m[c] = chr(base + k)
    return m


def find_phone_run(runs: list[dict]) -> dict | None:
    """The run that holds the first character of a phone number, or None.

    Word splits a phone across runs ("(503) 555", "-", "0142"), so no single run
    reads as one. Each baseline is joined in x order and searched whole, trying
    lines in the order their first run appears in the content stream.
    """
    tried: list[float] = []
    for run in runs:
        if any(abs(run["y"] - y) < 0.5 for y in tried):
            continue
        tried.append(run["y"])
        line = sorted((r for r in runs if abs(r["y"] - run["y"]) < 0.5), key=lambda r: r["x"])
        m = PHONE.search("".join(r["text"] for r in line))
        if m is None:
            continue
        pos = 0
        for r in line:
            if pos <= m.start() < pos + len(r["text"]):
                return r
            pos += len(r["text"])
    return None


def widths(font) -> tuple[int, list[float]]:
    return int(font.get("/FirstChar", 0)), [float(w) for w in font.get("/Widths", [])]


def raw(obj) -> bytes:
    return bytes(obj.original_bytes) if hasattr(obj, "original_bytes") else str(obj).encode("latin-1")


def main(src: Path, dst: Path) -> int:
    writer = PdfWriter(clone_from=str(src))  # edit the writer's own pages
    page = writer.pages[0]
    fonts = {n: f.get_object() for n, f in page["/Resources"]["/Font"].items()}
    umap = {n: tounicode(f) for n, f in fonts.items()}
    wmap = {n: widths(f) for n, f in fonts.items()}
    cs = ContentStream(page.get_contents(), writer)
    ops = cs.operations

    # --- pass 1: collect the text runs, each with the Tm that places it -------
    runs = []          # (tm_index, text_index, x, y, font, size, tc, text)
    tm_i = font = None
    size = 1.0
    tc = 0.0
    x = y = 0.0
    cm = [1.0, 0, 0, 1.0, 0, 0]
    stack: list[list[float]] = []
    for i, (args, op) in enumerate(ops):
        if op == b"q":
            stack.append(list(cm))
        elif op == b"Q":
            cm = stack.pop() if stack else cm
        elif op == b"cm":
            cm = [float(a) for a in args]
        if op == b"Tc":
            tc = float(args[0])
        elif op == b"Tm":
            tm_i, size, x, y = i, float(args[0]), float(args[4]), float(args[5])
        elif op == b"Tf":
            font = args[0]
        elif op in (b"Tj", b"TJ"):
            items = args[0] if op == b"TJ" else [args[0]]
            text = "".join(umap.get(font, {}).get(b, "�")
                           for it in items if not isinstance(it, (int, float)) for b in raw(it))
            runs.append({"tm": tm_i, "i": i, "x": x, "y": y, "font": font,
                         "size": size, "tc": tc, "text": text, "op": op,
                         "cm": list(cm)})

    hit = find_phone_run(runs)
    if hit is None:
        raise SystemExit("no phone-shaped run found; nothing changed")
    line = [r for r in runs if abs(r["y"] - hit["y"]) < 0.5]
    line.sort(key=lambda r: r["x"])

    # --- pass 2: decide what goes, and how wide the hole is -------------------
    # Everything from the first phone digit to the end of the separator that
    # followed it. Runs wholly inside that span are dropped; the run holding the
    # tail ("0142  •  dana...") keeps only what follows the separator.
    joined = "".join(r["text"] for r in line)
    m = PHONE.search(joined)
    if m is None:
        raise SystemExit("the line does not read as a phone number; nothing changed")
    start, end = m.start(), m.end()
    tail = joined[end:end + len(DROP_AFTER_PHONE)]
    if tail == DROP_AFTER_PHONE:
        end += len(DROP_AFTER_PHONE)

    def advance(font_name: str, size: float, tc: float, text: str) -> float:
        first, ws = wmap.get(font_name, (0, []))
        rev = {v: k for k, v in umap.get(font_name, {}).items()}
        total = 0.0
        for ch in text:
            code = rev.get(ch)
            w = ws[code - first] if code is not None and 0 <= code - first < len(ws) else 0.0
            total += (w / 1000.0 + tc) * size
        return total

    removed_w = 0.0
    pos = 0
    for r in line:
        r0, r1 = pos, pos + len(r["text"])
        pos = r1
        cut_lo, cut_hi = max(r0, start), min(r1, end)
        if cut_hi <= cut_lo:
            r["keep"] = r["text"]
            continue
        cut = r["text"][cut_lo - r0:cut_hi - r0]
        removed_w += advance(r["font"], r["size"], r["tc"], cut)
        r["keep"] = r["text"][:cut_lo - r0] + r["text"][cut_hi - r0:]
        # When the cut takes the run's own opening, what is left used to start
        # that much further along the line, so its origin moves right by the
        # width of the cut. A cut in the middle needs no such offset: the run is
        # rewritten as one string and the text simply closes up.
        r["lead_cut"] = advance(r["font"], r["size"], r["tc"], cut) if cut_lo == r0 else 0.0

    # --- pass 3: rewrite positions and text ----------------------------------
    half = removed_w / 2.0
    drop: set[int] = set()
    for r in line:
        if not r["keep"].strip():
            if r["text"] != r["keep"]:      # emptied by the cut, not a plain space
                drop.add(r["i"])
                drop.add(r["tm"])
                continue
        shift = half if r["x"] < hit["x"] else half - removed_w
        new_x = r["x"] + shift + r.get("lead_cut", 0.0)
        args = ops[r["tm"]][0]
        args[4] = FloatObject(round(new_x, 4))
        if r["text"] != r["keep"]:
            # Rebuild the run as a single string: the kerning numbers in the
            # original array belong to glyph pairs that no longer sit together.
            rev = {v: k for k, v in umap.get(r["font"], {}).items()}
            data = bytes(rev[c] for c in r["keep"] if c in rev)
            from pypdf.generic import ArrayObject, ByteStringObject
            # The operands have to be pypdf objects: a bare list cannot write
            # itself back to the content stream.
            ops[r["i"]] = ([ArrayObject([ByteStringObject(data)])], b"TJ")

    # --- pass 4: the link underlines -----------------------------------------
    # A Word link's underline is a filled quad in page space, not part of the
    # text, so text that moves without it leaves its underline behind. They sit
    # a point or two under the baseline, so the band reaches below the line.
    tcm = hit["cm"]
    scale = tcm[0]
    line_y = tcm[3] * hit["y"] + tcm[5]
    phone_x = tcm[0] * hit["x"] + tcm[4]
    cm = [1.0, 0, 0, 1.0, 0, 0]
    stack = []
    moved = 0
    for args, op in ops:
        if op == b"q":
            stack.append(list(cm))
        elif op == b"Q":
            cm = stack.pop() if stack else cm
        elif op == b"cm":
            cm = [float(a) for a in args]
        elif op in (b"m", b"l", b"re") and cm[1] == 0 and cm[2] == 0:
            ax = cm[0] * float(args[0]) + cm[4]
            ay = cm[3] * float(args[1]) + cm[5]
            if line_y - 4 <= ay <= line_y + 12:
                shift = half if ax < phone_x else half - removed_w
                args[0] = FloatObject(round(float(args[0]) + shift * scale / cm[0], 4))
                moved += 1

    cs.operations = [o for i, o in enumerate(ops) if i not in drop]
    page.replace_contents(cs)
    print(f"  moved {moved} underline point(s) with the text")

    # A tel: link would survive the text edit, so any annotation over the header
    # that points at one goes too.
    from pypdf.generic import ArrayObject, NameObject
    for pg in writer.pages:
        annots = pg.get("/Annots")
        annots = annots.get_object() if hasattr(annots, "get_object") else annots
        if not annots:
            continue
        keep = [a for a in annots
                if "tel:" not in str(a.get_object().get("/A", {}).get("/URI", ""))]
        if len(keep) != len(annots):
            pg[NameObject("/Annots")] = ArrayObject(keep)

    writer.add_metadata({"/Producer": "", "/Author": "", "/Creator": ""})
    with dst.open("wb") as fh:
        writer.write(fh)
    print(f"removed {m.group(0)!r} plus separator, width {removed_w:.1f}, wrote {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))

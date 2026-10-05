"""tools/strip_pdf_phone.py, against a synthetic PDF shaped like Word's output.

Word writes the header line as separately positioned runs, one `Tm` each, and
splits a phone number across them: "(503) 555 ", "- ", "0142". No single run
reads as a phone number, which is why the tool joins each baseline before
searching it. The fixture here is built byte by byte with that shape, a font
carrying `/Widths` and a `/ToUnicode` map, because those are the two things the
tool reads to know what each run says and how wide it is.

The digits are 555 numbers. Nothing here is anybody's real phone.
"""

import re
import sys
from pathlib import Path

import pytest

pypdf = pytest.importorskip("pypdf")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import strip_pdf_phone  # noqa: E402

BULLET = 0x95  # the byte the font maps to U+2022

CMAP = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CMapName /Synthetic-UCS def
/CMapType 2 def
1 begincodespacerange
<00> <FF>
endcodespacerange
1 beginbfchar
<95> <2022>
endbfchar
1 beginbfrange
<20> <7E> <0020>
endbfrange
endcmap
end
end
"""


def _literal(text: str) -> bytes:
    raw = text.replace("•", chr(BULLET)).encode("latin-1")
    out = bytearray(b"(")
    for byte in raw:
        if byte in b"()\\":
            out += b"\\" + bytes([byte])
        elif byte >= 0x80:
            out += b"\\%03o" % byte
        else:
            out.append(byte)
    return bytes(out + b")")


def build_pdf(lines: list[tuple[float, list[tuple[float, str]]]]) -> bytes:
    """A one page PDF: each line is (y, [(x, run text), ...]), one Tm per run."""
    ops = [b"BT", b"/F1 1 Tf"]
    for y, runs in lines:
        for x, text in runs:
            ops.append(b"10 0 0 10 %.2f %.2f Tm" % (x, y))
            ops.append(b"[" + _literal(text) + b"] TJ")
    ops.append(b"ET")
    content = b"\n".join(ops)
    widths = b" ".join(b"500" for _ in range(32, BULLET + 1))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /FirstChar 32 "
        b"/LastChar %d /Widths [%s] /ToUnicode 6 0 R >>" % (BULLET, widths),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(CMAP), CMAP),
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        pdf += b"%010d 00000 n \n" % offset
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref)
    return bytes(pdf)


HEADER = [
    (100.0, "Portland, "),
    (150.0, "OR • "),
    (180.0, "(503) 555 "),
    (240.0, "- "),
    (252.0, "0142"),
    (280.0, "  •  dana@example.com"),
    (400.0, "  •  LinkedIn"),
]


def _text(path: Path) -> str:
    return pypdf.PdfReader(str(path)).pages[0].extract_text()


def test_a_phone_split_across_runs_is_removed(tmp_path):
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    src.write_bytes(build_pdf([(720.0, [(100.0, "Dana Reyes")]), (700.0, HEADER)]))
    assert re.search(r"555\D*0142", _text(src)), "the fixture should read as a phone first"

    strip_pdf_phone.main(src, dst)

    after = _text(dst)
    assert "0142" not in after and "(503)" not in after
    assert "Portland" in after and "dana@example.com" in after and "LinkedIn" in after
    assert "Dana Reyes" in after


def test_the_hit_is_the_run_holding_the_first_digit():
    runs = [{"y": 700.0, "x": x, "text": t} for x, t in HEADER]
    runs.insert(0, {"y": 720.0, "x": 100.0, "text": "Dana Reyes"})
    assert strip_pdf_phone.find_phone_run(runs)["text"] == "(503) 555 "


def test_no_phone_is_a_refusal_not_a_silent_copy(tmp_path):
    """copy_resume_pdf in setup_profile catches exactly this SystemExit."""
    src = tmp_path / "in.pdf"
    src.write_bytes(build_pdf([(700.0, [(100.0, "Portland, "), (150.0, "OR")])]))
    with pytest.raises(SystemExit):
        strip_pdf_phone.main(src, tmp_path / "out.pdf")

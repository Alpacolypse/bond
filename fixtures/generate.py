"""Generate the synthetic case fixtures: tenants' cases faxed to a legal aid office, and their OCR output.

Each fax is drawn with reportlab, and its OCR output imitates Reducto's output schema
with bboxes computed from the same layout, so every block really sits where it
says it does. The OCR text carries the usual OCR quirks (a hyphenated line
break, curly quotes, a ligature) so search normalization has something to do.

All data is invented. Run with: uv run python fixtures/generate.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from bond.jsonvalue import JsonObject

HERE = Path(__file__).parent
W, H = letter  # 612 x 792 points
MARGIN = 72


class Block(NamedTuple):
    """A block of a fax page, and what OCR reads from it."""

    type: str  # the block type Reducto would label it with
    font: str
    size: int
    lines: list[str]
    ocr: list[str] | None  # replaces the block's transcribed text, to simulate OCR output


# Each page is a list of blocks.
CASE_001: list[list[Block]] = [
    [
        Block("Title", "Helvetica-Bold", 18, ["FAX COVER SHEET"], None),
        Block("Key Value", "Helvetica", 11, ["To: Intake Unit, Northwind Legal Aid"], None),
        Block("Key Value", "Helvetica", 11, ["From: Riverside Tenants Union, Elena Marsh"], None),
        Block("Key Value", "Helvetica", 11, ["Date: 09/28/2026    Pages: 2"], None),
        Block("Key Value", "Helvetica", 11, [
            "RE: Request for representation, repair and deduct for a",
            "bathroom ceiling leak (HC 12.4)",
        ], None),
        Block("Key Value", "Helvetica", 11, [
            "Client: Jordan Avery    Unit: 4B    Matter ID: NWL-4471902",
        ], None),
        Block("Text", "Helvetica-Oblique", 9, [
            "CONFIDENTIAL: This fax contains privileged information intended",
            "only for the recipient named above.",
        ], None),
    ],
    [
        Block("Title", "Helvetica-Bold", 14, ["Intake Note - 09/26/2026"], None),
        Block("Text", "Helvetica", 11, [
            "Problem: Water leaking through the bathroom ceiling for 8 weeks.",
        ], None),
        Block("Text", "Helvetica", 11, [
            "History: Tenant reports a progressive ceil-",
            "ing leak in the bathroom with mold spreading over the wall",
            "behind the toilet. The leak began after a storm on 08/01/2026.",
        ], None),
        Block("Text", "Helvetica", 11, [
            "Notice given: Sent 6 weeks of written notice to the landlord",
            "(3 letters) and called the management office twice a week, with",
            "“no response.”",
        ], None),
        Block("Text", "Helvetica", 11, [
            "Inspection: Active drip from the bathroom ceiling light fixture.",
            "Black mold over 4 square feet of the wall (photo 4/5). Floor",
            "dry. No significant damage to other rooms.",
        ], [
            "Inspection: Active drip from the bathroom ceiling light fixture.",
            "Black mold over 4 square feet of the wall (photo 4/5). Floor",
            "dry. No signiﬁcant damage to other rooms.",
        ]),
        Block("Text", "Helvetica", 11, [
            "Emergencies: None. No gas leak, no sewage backup, no exposed",
            "wiring, no loss of heat.",
        ], None),
        Block("Text", "Helvetica", 11, [
            "Assessment: Habitability defect, water intrusion (HC 12.4), not",
            "repaired after written notice.",
        ], None),
        Block("Text", "Helvetica", 11, [
            "Plan: Repair and deduct for the ceiling leak, or a demand letter",
            "to the landlord before filing in housing court.",
        ], None),
        Block("Text", "Helvetica-Oblique", 10, ["Electronically signed: Elena Marsh, Tenant Organizer"], None),
    ],
]


CASE_002: list[list[Block]] = [
    [
        Block("Title", "Helvetica-Bold", 18, ["FAX COVER SHEET"], None),
        Block("Key Value", "Helvetica", 11, ["To: Intake Unit, Northwind Legal Aid"], None),
        Block("Key Value", "Helvetica", 11, ["From: Lakeshore Housing Clinic, Tomas Reyes"], None),
        Block("Key Value", "Helvetica", 11, ["Date: 10/02/2026    Pages: 2"], None),
        Block("Key Value", "Helvetica", 11, [
            "RE: Request for representation, rodent infestation in the",
            "kitchen and bedroom (HC 14.1)",
        ], None),
        Block("Key Value", "Helvetica", 11, [
            "Client: Priya Natarajan    Unit: 7C    Matter ID: NWL-5520817",
        ], None),
        Block("Text", "Helvetica-Oblique", 9, [
            "CONFIDENTIAL: This fax contains privileged information intended",
            "only for the recipient named above.",
        ], None),
    ],
    [
        Block("Title", "Helvetica-Bold", 14, ["Housing Clinic Visit - 09/30/2026"], None),
        Block("Text", "Helvetica", 11, [
            "Problem: Mice in the kitchen and bedroom, with droppings in the cabinets,",
            "for 5 weeks since the unit next door was renovated.",
        ], None),
        Block("Text", "Helvetica", 11, [
            "History: Droppings turn up daily along the kitchen baseboards and",
            "under the sink. The tenant's son was \"bitten once\" in September.",
        ], [
            "History: Droppings turn up daily along the kitchen baseboards and",
            "under the sink. The tenant's son was “bitten once” in September.",
        ]),
        Block("Text", "Helvetica", 11, [
            "Notice given: 4 weeks of written notice (2 letters), a portal",
            "message, and traps the tenant bought herself, without",
            "improvement.",
        ], None),
        Block("Text", "Helvetica", 11, [
            "Inspection: Fresh droppings in 3 rooms. Gnaw marks on the cabinet",
            "doors and a stuffed toy. Gaps around the pipes under the sink.",
        ], [
            "Inspection: Fresh droppings in 3 rooms. Gnaw marks on the cabinet",
            "doors and a stuﬀed toy. Gaps around the pipes under the sink.",
        ]),
        Block("Text", "Helvetica", 11, [
            "Photos: Dated photographs from 09/12/2026 show droppings and",
            "gnaw marks in the kitchen.",
        ], None),
        Block("Text", "Helvetica", 11, [
            "Assessment: Suspected rodent infestation, kitchen and bedroom",
            "(HC 14.1), with signs of active spread.",
        ], None),
        Block("Text", "Helvetica", 11, [
            "Plan: Demand letter for professional extermination and a city",
            "inspec-",
            "tion request.",
        ], None),
        Block("Text", "Helvetica-Oblique", 10, ["Electronically signed: Tomas Reyes, Housing Counselor"], None),
    ],
]

CASE_003: list[list[Block]] = [
    [
        Block("Title", "Helvetica-Bold", 18, ["FAX COVER SHEET"], None),
        Block("Key Value", "Helvetica", 11, ["To: Intake Unit, Northwind Legal Aid"], None),
        Block("Key Value", "Helvetica", 11, ["From: Cedar Valley Community Center, Hannah Cho"], None),
        Block("Key Value", "Helvetica", 11, ["Date: 10/06/2026    Pages: 2"], None),
        Block("Key Value", "Helvetica", 11, [
            "RE: Request for representation, repair and deduct for a",
            "bedroom window (HC 12.6)",
        ], None),
        Block("Key Value", "Helvetica", 11, [
            "Client: Samuel Okafor    Unit: 2A    Matter ID: NWL-3308154",
        ], None),
        Block("Text", "Helvetica-Oblique", 9, [
            "CONFIDENTIAL: This fax contains privileged information intended",
            "only for the recipient named above.",
        ], None),
    ],
    [
        Block("Title", "Helvetica-Bold", 14, ["Intake Visit - 10/05/2026"], None),
        Block("Text", "Helvetica", 11, [
            "Problem: Bedroom window frame cracked and will not close for 4 weeks.",
        ], None),
        Block("Text", "Helvetica", 11, [
            "History: Damage after a storm on 09/07/2026. Wind and rain come in around",
            "the frame at night; the tenant calls the room \"freezing.\"",
        ], [
            "History: Damage after a storm on 09/07/2026. Wind and rain come in around",
            "the frame at night; the tenant calls the room “freezing.”",
        ]),
        Block("Text", "Helvetica", 11, [
            "Notice given: 2 weeks of written notice and a portal message to the",
            "property manager. City inspection request filed;",
            "inspection scheduled for 10/14/2026.",
        ], None),
        Block("Text", "Helvetica", 11, [
            "Inspection: Window frame cracked at the latch, leaving a 1-inch gap.",
            "Sill damp on the right side. No mold present.",
        ], None),
        Block("Text", "Helvetica", 11, [
            "Emergencies: None reported. No break-in, no broken glass, no water",
            "reaching the unit below.",
        ], None),
        Block("Text", "Helvetica", 11, [
            "Assessment: Habitability defect, window (HC 12.6), early in its",
            "notice period.",
        ], None),
        Block("Text", "Helvetica", 11, [
            "Plan: Repair and deduct for the window after the city inspec-",
            "tion, before housing court.",
        ], None),
        Block("Text", "Helvetica-Oblique", 10, ["Electronically signed: Hannah Cho, Housing Advocate"], None),
    ],
]


def build(case: str, pages: list[list[Block]], job_id: str, duration: float) -> None:
    stem = f"fax_case_{case}"
    pdf_path = HERE / f"{stem}.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=letter, invariant=1)
    chunks: list[JsonObject] = []
    for page_no, blocks in enumerate(pages, start=1):
        y = H - MARGIN
        out_blocks: list[JsonObject] = []
        contents: list[str] = []  # each block's transcribed text, which its chunk repeats
        for btype, font, size, lines, ocr in blocks:
            leading = size * 1.3
            top = y
            width = 0.0
            for line in lines:
                y -= leading
                c.setFont(font, size)
                c.drawString(MARGIN, y, line)
                width = max(width, stringWidth(line, font, size))
            bottom = y - size * 0.3
            pad = 2
            contents.append("\n".join(ocr or lines))
            out_blocks.append({
                "type": btype,
                "bbox": {
                    "left": round((MARGIN - pad) / W, 4),
                    "top": round((H - top) / H, 4),
                    "width": round((width + 2 * pad) / W, 4),
                    "height": round((top - bottom) / H, 4),
                    "page": page_no,
                    "original_page": page_no,
                },
                "content": contents[-1],
                "confidence": "high",
            })
            y -= size * 0.9
        c.showPage()
        chunks.append({
            "content": "\n\n".join(contents),
            "embed": "\n\n".join(contents),
            "enriched": None,
            "blocks": out_blocks,
        })
    c.save()

    output = {
        "job_id": job_id,
        "duration": duration,
        "result": {"type": "full", "chunks": chunks},
        "usage": {"num_pages": len(pages), "credits": len(pages)},
    }
    (HERE / f"{stem}.reducto.json").write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {pdf_path.name} and {stem}.reducto.json")


CASES = [
    ("001", CASE_001, "synthetic-0001", 2.81),
    ("002", CASE_002, "synthetic-0002", 3.07),
    ("003", CASE_003, "synthetic-0003", 2.64),
]

if __name__ == "__main__":
    for args in CASES:
        build(*args)

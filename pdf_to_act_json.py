#!/usr/bin/env python3
"""Convert a Bare Act PDF into a structured JSON document.

The script targets the schema shown in ``act_model.json`` and preserves all text
by attaching every unmatched line to the nearest logical container.

Usage:
    python pdf_to_act_json.py 2435_a1961-43.pdf --output 2435_a1961-43.json
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


ROMAN_RE = r"[IVXLCDM]+"
CHAPTER_RE = re.compile(rf"^CHAPTER\s+({ROMAN_RE}|\d+[A-Z]?)\b[:.\-\s]*(.*)$", re.IGNORECASE)
PART_RE = re.compile(rf"^PART\s+({ROMAN_RE}|\d+[A-Z]?)\b[:.\-\s]*(.*)$", re.IGNORECASE)
SUBPART_RE = re.compile(rf"^(?:SUB[-\s]?PART)\s+({ROMAN_RE}|\d+[A-Z]?)\b[:.\-\s]*(.*)$", re.IGNORECASE)
TITLE_RE = re.compile(rf"^TITLE\s+({ROMAN_RE}|\d+[A-Z]?)\b[:.\-\s]*(.*)$", re.IGNORECASE)
SECTION_START_RE = re.compile(
    r"^(?P<num>\d+[A-Z]?)\s*[\.)-]?\s*(?:\[(?P<bracketed>[^\]]+)\])?\s*(?P<rest>.*)$"
)
SCHEDULE_RE = re.compile(r"^THE\s+SCHEDULES?$|^SCHEDULE\s+([A-Z]+|\d+)", re.IGNORECASE)
ENACTMENT_DATE_RE = re.compile(r"\b(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+,?\s+\d{4})\b")
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
ACT_NUMBER_RE = re.compile(r"ACT\s+NO\.\s*([\w-]+)", re.IGNORECASE)
ARRANGEMENT_RE = re.compile(r"^(ARRANGEMENT OF SECTIONS|CONTENTS)$", re.IGNORECASE)
ARRANGEMENT_END_RE = re.compile(r"^(AN ACT|WHEREAS|BE IT ENACTED)", re.IGNORECASE)
PAGE_N_OF_M_RE = re.compile(r"^PAGE\s+\d+\s+OF\s+\d+$", re.IGNORECASE)


@dataclass
class Section:
    section_no: str
    heading: str = ""
    content_lines: List[str] = field(default_factory=list)
    footnote_lines: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, str]:
        content_raw = "\n".join(self.content_lines).strip()
        footnote_raw = "\n".join(self.footnote_lines).strip()
        return {
            "section_no": self.section_no,
            "heading": self.heading.strip(),
            "content_html": to_html(content_raw),
            "content_raw": content_raw,
            "footnote_html": to_html(footnote_raw),
        }


@dataclass
class Node:
    type: str
    label: str
    name: str
    sections: List[Section] = field(default_factory=list)
    children: List["Node"] = field(default_factory=list)

    def to_dict(self) -> Dict:
        payload = {
            "type": self.type,
            "label": self.label,
            "name": self.name.strip(),
            "sections": [s.to_dict() for s in self.sections],
        }
        if self.children:
            payload["children"] = [child.to_dict() for child in self.children]
        return payload


@dataclass
class LineWithPage:
    text: str
    page_no: int


def to_html(text: str) -> str:
    if not text:
        return ""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "".join(f"<p>{escape_html(p).replace(chr(10), '<br/>')}</p>" for p in paragraphs)


def escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def extract_pdf_pages(pdf_path: Path) -> List[str]:
    """Extract text page-wise using pypdf if available."""
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:  # pragma: no cover - runtime environment concern
        raise RuntimeError(
            "pypdf is required to read PDFs. Install with: pip install pypdf"
        ) from exc

    reader = PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        pages.append((page.extract_text() or "").strip())
    return pages


def _iter_normalized_page_lines(page_text: str) -> Iterable[str]:
    for raw_line in page_text.splitlines():
        line = " ".join(raw_line.replace("\u00a0", " ").split()).strip()
        if line:
            yield line


def _repeated_header_footer_lines(pages: List[str]) -> set[str]:
    edge_occurrence_count: Dict[str, int] = {}
    page_count = len(pages)

    for page_text in pages:
        normalized = list(_iter_normalized_page_lines(page_text))
        if not normalized:
            continue
        edge_candidates = set(normalized[:2] + normalized[-2:])
        for candidate in edge_candidates:
            edge_occurrence_count[candidate] = edge_occurrence_count.get(candidate, 0) + 1

    # Keep only "high confidence" repeated edge lines.
    return {
        text
        for text, count in edge_occurrence_count.items()
        if page_count >= 3 and count >= max(3, int(page_count * 0.6))
    }


def normalize_lines(pages: List[str]) -> List[LineWithPage]:
    repeated_edge_lines = _repeated_header_footer_lines(pages)
    lines: List[LineWithPage] = []

    for page_idx, page_text in enumerate(pages, start=1):
        for line in _iter_normalized_page_lines(page_text):
            # Drop noisy pagination/footer lines only.
            if re.fullmatch(r"\d+", line):
                continue
            if PAGE_N_OF_M_RE.match(line):
                continue
            if line in repeated_edge_lines and len(line) > 5:
                continue
            lines.append(LineWithPage(text=line, page_no=page_idx))
    return lines


def split_heading_and_body(text: str) -> Tuple[str, str]:
    text = text.strip()
    if not text:
        return "", ""
    if "—" in text:
        left, right = text.split("—", 1)
        return left.strip(), right.strip()
    if "." in text and len(text.split(".", 1)[0].split()) <= 12:
        left, right = text.split(".", 1)
        return left.strip(), right.strip()
    return text, ""


def parse_metadata(lines: List[LineWithPage], pdf_path: Path) -> Dict:
    title = lines[0].text if lines else pdf_path.stem
    handle_id = pdf_path.stem
    year = ""
    act_number = ""
    enactment_date = ""

    long_title_lines: List[str] = []
    started = False
    for entry in lines[:180]:
        line = entry.text
        if not year:
            ym = YEAR_RE.search(line)
            if ym:
                year = ym.group(1)
        if not act_number:
            am = ACT_NUMBER_RE.search(line)
            if am:
                act_number = am.group(1)
        if not enactment_date:
            dm = ENACTMENT_DATE_RE.search(line)
            if dm:
                enactment_date = dm.group(1)

        if line.upper().startswith("AN ACT"):
            started = True
        if started:
            long_title_lines.append(line)
            if line.endswith("."):
                break

    long_title = " ".join(long_title_lines).strip() or title

    metadata = {
        "Enactment Date": to_iso_date(enactment_date),
        "Long Title": long_title,
        "Ministry": "",
        "Department": "",
        "Enforcement Date": "",
    }

    return {
        "handle_id": handle_id,
        "title": title,
        "year": year,
        "act_number": act_number,
        "enactment_date": enactment_date,
        "jurisdiction": "India",
        "actid": handle_id,
        "metadata": metadata,
    }


def to_iso_date(date_text: str) -> str:
    if not date_text:
        return ""
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", date_text.replace(",", ""))
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def is_likely_container_name(line: str) -> bool:
    if not line or len(line) < 3:
        return False
    if len(line) > 160:
        return False
    alpha_count = sum(1 for c in line if c.isalpha())
    if alpha_count < 3:
        return False
    uppercase_ratio = sum(1 for c in line if c.isupper()) / max(alpha_count, 1)
    return uppercase_ratio > 0.6


def parse_structure(lines: List[LineWithPage]) -> Tuple[List[Node], List[Section], List[Dict], List[str]]:
    body: List[Node] = []
    uncategorised_sections: List[Section] = []
    schedules: List[Dict] = []
    preamble_lines: List[str] = []

    node_stack: List[Node] = []
    current_section: Optional[Section] = None
    in_schedule_zone = False
    in_arrangement_zone = False
    pending_name_node: Optional[Node] = None

    def attach_section(sec: Section) -> None:
        if node_stack:
            node_stack[-1].sections.append(sec)
        else:
            uncategorised_sections.append(sec)

    def close_section() -> None:
        nonlocal current_section
        if current_section is not None:
            attach_section(current_section)
            current_section = None

    def add_node(node: Node, allowed_parent_types: Tuple[str, ...]) -> None:
        nonlocal pending_name_node
        close_section()
        # Exit stack until parent is compatible.
        while node_stack and node_stack[-1].type not in allowed_parent_types:
            node_stack.pop()
        if node_stack and node_stack[-1].type in allowed_parent_types:
            node_stack[-1].children.append(node)
        else:
            body.append(node)
        node_stack.append(node)
        pending_name_node = node if not node.name else None

    for entry in lines:
        line = entry.text
        if ARRANGEMENT_RE.match(line):
            in_arrangement_zone = True
            continue

        if in_arrangement_zone:
            if ARRANGEMENT_END_RE.match(line):
                in_arrangement_zone = False
            elif (
                CHAPTER_RE.match(line)
                or PART_RE.match(line)
                or SUBPART_RE.match(line)
                or TITLE_RE.match(line)
                or SECTION_START_RE.match(line)
            ):
                continue
            else:
                # End of arrangement zone when narrative prose begins.
                if len(line.split()) > 5:
                    in_arrangement_zone = False
                else:
                    continue

        if pending_name_node and is_likely_container_name(line):
            pending_name_node.name = line.strip(" :-")
            pending_name_node = None
            continue

        chap = CHAPTER_RE.match(line)
        if chap:
            label = f"CHAPTER {chap.group(1).upper()}"
            name = chap.group(2).strip(" :-")
            chapter = Node(type="chapter", label=label, name=name)
            add_node(chapter, allowed_parent_types=("part", "subpart", "title"))
            in_schedule_zone = False
            continue

        part = PART_RE.match(line)
        if part:
            node_stack.clear()
            label = f"PART {part.group(1).upper()}"
            name = part.group(2).strip(" :-")
            part_node = Node(type="part", label=label, name=name)
            add_node(part_node, allowed_parent_types=())
            in_schedule_zone = False
            continue

        subpart = SUBPART_RE.match(line)
        if subpart:
            label = f"SUBPART {subpart.group(1).upper()}"
            name = subpart.group(2).strip(" :-")
            subpart_node = Node(type="subpart", label=label, name=name)
            add_node(subpart_node, allowed_parent_types=("part",))
            in_schedule_zone = False
            continue

        title = TITLE_RE.match(line)
        if title:
            label = f"TITLE {title.group(1).upper()}"
            name = title.group(2).strip(" :-")
            title_node = Node(type="title", label=label, name=name)
            add_node(title_node, allowed_parent_types=("chapter", "part", "subpart"))
            in_schedule_zone = False
            continue

        if SCHEDULE_RE.match(line):
            close_section()
            in_schedule_zone = True
            schedule_no = line.strip()
            schedules.append(
                {
                    "schedule_no": schedule_no,
                    "title": "",
                    "pdf_url": "",
                    "local_pdf_path": "",
                }
            )
            continue

        sec_match = SECTION_START_RE.match(line)
        if sec_match and not in_schedule_zone:
            sec_no = sec_match.group("num")
            rest = (sec_match.group("rest") or "").strip()

            # Guard against plain numbered bullets/sub-clauses.
            if rest and len(rest) > 2 and not re.match(r"^\(?[a-zivx]+\)", rest, re.IGNORECASE):
                close_section()
                heading, inline_body = split_heading_and_body(rest)
                current_section = Section(section_no=sec_no, heading=heading)
                if inline_body:
                    current_section.content_lines.append(inline_body)
                continue

        # Plain content line.
        if in_schedule_zone and schedules:
            if not schedules[-1]["title"]:
                schedules[-1]["title"] = line
            else:
                schedules[-1]["title"] += " " + line
            continue

        if current_section is not None:
            # Footnote heuristic: line starts with bracketed numeric marker.
            if re.match(r"^\[\d+\]", line):
                current_section.footnote_lines.append(line)
            else:
                current_section.content_lines.append(line)
            continue

        if not body and not uncategorised_sections and not in_arrangement_zone:
            preamble_lines.append(line)

    close_section()
    return body, uncategorised_sections, schedules, preamble_lines


def build_payload(pdf_path: Path) -> Dict:
    pages = extract_pdf_pages(pdf_path)
    lines = normalize_lines(pages)

    base = parse_metadata(lines, pdf_path)
    body, uncategorised_sections, schedules, preamble_lines = parse_structure(lines)

    act_title_key = base["title"]
    preamble_raw = "\n".join(preamble_lines).strip()

    payload = {
        **base,
        act_title_key: {
            "content_html": to_html(preamble_raw),
            "content_raw": preamble_raw,
        },
        "pdfs": [
            {
                "lang": "en",
                "url": "",
                "filename": pdf_path.name,
                "local_path": str(pdf_path),
            }
        ],
        "body": [node.to_dict() for node in body],
        "uncategorised_sections": [s.to_dict() for s in uncategorised_sections],
        "schedules": schedules,
        "subordinate_legislation": {},
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert Bare Act PDF to act-model JSON")
    parser.add_argument("pdf_path", type=Path, help="Input PDF path")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output JSON path (default: <pdf_stem>.json)",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation level (default: 2)",
    )
    args = parser.parse_args()

    output = args.output or args.pdf_path.with_suffix(".json")
    payload = build_payload(args.pdf_path)
    output.write_text(json.dumps(payload, indent=args.indent, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()

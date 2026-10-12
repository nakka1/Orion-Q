"""Reconstruct real EVA PTY cells as PNGs from the public evidence projection.

Received characters and attributes are preserved. This is a cell reconstruction,
not a native screenshot or a rendering of backend objects.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont


_path = Path(__file__).with_name("render_terminal_dashboard_previews.py")
_spec = importlib.util.spec_from_file_location("eva_preview_colors", _path)
_helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helper)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--font-directory", default=Path("/usr/share/fonts/truetype/dejavu"), type=Path)
    parser.add_argument("--label", action="append")
    args = parser.parse_args(argv)
    report = json.loads(args.campaign.read_text(encoding="utf-8"))
    if report["fixture_only"] is not True or report["pty"]["actual_curses"] is not True:
        raise ValueError("actual fixture-only curses evidence required")
    if args.output.exists():
        raise ValueError("preview destination must be new")
    frames = report["pty"]["public_previews"]
    if args.label:
        frames = [value for value in frames if value["label"] in args.label]
    if not 1 <= len(frames) <= 24:
        raise ValueError("bounded captured viewport count required")
    normal = ImageFont.truetype(str(args.font_directory / "DejaVuSansMono.ttf"), 16)
    bold = ImageFont.truetype(str(args.font_directory / "DejaVuSansMono-Bold.ttf"), 16)
    braille = ImageFont.truetype(str(args.font_directory / "DejaVuSans.ttf"), 13)
    cell, line_height, padding = normal.getlength("M"), 21, 12
    background, foreground = "#121212", "#e5e5e5"
    manifest = {"schema": "orion.terminal-eva.preview.v1",
                "source": "actual Linux curses PTY current cells; PNG reconstruction, not native screenshot",
                "screen_parser": report["pty"]["screen_parser"], "fixture_only": True,
                "hardware_qualified": False, "campaign_status": report["status"],
                "campaign_sha256": hashlib.sha256(args.campaign.read_bytes()).hexdigest(),
                "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "color_helper_sha256": hashlib.sha256(_path.read_bytes()).hexdigest(),
                "redaction": "Peer identity and local test account explicitly replaced in public projection; masked phrase only",
                "font": "DejaVu Sans Mono 16 px; Braille fallback DejaVu Sans 13 px in fixed cells",
                "default_background": background, "images": []}
    args.output.mkdir(parents=True)
    for index, frame in enumerate(frames):
        columns, rows = frame["columns"], frame["rows"]
        if not 1 <= columns <= 240 or not 1 <= rows <= 100 or len(frame["lines"]) != rows:
            raise ValueError("unexpected viewport geometry")
        picture = Image.new("RGB", (round(columns * cell + padding * 2), rows * line_height + padding * 2), background)
        draw = ImageDraw.Draw(picture)
        for row_index, row in enumerate(frame["lines"]):
            row_text = row["text"]
            if "thirdpilot" in row_text.casefold():
                raise ValueError("unmasked local phrase cannot be exported")
            if row_index == 1 and row_text.startswith("Peer: ") and " | received age " in row_text:
                start, end = 6, row_text.index(" | received age ")
                if row_text[start:end].strip() != "<test identity redacted>":
                    raise ValueError("public projection must explicitly redact Peer identity")
            if row_text.startswith("Display login:") and row_text.rstrip() != "Display login: <local test account redacted>":
                raise ValueError("public projection must explicitly redact local account")
            for run in row["runs"]:
                start, length = run["column"], run["length"]
                if start < 0 or length < 1 or start + length > columns:
                    raise ValueError("invalid captured cell run")
                fg = _helper.color(run["fg"], foreground)
                bg = _helper.color(run.get("bg", "default"), background)
                if run.get("reverse"):
                    fg, bg = bg, fg
                x, y = padding + start * cell, padding + row_index * line_height
                draw.rectangle((x, y, x + length * cell, y + line_height), fill=bg)
                font = bold if run["bold"] else normal
                for offset, char in enumerate(row_text[start:start + length]):
                    draw.text((x + offset * cell, y), char,
                              font=braille if 0x2800 <= ord(char) <= 0x28ff else font, fill=fg)
                if run.get("underscore"):
                    draw.line((x, y + line_height - 2, x + length * cell, y + line_height - 2), fill=fg)
        name = f"{index:02d}-" + re.sub(r"[^a-z0-9]+", "-", frame["label"].lower()).strip("-") + ".png"
        path = args.output / name
        picture.save(path)
        manifest["images"].append({"path": name, "label": frame["label"], "columns": columns, "rows": rows,
                                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
    (args.output / "preview-integrity.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"images": len(manifest["images"]), "status": "PASS"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Project actual pyte dashboard cells/attributes into inspectable PNGs.

This draws captured Linux curses viewports. It does not render a reconstructed
backend snapshot, generate illustrative charts, or add UI decorations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont


COLORS = {"black": "#000000", "red": "#cd0000", "green": "#00cd00",
          "brown": "#cdcd00", "blue": "#0000ee", "magenta": "#cd00cd",
          "cyan": "#00cdcd", "white": "#e5e5e5", "brightblack": "#7f7f7f",
          "brightred": "#ff0000", "brightgreen": "#00ff00", "brightbrown": "#ffff00",
          "brightblue": "#5c5cff", "brightmagenta": "#ff00ff", "brightcyan": "#00ffff",
          "brightwhite": "#ffffff"}


def color(value, default):
    if value == "default":
        return default
    if re.fullmatch(r"[0-9a-fA-F]{6}", value):
        return "#" + value
    return COLORS[value]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--font-directory", default=Path("/usr/share/fonts/truetype/dejavu"), type=Path)
    parser.add_argument("--label", action="append", help="Render only these captured viewport labels")
    args = parser.parse_args(argv)
    report = json.loads(args.campaign.read_text(encoding="utf-8"))
    if report["fixture_only"] is not True or report["pty"]["actual_curses"] is not True:
        raise ValueError("actual fixture-only curses evidence is required")
    if args.output.exists():
        raise ValueError("preview output must be new")
    frames = report["pty"]["public_previews"]
    if args.label:
        frames = [frame for frame in frames if frame["label"] in args.label]
    if not 1 <= len(frames) <= 24:
        raise ValueError("bounded captured viewport count required")
    args.output.mkdir(parents=True)
    normal = ImageFont.truetype(str(args.font_directory / "DejaVuSansMono.ttf"), 16)
    bold = ImageFont.truetype(str(args.font_directory / "DejaVuSansMono-Bold.ttf"), 16)
    # The monospace distribution omits Braille. Use an explicitly declared
    # glyph fallback in the captured cell, without replacing or adding dots.
    braille = ImageFont.truetype(str(args.font_directory / "DejaVuSans.ttf"), 13)
    cell, line_height, padding = normal.getlength("M"), 21, 12
    background, foreground = "#121212", "#e5e5e5"
    manifest = {"schema": "orion.terminal-dashboard.preview.v1",
                "source": "actual Linux curses PTY current viewports, captured by " + report["pty"]["screen_parser"],
                "fixture_only": True, "hardware_qualified": False,
                "campaign_status": report["status"], "pty_status": report["pty"]["status"],
                "campaign_sha256": hashlib.sha256(args.campaign.read_bytes()).hexdigest(),
                "redaction": "Peer identity field replaced with <test identity redacted>; simulated parameters/results unchanged",
                "font": "DejaVu Sans Mono 16 px; Braille fallback DejaVu Sans 13 px in fixed captured cells",
                "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "default_background": background, "images": []}
    for index, frame in enumerate(frames):
        columns, rows = frame["columns"], frame["rows"]
        if not 1 <= columns <= 200 or not 1 <= rows <= 60 or len(frame["lines"]) != rows:
            raise ValueError("unexpected viewport geometry")
        picture = Image.new("RGB", (round(columns * cell + padding * 2), rows * line_height + padding * 2), background)
        draw = ImageDraw.Draw(picture)
        for row_index, row in enumerate(frame["lines"]):
            row_text = row["text"]
            if row_index == 1 and row_text.startswith("Peer: ") and " | received age " in row_text:
                start, end = 6, row_text.index(" | received age ")
                original = row_text[start:end]
                if original not in {"not authenticated", "identity not published by mixed status"}:
                    if len(original) < len("<test identity redacted>"):
                        raise ValueError("identity field is too short for explicit redaction")
                    row_text = row_text[:start] + "<test identity redacted>".ljust(end - start) + row_text[end:]
            for run in row["runs"]:
                start, length = run["column"], run["length"]
                if start < 0 or length < 1 or start + length > columns:
                    raise ValueError("invalid captured cell run")
                fg, bg = color(run["fg"], foreground), color(run.get("bg", "default"), background)
                if run.get("reverse"):
                    fg, bg = bg, fg
                x, y = padding + start * cell, padding + row_index * line_height
                draw.rectangle((x, y, x + length * cell, y + line_height), fill=bg)
                text = row_text[start:start + length]
                selected_font = bold if run["bold"] else normal
                for offset, char in enumerate(text):
                    glyph_font = braille if 0x2800 <= ord(char) <= 0x28ff else selected_font
                    draw.text((x + offset * cell, y), char, font=glyph_font, fill=fg)
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

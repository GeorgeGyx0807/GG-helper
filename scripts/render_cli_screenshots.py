"""Render README terminal screenshots from the current Poppy CLI strings."""

import os
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont

from poppy.cli import HELP_DETAILS, build_arg_parser, build_welcome


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "screenshots"
FONT_PATH = "/System/Library/Fonts/SFNSMono.ttf"
BACKGROUND = "#0f1726"
TITLEBAR = "#1f2a3a"
FOREGROUND = "#d7dce5"
BORDER = "#34445e"


def render_terminal(filename, title, body, width=1440, font_size=21):
    font = ImageFont.truetype(FONT_PATH, font_size)
    title_font = ImageFont.truetype(FONT_PATH, font_size)
    lines = body.rstrip().splitlines()
    line_height = font_size + 8
    height = max(520, 86 + 32 + len(lines) * line_height)
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, width - 8, height - 8), radius=24, fill=BACKGROUND, outline=BORDER, width=2)
    draw.rounded_rectangle((8, 8, width - 8, 66), radius=24, fill=TITLEBAR)
    draw.rectangle((8, 42, width - 8, 66), fill=TITLEBAR)
    for x, color in ((32, "#ff5f57"), (56, "#febc2e"), (80, "#28c840")):
        draw.ellipse((x - 8, 29 - 8, x + 8, 29 + 8), fill=color)
    draw.text((116, 18), title, font=title_font, fill=FOREGROUND)
    y = 86
    for line in lines:
        draw.text((30, y), line, font=font, fill=FOREGROUND)
        y += line_height
    OUTPUT.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT / filename, optimize=True)


def welcome_text():
    os.environ["COLUMNS"] = "84"
    agent = SimpleNamespace(
        workspace=SimpleNamespace(cwd="/Users/george/Desktop/poppy", branch="main"),
        approval_policy="ask",
        session={"id": "20260717-102516-poppy"},
    )
    return build_welcome(agent, model="deepseek-v4-pro", host="https://api.deepseek.com/anthropic")


def main():
    os.environ["COLUMNS"] = "92"
    parser = build_arg_parser()
    parser.prog = "poppy"
    render_terminal(
        "poppy-help.png",
        "real terminal: uv run poppy --help",
        parser.format_help(),
        width=1480,
        font_size=19,
    )
    welcome = welcome_text()
    render_terminal(
        "poppy-start.png",
        "real terminal: uv run poppy",
        f"{welcome}\n\npoppy>",
        width=1460,
        font_size=20,
    )
    repl = f"{welcome}\n\npoppy> /help\n{HELP_DETAILS}\n\npoppy> /session\n/Users/george/Desktop/poppy/.poppy/sessions/20260717-102516-poppy.json\n\npoppy>"
    render_terminal(
        "poppy-repl.png",
        "real terminal: /help and /session",
        repl,
        width=1460,
        font_size=20,
    )


if __name__ == "__main__":
    main()

"""Package browser-captured demo states into a captioned README GIF.

Capture the seven named JPEGs through the running app first; this script does
not fabricate UI states, change screenshot contents, or operate a browser.
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


STEPS = [
    ("01-start.jpg", "Start with one click", "No credentials needed. The isolated demo keeps your workspace untouched.", 2500),
    ("02-guide.jpg", "Compare three contractual outcomes", "$900 supported, one missing ticket, and one excluded maintenance window.", 4000),
    ("03-refusal.jpg", "Know when a claim is not supported", "Cobalt's scheduled maintenance is excluded. Its amount stays out of the total.", 4000),
    ("04-ticket-arrived.jpg", "New evidence changes the answer", "Harbor's timely ticket adds $480. Supported requests now total $1,380.", 4000),
    ("05-review.jpg", "Inspect the calculation before accepting", "62 qualifying minutes, 99.85648% uptime, and the 10% credit tier.", 4500),
    ("06-saved-draft.jpg", "Finance keeps the approval step", "Harbor's $480 request is saved as a draft. No external submission.", 3000),
    ("07-challenge.jpg", "Challenge the evidence, protect the saved draft", "Removing the required ticket changes $480 to $0 in an isolated what-if.", 6000),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    font_path = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
    font = lambda size: ImageFont.truetype(str(font_path), size) if font_path.exists() else ImageFont.load_default(size=size)
    frames = []
    for index, (name, title, subtitle, _) in enumerate(STEPS, 1):
        with Image.open(args.frames / name) as source:
            source = source.convert("RGB")
            frame = Image.new("RGB", (source.width, source.height + 96), "#f7f8f3")
            frame.paste(source, (0, 96))
        draw = ImageDraw.Draw(frame)
        draw.rectangle((0, 0, 7, 95), fill="#dfee78")
        draw.text((25, 14), title, font=font(24), fill="#252a21")
        draw.text((25, 52), subtitle, font=font(17), fill="#626c55")
        draw.text((frame.width - 152, 15), f"{index} / {len(STEPS)}   DEMO", font=font(16), fill="#626c55")
        draw.line((0, 95, frame.width, 95), fill="#dce1d2", width=1)
        frames.append(frame.quantize(colors=192, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    if len({frame.size for frame in frames}) != 1:
        raise SystemExit("Use the same viewport for all seven captures.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(args.output, save_all=True, append_images=frames[1:],
                   duration=[s[3] for s in STEPS], loop=0, disposal=2, optimize=True)
    with Image.open(args.output) as result:
        assert result.n_frames == len(STEPS)
        duration = 0
        for i in range(result.n_frames):
            result.seek(i)
            duration += result.info["duration"]
        print(f"{args.output}: {result.n_frames} frames, {duration / 1000:g} seconds, {args.output.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()

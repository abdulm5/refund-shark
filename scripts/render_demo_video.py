"""Render a 150-second, 1080p motion-designed demo from genuine browser captures.

Python/Pillow composes the frames; the small native macOS encoder writes H.264.
No generated product screens, voice cloning, music licensing, or external service.
Use --preview for storyboard images, --seconds 4 for a short encode check.
"""
import argparse
import json
import math
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
CAPTURES = ROOT / ".cache/video-capture"
OUT = ROOT / "docs/demo"
W, H, FPS, DURATION = 1920, 1080, 30, 150
INK = "#12221f"
PAPER = "#f7f8f2"
LIME = "#e5f253"
MUTED = "#a7b6ad"
STAGE = (64, 164, 1792, 820)
ASPECT = STAGE[2] / STAGE[3]
FONT_DIR = Path("/System/Library/Fonts/Supplemental")


@lru_cache(maxsize=100)
def font(size, weight="regular"):
    names = {"regular": "Arial.ttf", "bold": "Arial Bold.ttf", "black": "Arial Black.ttf", "mono": "Courier New.ttf"}
    return ImageFont.truetype(str(FONT_DIR / names[weight]), size)


def ease(v):
    x = max(0, min(1, v))
    return x * x * x * (x * (x * 6 - 15) + 10)


def mix(a, b, p):
    return a + (b - a) * p


def text(draw, xy, value, size, color=PAPER, weight="regular", anchor=None):
    draw.text(xy, value, font=font(size, weight), fill=color, anchor=anchor, stroke_width=0)


def curve(points, steps=30):
    p0, p1, p2, p3 = points
    return [((1-t)**3*p0[0]+3*(1-t)**2*t*p1[0]+3*(1-t)*t*t*p2[0]+t**3*p3[0],
             (1-t)**3*p0[1]+3*(1-t)**2*t*p1[1]+3*(1-t)*t*t*p2[1]+t**3*p3[1]) for t in [n/steps for n in range(steps+1)]]


@lru_cache(maxsize=10)
def logo(size):
    im = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, size-1, size-1), radius=size/4, fill=LIME)
    scale = size / 64
    points = curve([(12, 45), (20, 42), (25, 29), (33, 15)]) + curve([(33, 15), (32, 28), (40, 34), (52, 45)])
    d.polygon([(x*scale, y*scale) for x, y in points], fill=INK)
    wave = curve([(12, 49), (19, 45), (24, 53), (32, 49)]) + curve([(32, 49), (40, 45), (44, 52), (52, 49)])
    d.line([(x*scale, y*scale) for x, y in wave], fill=INK, width=max(2, round(3*scale)))
    return im


@lru_cache(maxsize=3)
def background(light=False):
    im = Image.new("RGB", (W, H), PAPER if light else INK)
    d = ImageDraw.Draw(im)
    # A quiet accounting-paper grid. The moving lime line links each evidentiary step.
    grid = "#e9ece2" if light else "#1a2c27"
    for x in range(64, W, 128):
        d.line((x, 0, x, H), fill=grid, width=1)
    for y in range(64, H, 128):
        d.line((0, y, W, y), fill=grid, width=1)
    return im


SCENES = [
    dict(start=0, end=7, kind="intro", title="Find the credit. Prove the claim."),
    dict(start=7, end=15, shot="01-overview", chapter="THE OPPORTUNITY", title="Your contracts have teeth.", note="One-click local demo. No provider credentials needed.", cameras=[(0, (0, 0, 1440)), (2, (255, 68, 1160)), (7, (285, 84, 1075))]),
    dict(start=15, end=24, shot="14b-terms", chapter="READ THE AGREEMENT", title="Start with what the contract actually says.", note="Saved Atlassian agreement · terms linked to source quotations", cameras=[(0, (280, 20, 1110)), (3, (290, 240, 1095)), (8, (645, 337, 740))]),
    dict(start=24, end=34, shot="03-excluded", chapter="SCAN THE PORTFOLIO", title="One outage. Three different outcomes.", note="Northstar: $900 supported  /  Harbor: missing evidence  /  Cobalt: excluded", cameras=[(0, (250, 90, 1165)), (3, (270, 170, 1130)), (9, (282, 200, 1105))]),
    dict(start=34, end=43, shot="03-excluded", chapter="EXCLUDE UNSUPPORTED CLAIMS", title="No qualifying downtime. No claim.", note="Cobalt’s error minutes fall inside confirmed scheduled maintenance.", cameras=[(0, (265, 430, 1130)), (3, (290, 480, 1080)), (8, (285, 495, 1085))]),
    dict(start=43, end=51, shot="04-missing", chapter="IDENTIFY WHAT IS MISSING", title="A missing ticket stops the request.", note="Harbor stays out of the supported total until the required evidence arrives.", cameras=[(0, (265, 270, 1135)), (3, (290, 420, 1090)), (7, (297, 445, 1060))]),
    dict(start=51, end=63, shot="05-ticket-arrived", chapter="RECALCULATE WHEN EVIDENCE ARRIVES", title="$900 → $1,380. Evidence changes the answer.", note="Harbor’s timely support ticket unlocks a $480 proposal. Cobalt still stays excluded.", cameras=[(0, (245, 235, 1170)), (3, (265, 480, 1140)), (7, (275, 485, 660)), (11, (280, 492, 640))]),
    dict(start=63, end=77, shot="06-calculation", chapter="EXPLAIN EVERY DOLLAR", title="62 minutes. A 10% tier. A $480 request.", note="Contractual metric + customer evidence + affected-product invoice", cameras=[(0, (270, 40, 1140)), (5, (300, 80, 1080)), (9, (308, 145, 1060)), (13, (310, 185, 1050))]),
    dict(start=77, end=86, shot="07-request", chapter="PREPARE THE REQUEST", title="A draft finance can actually review.", note="Account, incident, calculation, and original clause—in one prepared request.", cameras=[(0, (300, 50, 1070)), (3, (310, 150, 1045)), (8, (315, 260, 1035))]),
    dict(start=86, end=94, shot="08-accept-before", chapter="KEEP FINANCE IN CONTROL", title="Review first. Then save the draft.", note="Accepting a proposal does not approve or send the claim.", cameras=[(0, (280, 350, 1110)), (2, (290, 405, 1090)), (7, (295, 410, 1080))], action="accept"),
    dict(start=94, end=99, kind="challenge_title", title="Now try to break it."),
    dict(start=99, end=112, shot="12-ticket-challenge", chapter="CHALLENGE THE CLAIM", title="Remove the ticket. $480 becomes $0.", note="The actual engine recalculates a copy. The saved draft stays untouched.", cameras=[(0, (285, 65, 1100)), (3, (650, 145, 735)), (8, (658, 205, 720)), (12, (657, 230, 720))]),
    dict(start=112, end=122, shot="13-duplicate-challenge", chapter="MAKE DUPLICATES HARMLESS", title="Same evidence. Same $480.", note="Importing a measurement twice does not double the service credit.", cameras=[(0, (285, 60, 1100)), (3, (655, 145, 725)), (9, (657, 193, 720))]),
    dict(start=122, end=133, kind="proof", title="Take the evidence with you."),
    dict(start=133, end=143, shot="16-credit-matched", chapter="CLOSE THE LOOP", title="Confirm the credit on a later invoice.", note="Separate Northstar replay · simulated $900 invoice credit · no real submission", cameras=[(0, (280, 260, 1100)), (3, (295, 350, 1080)), (9, (302, 380, 1060))]),
    dict(start=143, end=150, kind="outro", title="Models read. Code decides. Finance approves."),
]


class Film:
    def __init__(self):
        names = {s["shot"] for s in SCENES if "shot" in s} | {"09-accept-checked", "10-draft-saved", "11-challenges"}
        self.images = {name: Image.open(CAPTURES / f"{name}.png").convert("RGB") for name in names}
        self.verification = json.loads((CAPTURES / "bundle-verification.json").read_text())
        assert self.verification["verified"] and self.verification["amount"] == "480.00"
        self.mask = Image.new("L", STAGE[2:], 0)
        ImageDraw.Draw(self.mask).rounded_rectangle((0, 0, STAGE[2]-1, STAGE[3]-1), radius=22, fill=255)
        self.last_transitions = {}

    def frame(self, t, transitions=True):
        index = next(i for i, s in enumerate(SCENES) if s["start"] <= t < s["end"])
        s = SCENES[index]
        im = self.scene(s, t-s["start"])
        if transitions and index and t-s["start"] < .45:
            if index not in self.last_transitions:
                self.last_transitions[index] = self.scene(SCENES[index-1], SCENES[index-1]["end"]-SCENES[index-1]["start"]-.001)
            im = Image.blend(self.last_transitions[index], im, ease((t-s["start"])/.45))
        d = ImageDraw.Draw(im)
        # Timeline maps to the actual chapter durations, rather than decorative numbering.
        d.line((64, 1046, 1856, 1046), fill="#3a4b40", width=2)
        d.line((64, 1046, 64+1792*t/DURATION, 1046), fill=LIME, width=4)
        for scene in SCENES[1:]:
            x = 64+1792*scene["start"]/DURATION
            d.rectangle((x-2, 1042, x+2, 1050), fill=INK)
        return im

    def scene(self, scene, local):
        kind = scene.get("kind")
        if kind == "intro": return self.intro(local)
        if kind == "outro": return self.outro(local)
        if kind == "challenge_title": return self.challenge_title(local)
        if kind == "proof": return self.proof(local)
        return self.product(scene, local)

    def header(self, im, chapter, title):
        d = ImageDraw.Draw(im)
        im.paste(logo(42), (66, 38), logo(42))
        text(d, (123, 44), "refundshark.", 27, weight="bold")
        text(d, (1854, 47), chapter, 17, LIME, "bold", "ra")
        text(d, (64, 98), title, 38, weight="bold")

    def camera(self, scene, local):
        points = scene["cameras"]
        for (start, a), (end, b) in zip(points, points[1:]):
            if local < end:
                return tuple(mix(x, y, ease((local-start)/(end-start))) for x, y in zip(a, b))
        return points[-1][1]

    def product(self, scene, local):
        im = background().copy()
        self.header(im, scene["chapter"], scene["title"])
        name = scene["shot"]
        if scene.get("action") == "accept":
            name = "09-accept-checked" if local >= 3.2 else name
            if local >= 5.8:
                name = "11-challenges"
        source = self.images[name]
        x, y, width = self.camera(scene, local)
        if scene.get("action") == "accept" and local >= 5.8:
            x, y, width = 280, 45, 1110
        height = width/ASPECT
        # All captures are 2x. Clamp the camera so no edge stretches or invented pixels appear.
        x = max(0, min(1440-width, x)); y = max(0, min(900-height, y))
        panel = source.transform(STAGE[2:], Image.Transform.EXTENT, (2*x, 2*y, 2*(x+width), 2*(y+height)), Image.Resampling.BICUBIC)
        im.paste(panel, STAGE[:2], self.mask)
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((64, 164, 1856, 984), radius=22, outline="#5b6b57", width=1)
        text(d, (65, 1003), scene["note"], 20, MUTED)
        if scene.get("action") == "accept" and 1.2 < local < 5.7:
            # Editorial cursor points to the real checkbox/button, then clicks at the state transition.
            target = (325, 495) if local < 3.2 else (420, 530)
            p = ease((local-1.2)/1.25) if local < 3.2 else ease((local-3.2)/.7)
            origin = (1050, 650) if local < 3.2 else (325, 495)
            cx, cy = mix(origin[0], target[0], p), mix(origin[1], target[1], p)
            sx, sy = 64+(cx-x)/width*1792, 164+(cy-y)/height*820
            self.cursor(im, sx, sy, local)
        return im

    def cursor(self, im, x, y, t):
        d = ImageDraw.Draw(im)
        phase = min(abs(t-3.15), abs(t-5.55))
        if phase < .42:
            radius = 16+(1-phase/.42)*31
            d.ellipse((x-radius, y-radius, x+radius, y+radius), outline="#b7ca30", width=3)
        points = [(x, y), (x+3, y+31), (x+11, y+23), (x+19, y+36), (x+25, y+32), (x+17, y+19), (x+29, y+18)]
        d.polygon(points, fill=INK, outline="white", width=2)

    def evidence_line(self, im, local, y):
        d = ImageDraw.Draw(im)
        labels = ["CONTRACT", "CUSTOMER EVIDENCE", "INVOICE", "SUPPORTED REQUEST"]
        xs = [128, 590, 1040, 1495]
        d.line((128, y, 1765, y), fill="#3a4b40", width=2)
        progress = ease((local-.5)/4)
        end = 128+1637*progress
        d.line((128, y, end, y), fill=LIME, width=3)
        for x, label in zip(xs, labels):
            active = x <= end
            d.ellipse((x-7, y-7, x+7, y+7), fill=LIME if active else INK, outline=LIME if active else "#66816f", width=2)
            text(d, (x, y+24), label, 18, LIME if active else MUTED, "bold")

    def intro(self, local):
        im = background().copy();d=ImageDraw.Draw(im)
        p = ease(local/.9)
        im.paste(logo(80), (116, 100), logo(80))
        text(d, (220, 112), "refundshark.", 48, weight="bold")
        text(d, (120, 270), "DOWNTIME HAPPENED.", 23, LIME, "bold")
        y = int(340+35*(1-p))
        text(d, (110, y), "Find the credit.", 118, weight="bold")
        text(d, (110, y+140), "Prove the claim.", 118, LIME, "bold")
        text(d, (120, 682), "From SLA terms to an evidence-backed service-credit request.", 30, MUTED)
        self.evidence_line(im, local, 835)
        text(d, (120, 967), "LOCAL PRODUCT DEMO  /  SIMULATED CUSTOMER RECORDS", 17, MUTED)
        return im

    def challenge_title(self, local):
        im = background(True).copy();d=ImageDraw.Draw(im)
        im.paste(logo(58), (116, 90), logo(58))
        text(d, (197, 100), "refundshark.", 34, INK, "bold")
        text(d, (118, 285), "THE IMPORTANT QUESTION", 22, "#5a6854", "bold")
        text(d, (108, 375-int(12*ease(local))), "Does the claim", 112, INK, "bold")
        text(d, (108, 510-int(12*ease(local))), "still hold up?", 112, INK, "bold")
        labels = ["Remove a required ticket", "Import the same evidence twice", "Recalculate. Don’t guess."]
        for i, label in enumerate(labels):
            if local > .6+i*.5:
                yy = 725+i*60
                d.ellipse((122, yy+8, 134, yy+20), fill="#5a7440")
                text(d, (156, yy), label, 29, INK, "bold" if i==2 else "regular")
        return im

    def proof(self, local):
        im=background().copy();self.header(im,"VERIFY IT OUTSIDE THE APP","Take the evidence with you.")
        d=ImageDraw.Draw(im)
        d.rounded_rectangle((92,190,1828,948),radius=22,fill="#081310",outline="#3b5144",width=2)
        for x,color in [(129,"#d8776a"),(159,"#d9bb61"),(189,"#8aaf73")]:d.ellipse((x,217,x+12,229),fill=color)
        text(d,(960,211),"OFFLINE EVIDENCE VERIFICATION",17,MUTED,"bold","ma")
        d.line((92,254,1828,254),fill="#2a3b30",width=1)
        cmd="$ python -m shark.bundle verify harbor-evidence.zip"
        text(d,(135,290),cmd[:int(max(0,local-.4)*35)],28,LIME,"mono")
        lines=[("{",PAPER),('  "verified": true,',LIME),
               (f'  "files_checked": {self.verification["files_checked"]},',PAPER),
               ('  "simulated": true,',MUTED),('  "outcome": "eligible",',PAPER),
               (f'  "amount": "{self.verification["amount"]}",',LIME),('  "currency": "USD"',PAPER),("}",PAPER)]
        count=min(len(lines),max(0,int((local-2.1)*5)))
        for i,(value,color) in enumerate(lines[:count]):text(d,(135,370+i*49),value,32,color,"mono")
        if local>4.1:
            text(d,(1015,440),"Same evidence.",42,weight="bold")
            text(d,(1015,500),"Same $480.",61,LIME,"bold")
            text(d,(1015,600),"File hashes checked",25,MUTED)
            text(d,(1015,643),"Request logs remeasured",25,MUTED)
            text(d,(1015,686),"Decision reproduced",25,MUTED)
        text(d,(96,991),"Verifies internal consistency. The vendor still decides whether to award the credit.",21,MUTED)
        return im

    def outro(self, local):
        im=background().copy();d=ImageDraw.Draw(im)
        im.paste(logo(86),(115,95),logo(86));text(d,(225,109),"refundshark.",49,weight="bold")
        phrases=[("Models read.",PAPER),("Code decides.",PAPER),("Finance approves.",LIME)]
        for i,(phrase,color) in enumerate(phrases):
            p=ease((local-i*.45)/.75)
            text(d,(110+int(35*(1-p)),290+i*125),phrase,97,color,"bold")
        d.line((120,737,1785,737),fill="#415447",width=1)
        text(d,(120,790),"Try the local demo.",43,weight="bold")
        text(d,(120,857),"One repository. A complete, reviewable workflow.",27,MUTED)
        d.rounded_rectangle((1325,787,1770,851),radius=32,fill=LIME)
        text(d,(1547,805),"318 TESTS PASSING",24,INK,"bold","ma")
        text(d,(120,978),"Simulated customers, submissions, and invoice credits. No real money moved.",18,MUTED)
        return im


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview",action="store_true")
    parser.add_argument("--seconds",type=float,default=DURATION)
    parser.add_argument("--output",type=Path,default=OUT/"refund-shark-demo-1080p.mp4")
    args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    film=Film()
    if args.preview:
        previews=CAPTURES/"storyboard";previews.mkdir(exist_ok=True)
        for i,s in enumerate(SCENES):
            t=min(s["end"]-.1,s["start"]+max(3,(s["end"]-s["start"])*.65))
            film.frame(t).save(previews/f"{i+1:02}-{int(t):03}s.jpg",quality=92)
        thumb=film.frame(4);thumb.save(OUT/"poster.jpg",quality=94)
        sheet=Image.new("RGB",(1920,4*310),INK);d=ImageDraw.Draw(sheet)
        for i,p in enumerate(sorted(previews.glob('*.jpg'))):
            frame=Image.open(p).resize((480,270));x=i%4*480;y=i//4*310
            sheet.paste(frame,(x,y+30));text(d,(x+8,y+4),p.stem,18)
        sheet.save(previews/"contact-sheet.jpg",quality=90)
        print(previews/"contact-sheet.jpg");return
    encoder=ROOT/".cache/video-encoder"
    if not encoder.exists():raise SystemExit("Compile scripts/video_encoder.swift first.")
    args.output.parent.mkdir(parents=True,exist_ok=True)
    process=subprocess.Popen([str(encoder),"encode",str(args.output),str(W),str(H),str(FPS)],stdin=subprocess.PIPE)
    total=round(min(args.seconds,DURATION)*FPS)
    try:
        for n in range(total):
            frame=film.frame(n/FPS)
            process.stdin.write(frame.convert("RGBA").tobytes("raw","BGRA"))
            if n%(FPS*5)==0:print(f"Rendered {n/FPS:.0f}/{total/FPS:.0f}s",flush=True)
        process.stdin.close()
        if process.wait()!=0:raise SystemExit("Video encoder failed")
    finally:
        if process.poll() is None:process.terminate()
    (OUT/"timeline.json").write_text(json.dumps({"duration_seconds":DURATION,"resolution":[W,H],"fps":FPS,"audio":"silent; ready for the creator’s voice-over","scenes":SCENES},indent=2,ensure_ascii=False)+"\n")
    print(f"Saved {args.output} ({args.output.stat().st_size/1_000_000:.1f} MB)")


if __name__=="__main__":main()

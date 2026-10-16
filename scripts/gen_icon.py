#!/usr/bin/env python3
"""Generate the Claudio app icon: a cartoon bust split half Roman / half developer.

Writes the master SVG and renders PNG sizes with cairosvg.
Run:  python scripts/gen_icon.py
"""
import math
import os

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "public")
OUT_DIR = os.path.abspath(OUT_DIR)

W = 512
CX = 256.0
HEAD_CX = 256.0
HEAD_CY = 200.0
HEAD_RX = 104.0
HEAD_RY = 120.0

# Palette
ROMAN_TOP = "#F7E7C3"
ROMAN_BOT = "#E7C98C"
DEV_TOP = "#16324F"
DEV_BOT = "#0A1626"
GOLD = "#C9A227"
GOLD_DK = "#9C7C1C"
RED = "#A8322D"
LAUREL = "#3F7D3A"
LAUREL_LT = "#5FA854"
SKIN = "#E8B07D"
SKIN_SH = "#D2955F"
HAIR = "#3A2A1E"
TOGA = "#F6F1E3"
TOGA_SH = "#E2D7BF"
HOODIE = "#1F5F6B"
HOODIE_SH = "#154852"
FRAME = "#26262B"
TEAL = "#2AA8B8"


def leaf(cx, cy, angle_deg, length, width):
    a = math.radians(angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    px, py = -math.sin(a), math.cos(a)
    tipx, tipy = cx + dx * length / 2, cy + dy * length / 2
    basx, basy = cx - dx * length / 2, cy - dy * length / 2
    c1x, c1y = cx + px * width / 2, cy + py * width / 2
    c2x, c2y = cx - px * width / 2, cy - py * width / 2
    return (
        f"M {basx:.1f} {basy:.1f} Q {c1x:.1f} {c1y:.1f} {tipx:.1f} {tipy:.1f} "
        f"Q {c2x:.1f} {c2y:.1f} {basx:.1f} {basy:.1f} Z"
    )


def build_svg():
    s = []
    s.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{W}" '
        f'viewBox="0 0 {W} {W}" fill="none">'
    )

    # ---- defs: gradients + clip ----
    s.append("<defs>")
    s.append(
        f'<linearGradient id="roman" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{ROMAN_TOP}"/>'
        f'<stop offset="1" stop-color="{ROMAN_BOT}"/></linearGradient>'
    )
    s.append(
        f'<linearGradient id="dev" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{DEV_TOP}"/>'
        f'<stop offset="1" stop-color="{DEV_BOT}"/></linearGradient>'
    )
    s.append(
        f'<linearGradient id="skin" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{SKIN}"/>'
        f'<stop offset="1" stop-color="{SKIN_SH}"/></linearGradient>'
    )
    s.append(
        f'<radialGradient id="glow" cx="0.5" cy="0.42" r="0.6">'
        f'<stop offset="0" stop-color="#ffffff" stop-opacity="0.18"/>'
        f'<stop offset="1" stop-color="#ffffff" stop-opacity="0"/></radialGradient>'
    )
    s.append(
        f'<clipPath id="squircle"><rect x="0" y="0" width="{W}" height="{W}" rx="112" ry="112"/></clipPath>'
    )
    s.append("</defs>")

    # ---- background split (clipped to squircle) ----
    s.append('<g clip-path="url(#squircle)">')
    s.append(f'<rect x="0" y="0" width="{CX}" height="{W}" fill="url(#roman)"/>')
    s.append(f'<rect x="{CX}" y="0" width="{W - CX}" height="{W}" fill="url(#dev)"/>')

    # left motif: faint column flutes
    s.append(f'<g opacity="0.10" stroke="{GOLD_DK}" stroke-width="6">')
    for x in (34, 60, 86):
        s.append(f'<line x1="{x}" y1="40" x2="{x}" y2="472"/>')
    s.append("</g>")
    # right motif: faint code glyphs
    s.append(
        f'<g opacity="0.12" fill="#7FB4E8" font-family="monospace" font-size="34" font-weight="700">'
        f'<text x="430" y="120">&lt;/&gt;</text>'
        f'<text x="452" y="300" font-size="26">{{ }}</text>'
        f'<text x="410" y="420" font-size="22">();</text>'
        f"</g>"
    )
    # center seam
    s.append(f'<rect x="{CX - 1.5}" y="0" width="3" height="{W}" fill="{GOLD}" opacity="0.55"/>')
    s.append("</g>")

    # ---- torso (split toga / hoodie) ----
    torso = (
        "M 70 512 "
        "C 70 420 120 372 196 360 "
        "L 316 360 "
        "C 392 372 442 420 442 512 Z"
    )
    s.append(f'<clipPath id="torso"><path d="{torso}"/></clipPath>')
    s.append(f'<path d="{torso}" fill="{TOGA}"/>')
    # right half hoodie
    s.append(
        f'<g clip-path="url(#torso)">'
        f'<rect x="{CX}" y="340" width="{W - CX}" height="{W}" fill="{HOODIE}"/>'
        # hoodie center seam
        f'<rect x="{CX - 2}" y="360" width="4" height="160" fill="{HOODIE_SH}"/>'
        # drawstrings
        f'<path d="M 268 372 C 262 410 262 440 268 470" stroke="#EAF6F8" stroke-width="5" fill="none" stroke-linecap="round"/>'
        f'<path d="M 288 372 C 296 410 296 440 290 470" stroke="#EAF6F8" stroke-width="5" fill="none" stroke-linecap="round"/>'
        f'<circle cx="268" cy="472" r="5" fill="{TEAL}"/>'
        f'<circle cx="290" cy="472" r="5" fill="{TEAL}"/>'
        # lanyard + badge on right chest
        f'<path d="M 330 372 L 352 430" stroke="#0E3A44" stroke-width="6" fill="none"/>'
        f'<rect x="338" y="428" width="34" height="44" rx="5" fill="#EAF6F8"/>'
        f'<rect x="344" y="436" width="22" height="6" rx="3" fill="{TEAL}"/>'
        f'<rect x="344" y="450" width="22" height="4" rx="2" fill="#9BB"/>'
        f'<rect x="344" y="458" width="16" height="4" rx="2" fill="#9BB"/>'
        f"</g>"
    )
    # toga folds on left
    s.append(
        f'<g clip-path="url(#torso)" stroke="{TOGA_SH}" stroke-width="4" fill="none" opacity="0.9">'
        f'<path d="M 150 380 C 180 420 180 470 160 512"/>'
        f'<path d="M 196 366 C 210 420 206 470 196 512"/>'
        f'<path d="M 110 410 C 140 450 140 480 128 512"/>'
        f"</g>"
    )
    # toga shoulder drape + gold fibula (left shoulder)
    s.append(
        f'<path d="M 150 372 C 170 350 210 344 240 352 L 236 388 C 206 380 176 386 162 404 Z" '
        f'fill="{TOGA}" stroke="{TOGA_SH}" stroke-width="3"/>'
    )
    s.append(f'<circle cx="196" cy="384" r="13" fill="{GOLD}" stroke="{GOLD_DK}" stroke-width="3"/>')
    s.append(f'<circle cx="196" cy="384" r="4" fill="{GOLD_DK}"/>')

    # ---- neck ----
    s.append(f'<rect x="226" y="292" width="60" height="86" rx="26" fill="url(#skin)"/>')
    s.append(f'<ellipse cx="256" cy="300" rx="34" ry="14" fill="{SKIN_SH}" opacity="0.5"/>')

    # ---- head ----
    s.append(f'<ellipse cx="{HEAD_CX}" cy="{HEAD_CY}" rx="{HEAD_RX}" ry="{HEAD_RY}" fill="url(#skin)"/>')
    # soft glow
    s.append(f'<ellipse cx="{HEAD_CX}" cy="{HEAD_CY}" rx="{HEAD_RX}" ry="{HEAD_RY}" fill="url(#glow)"/>')

    # ears
    s.append(f'<ellipse cx="152" cy="210" rx="16" ry="22" fill="{SKIN}"/>')
    s.append(f'<ellipse cx="360" cy="210" rx="16" ry="22" fill="{SKIN}"/>')

    # hair cap (top of head)
    s.append(
        f'<path d="M 152 196 C 150 120 200 84 256 84 C 312 84 362 120 360 196 '
        f'C 340 168 300 156 256 156 C 212 156 172 168 152 196 Z" fill="{HAIR}"/>'
    )

    # ---- eyes ----
    for ex in (212, 300):
        s.append(f'<ellipse cx="{ex}" cy="206" rx="23" ry="17" fill="#FFFFFF"/>')
        s.append(f'<circle cx="{ex}" cy="208" r="9" fill="#2A2A2E"/>')
        s.append(f'<circle cx="{ex + 3}" cy="205" r="3" fill="#FFFFFF"/>')

    # eyebrows: left calm, right raised
    s.append(f'<path d="M 188 178 C 205 172 224 172 236 178" stroke="{HAIR}" stroke-width="7" fill="none" stroke-linecap="round"/>')
    s.append(f'<path d="M 276 170 C 292 160 312 160 324 168" stroke="{HAIR}" stroke-width="7" fill="none" stroke-linecap="round"/>')

    # nose
    s.append(f'<path d="M 256 214 C 250 236 248 246 258 250" stroke="{SKIN_SH}" stroke-width="5" fill="none" stroke-linecap="round"/>')

    # mouth: slight confident smile
    s.append(f'<path d="M 226 272 C 244 288 268 288 286 272" stroke="#8A4B3A" stroke-width="6" fill="none" stroke-linecap="round"/>')

    # stubble on right jaw only (dev)
    s.append(
        f'<path d="M 300 250 C 320 268 322 296 300 312 C 322 300 336 270 322 244 Z" '
        f'fill="#6B5B4A" opacity="0.35"/>'
    )

    # ---- glasses (dev cue): clear left lens, blue-tinted right lens ----
    s.append(
        f'<g stroke="{FRAME}" stroke-width="6" fill="none">'
        f'<rect x="186" y="188" width="58" height="40" rx="12"/>'
        f'<rect x="268" y="188" width="58" height="40" rx="12"/>'
        f'<path d="M 244 206 C 252 200 260 200 268 206"/>'
        f'<path d="M 326 200 L 356 206"/>'
        f"</g>"
    )
    s.append(f'<rect x="268" y="188" width="58" height="40" rx="12" fill="#3B82F6" opacity="0.30"/>')

    # ---- laurel wreath hugging the LEFT side of the head ----
    # Leaves sit on an ellipse just outside the head, each swept ~45deg off the
    # radius so they read as a laurel crown rather than a sunburst.
    wr_rx = 110.0
    wr_ry = 126.0
    t0, t1 = 130.0, 272.0
    n = 11
    for i in range(n):
        t = math.radians(t0 + (t1 - t0) * i / (n - 1))
        px = HEAD_CX + wr_rx * math.cos(t)
        py = HEAD_CY + wr_ry * math.sin(t)
        radial = math.degrees(math.atan2(py - HEAD_CY, px - HEAD_CX))
        s.append(
            f'<path d="{leaf(px, py, radial - 48, 30, 14)}" '
            f'fill="{LAUREL}" stroke="{LAUREL_LT}" stroke-width="2"/>'
        )
    # small red knot where the wreath ends at the crown
    bx = HEAD_CX + wr_rx * math.cos(math.radians(272))
    by = HEAD_CY + wr_ry * math.sin(math.radians(272))
    s.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="8" fill="{RED}"/>')

    # ---- headphone band over the RIGHT half of the head ----
    s.append(
        f'<path d="M 256 84 A 116 116 0 0 1 360 196" stroke="#2B2B2E" stroke-width="16" fill="none" stroke-linecap="round"/>'
    )
    s.append(
        f'<path d="M 256 84 A 116 116 0 0 1 360 196" stroke="{TEAL}" stroke-width="4" fill="none" stroke-linecap="round" opacity="0.7"/>'
    )
    # ear cup over the right ear
    s.append(f'<rect x="342" y="182" width="40" height="56" rx="16" fill="#2B2B2E"/>')
    s.append(f'<rect x="348" y="190" width="28" height="40" rx="12" fill="#3A3A40"/>')
    s.append(f'<circle cx="362" cy="210" r="9" fill="{TEAL}" opacity="0.85"/>')

    # ---- outer ring ----
    s.append(f'<rect x="3" y="3" width="{W - 6}" height="{W - 6}" rx="110" ry="110" stroke="{GOLD}" stroke-width="6" opacity="0.85"/>')

    s.append("</svg>")
    return "\n".join(s)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    svg = build_svg()
    svg_path = os.path.join(OUT_DIR, "claudio-icon.svg")
    with open(svg_path, "w") as f:
        f.write(svg)
    print("wrote", svg_path)

    import cairosvg
    sizes = [16, 32, 48, 64, 128, 180, 192, 256, 512, 1024]
    for sz in sizes:
        out = os.path.join(OUT_DIR, f"claudio-icon-{sz}.png")
        cairosvg.svg2png(bytestring=svg.encode(), write_to=out, output_width=sz, output_height=sz)
        print("wrote", out)

    # multi-size .ico for the browser favicon
    from PIL import Image
    ico_sizes = [16, 24, 32, 48, 64]
    imgs = []
    for sz in ico_sizes:
        tmp = os.path.join(OUT_DIR, f"_ico_{sz}.png")
        cairosvg.svg2png(bytestring=svg.encode(), write_to=tmp, output_width=sz, output_height=sz)
        imgs.append(Image.open(tmp).convert("RGBA"))
    ico_path = os.path.join(OUT_DIR, "favicon.ico")
    imgs[0].save(ico_path, format="ICO", sizes=[(s, s) for s in ico_sizes],
                 append_images=imgs[1:])
    for sz in ico_sizes:
        os.remove(os.path.join(OUT_DIR, f"_ico_{sz}.png"))
    print("wrote", ico_path)


if __name__ == "__main__":
    main()

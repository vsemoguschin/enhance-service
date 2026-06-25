from PIL import Image
import os

base = "/Users/mac/code/EASY-CRM/1/crm/tasks/image-enhance-book-editor-2026-06-23"
inp, out = base + "/test-input", base + "/test-output"
cmp = base + "/test-compare"
os.makedirs(cmp, exist_ok=True)

# fractional face/upper-body boxes (l, t, r, b) — generous to include the face
jobs = {
    "image":  (0.26, 0.16, 0.78, 0.64),
    "image1": (0.28, 0.06, 0.82, 0.46),
    "image2": (0.30, 0.12, 0.70, 0.62),
}

for name, (l, t, r, b) in jobs.items():
    orig = Image.open(f"{inp}/{name}.png").convert("RGB")
    ai = Image.open(f"{out}/{name}_x4.png").convert("RGB")
    W, H = ai.size
    bic = orig.resize((W, H), Image.BICUBIC)  # naive upscale to same size
    box = (int(l * W), int(t * H), int(r * W), int(b * H))
    ai_c, bic_c = ai.crop(box), bic.crop(box)

    vw = 560
    def fit(im):
        w, h = im.size
        return im.resize((vw, int(h * vw / w)), Image.LANCZOS)
    ai_c, bic_c = fit(ai_c), fit(bic_c)

    gap = 16
    canvas = Image.new("RGB", (bic_c.width + ai_c.width + gap, max(bic_c.height, ai_c.height)), (255, 255, 255))
    canvas.paste(bic_c, (0, 0))                       # LEFT = bicubic (naive)
    canvas.paste(ai_c, (bic_c.width + gap, 0))        # RIGHT = AI (Real-ESRGAN)
    canvas.save(f"{cmp}/{name}_compare.png")
    print(f"{name}: AI {W}x{H}, box {box} -> {name}_compare.png")

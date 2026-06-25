from PIL import Image, ImageDraw

base = "/Users/mac/code/EASY-CRM/1/crm/tasks/image-enhance-book-editor-2026-06-23"
out, cmp = base + "/test-output", base + "/test-compare"

boxes = {
    "image":  (0.26, 0.16, 0.78, 0.64),
    "image1": (0.28, 0.06, 0.82, 0.46),
    "image2": (0.30, 0.12, 0.70, 0.62),
}

for name, (l, t, r, b) in boxes.items():
    esr = Image.open(f"{out}/{name}_x2.png").convert("RGB")       # Real-ESRGAN only (no face restore)
    gfp = Image.open(f"{out}/{name}_gfpgan.png").convert("RGB")   # GFPGAN face-restored
    def crop(im):
        w, h = im.size
        return im.crop((int(l * w), int(t * h), int(r * w), int(b * h)))
    vw = 420
    def fit(im):
        w, h = im.size
        return im.resize((vw, int(h * vw / w)), Image.LANCZOS)
    a, c = fit(crop(esr)), fit(crop(gfp))
    gap, lab = 16, 26
    canvas = Image.new("RGB", (vw * 2 + gap, max(a.height, c.height) + lab), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    canvas.paste(a, (0, lab)); d.text((6, 6), "Real-ESRGAN (no face)", fill=(0, 0, 0))
    canvas.paste(c, (vw + gap, lab)); d.text((vw + gap + 6, 6), "GFPGAN (face restored)", fill=(0, 0, 0))
    canvas.save(f"{cmp}/{name}_faces.png")
    print(f"{name}_faces.png saved")

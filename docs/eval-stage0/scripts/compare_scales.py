from PIL import Image, ImageDraw

base = "/Users/mac/code/EASY-CRM/1/crm/tasks/image-enhance-book-editor-2026-06-23"
inp, out, cmp = base + "/test-input", base + "/test-output", base + "/test-compare"

name = "image"
l, t, r, b = 0.26, 0.16, 0.78, 0.64  # face box (fractional)

orig = Image.open(f"{inp}/{name}.png").convert("RGB")
x2 = Image.open(f"{out}/{name}_x2.png").convert("RGB")
x4 = Image.open(f"{out}/{name}_x4.png").convert("RGB")
W4, H4 = x4.size
bic = orig.resize((W4, H4), Image.BICUBIC)

def crop_frac(im):
    w, h = im.size
    return im.crop((int(l * w), int(t * h), int(r * w), int(b * h)))

vw = 380
def fit(im):
    w, h = im.size
    return im.resize((vw, int(h * vw / w)), Image.LANCZOS)

panels = [("bicubic", fit(crop_frac(bic))), ("AI x2", fit(crop_frac(x2))), ("AI x4", fit(crop_frac(x4)))]
gap, lab = 16, 26
ph = max(p.height for _, p in panels)
canvas = Image.new("RGB", (vw * 3 + gap * 2, ph + lab), (255, 255, 255))
d = ImageDraw.Draw(canvas)
x = 0
for title, im in panels:
    canvas.paste(im, (x, lab))
    d.text((x + 6, 6), title, fill=(0, 0, 0))
    x += vw + gap
canvas.save(f"{cmp}/scales_compare.png")
print("saved scales_compare.png  ", [(n, p.size) for n, p in panels])

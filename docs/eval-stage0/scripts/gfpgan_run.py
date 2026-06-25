import sys, types, os, time, ssl
ssl._create_default_https_context = ssl._create_unverified_context  # python.org SSL workaround

# --- Max's shim for torchvision>=0.17 (functional_tensor removed) ---
try:
    import torchvision.transforms.functional_tensor  # noqa
except Exception:
    import torchvision.transforms.functional as _F
    m = types.ModuleType("torchvision.transforms.functional_tensor")
    m.rgb_to_grayscale = _F.rgb_to_grayscale
    sys.modules["torchvision.transforms.functional_tensor"] = m

import cv2
from gfpgan import GFPGANer

base = "/Users/mac/code/EASY-CRM/1/crm/tasks/image-enhance-book-editor-2026-06-23"
inp, out = base + "/test-input", base + "/test-output"
model = base + "/tools/models-gfpgan/GFPGANv1.4.pth"

# upscale=2 to match our AI x2; bg_upsampler=None (faces only, like Max). weight=0.85 (Max fidelity)
restorer = GFPGANer(model_path=model, upscale=2, arch="clean", channel_multiplier=2, bg_upsampler=None)

for name in ("image", "image1", "image2"):
    img = cv2.imread(f"{inp}/{name}.png")
    t = time.time()
    _, _, restored = restorer.enhance(img, has_aligned=False, only_center_face=False, paste_back=True, weight=0.85)
    cv2.imwrite(f"{out}/{name}_gfpgan.png", restored)
    print(f"{name}_gfpgan.png {restored.shape[1]}x{restored.shape[0]} {time.time()-t:.1f}s")
print("GFPGAN done")

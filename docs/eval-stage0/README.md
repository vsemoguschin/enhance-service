# Этап 0 — проверка качества (2026-06-23)

Разовая проверка качества/скорости Real-ESRGAN на реальных low-DPI фото перед сборкой сервиса.
Вердикт: **GO** (детали — [../задача.md](../задача.md), раздел «Этап 0»).

## Содержимое

- `test-input/` — исходные фото (3 уникальных портрета, 480–640px; image2–6 = дубликаты).
- `test-output/` — результаты: `*_x2.png`, `*_x4.png` (Real-ESRGAN), `*_gfpgan.png` (с лицами).
- `test-compare/` — наглядные сравнения:
  - `*_compare.png` — bicubic vs Real-ESRGAN (лицо, нативное разрешение);
  - `scales_compare.png` — bicubic | x2 | x4;
  - `*_faces.png` — Real-ESRGAN vs GFPGAN (восстановление лиц).
- `scripts/` — скрипты сравнения (PIL): `compare.py`, `compare_scales.py`, `compare_faces.py`,
  `make_x2.py`, `gfpgan_run.py`.

## Итоги

- Real-ESRGAN — заметно лучше bicubic, лица достоверны (fidelity-модель).
- x2 ≈ x4 по качеству-на-пиксель → масштаб target-driven (под размер ячейки).
- GFPGAN — резче лица, но сглаживает кожу («пластик») → opt-in, не по умолчанию.
- Скорость ~16–19с/фото (x4) и +6–7с (лица) на CPU без GPU (Intel Mac — прокси для codex).

## Как воспроизвести (тяжёлые модели/бинарь НЕ в git — перекачиваемые)

```bash
# 1. Движок Real-ESRGAN (ncnn, ~50МБ zip → бинарь + модели):
curl -L -o realesrgan.zip \
  https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-macos.zip
unzip realesrgan.zip
xattr -cr realesrgan-ncnn-vulkan            # снять карантин macOS
# апскейл x4 (модель для фото):
./realesrgan-ncnn-vulkan -i test-input/image.png -o test-output/image_x4.png -n realesrgan-x4plus

# 2. (опц.) Восстановление лиц GFPGAN — Python-стек (враждебная установка, см. задача.md):
#    numpy==1.26.4 scipy==1.11.4 scikit-image==0.22 torch torchvision gfpgan realesrgan basicsr facexlib
#    + shim torchvision.functional_tensor + SSL-обход + curl моделей:
#    GFPGANv1.4.pth, detection_Resnet50_Final.pth, parsing_parsenet.pth
#    (поэтому в прод берём ncnn, а не torch).
```

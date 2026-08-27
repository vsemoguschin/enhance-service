"""Промпты для генеративного восстановления (Magnific / Seedream / Codex).

Единственный источник: сюда же ходит scripts/magnific_smoke.py, чтобы эксперименты и прод
не разъезжались. Формулировки подобраны на реальных фото — детали в
docs/audits/2026-08-10-magnific-api-test.md.

Главный вывод тех прогонов: модели по умолчанию затирают фотографическую фактуру бьюти-ретушью,
и просьбы «сохрани детали» не работают — работают прямые запреты.
"""

# Роли multi-reference проверены 27.08 на трёх типах кадра. Важно не предлагать
# вспомогательные варианты как равноправные композиционные референсы: Retinex проявляет
# тени, но меняет свет, а сильный финиш помогает с фактурой, но может дать артефакты.
CODEX_MULTI_REFERENCE_PROMPT = (
    "Create one polished, high-resolution photographic enhancement. "
    "Image 1 is the sole authoritative source for every person, identity, facial structure, "
    "pose, crop, composition, geometry, object, lighting mood, and color balance. "
    "Image 2 is only a supporting reference for recoverable shadow detail; do not copy its "
    "overall exposure, softness, compression, color cast, geometry, or edge artifacts. "
    "Image 3 is only a supporting reference for authentic edge and texture cues; do not copy "
    "halos, noise, oversharpening, altered contours, geometry, duplicated objects, or edge "
    "artifacts. Preserve every person and object exactly as photographed, including identities, "
    "expressions, hair, hands, clothing, accessories, positions, scale, overlaps, background, "
    "perspective, and complete original framing. Improve authentic fine detail in faces, hair, "
    "glasses, fabric, vegetation, architecture, and other already visible surfaces. Keep skin "
    "natural and photographic, with no beauty retouching, waxy skin, invented features, or "
    "changed identity. Retain highlights without clipping and open only useful shadow detail. "
    "Do not add, remove, replace, crop, duplicate, or reposition anything. Output one clean, "
    "natural photograph with high micro-detail and no text or watermark."
)

PROMPTS = {
    # Лабораторный вариант для проверки 27.08. Не ставить дефолтом до прогона на выборке.
    # В отличие от preserve здесь явно разделены разрешённые оптические изменения и
    # неизменяемое содержимое кадра. Размер выхода не фиксируем: качество важнее масштаба.
    "preserve-detail-lab": (
        "Use case: identity-preserve\n"
        "Asset type: conservative enhancement of the same personal photograph for print\n"
        "Input image: edit target, not a loose reference or inspiration\n"
        "Primary request: improve the optical clarity of this exact photograph. Make only "
        "details that are already visibly recorded in the input easier to read. Correct mild "
        "optical softness and compression artifacts, reduce distracting digital noise, and "
        "strengthen existing edges and texture conservatively. Do not synthesize missing detail.\n"
        "Allowed changes only: optical clarity, focus impression, compression cleanup, restrained "
        "noise cleanup, and local definition of already visible texture.\n"
        "Identity lock: preserve every person's exact facial geometry, eyes, pupils, gaze, mouth, "
        "expression, age, skin marks, hairline, hairstyle, body, pose, hands, clothing and accessories.\n"
        "Scene lock: preserve every person and object, their position, scale and overlap. Preserve "
        "the exact composition, perspective, crop and aspect ratio. Do not re-frame or recompose.\n"
        "Color lock: preserve the original brightness, contrast, saturation, white balance and light "
        "temperature. Do not beautify skin and do not apply cinematic or dramatic color grading.\n"
        "Output intent: the same photograph with cleaner, more legible existing detail. Output pixel "
        "dimensions may differ, but content, aspect ratio and framing must remain unchanged.\n"
        "Avoid: repainting or reconstructing faces, invented pores or hair, altered pupils, plastic "
        "skin, halos, oversharpening, added or removed people or objects, changed background, crop, "
        "text, logo or watermark."
    ),
    # Прод с 2026-08-21. Отличие от identity: не просим «восстановить/подготовить к печати»
    # и не просим увеличить — обе формулировки провоцировали пересборку кадра, потому что
    # модель понимает их как «нарисуй заново, но лучше». Здесь задача сформулирована как
    # техническая чистка того же кадра, а детализацию добавляет уже локальный finish.py.
    # Замер 21.08 на групповом кадре 1024x768 (10 лиц по ~80 px): composition, состав и
    # черты лиц сохранены, размер не изменился — впервые за все прогоны.
    "preserve": (
        "Приведи это фото в порядок, НЕ меняя его содержимое и не увеличивая его.\n"
        "Сохрани оригинальные цвета, фактуру, зернистость и микроконтраст.\n"
        "Сохрани все существующие детали и сделай их читаемее: аккуратно подчеркни границы "
        "и мелкую фактуру, которые уже есть в кадре. Не дорисовывай того, чего нет.\n"
        "Уменьши цифровой шум, но не делай кожу или поверхность «пластиковой».\n"
        "ЦВЕТ ОСТАВЬ КАК В ОРИГИНАЛЕ: та же яркость, тот же контраст, та же насыщенность, "
        "тот же баланс белого и та же температура света. Тёплый свет комнатной лампы — "
        "часть кадра, а не дефект: не выправляй его в нейтральный и не делай картинку "
        "ярче, сочнее или контрастнее. Не пересвечивай света и не заваливай тени.\n\n"
        "ЗАПРЕЩЕНО МЕНЯТЬ:\n"
        "- лица, черты, мимику, направление взгляда и положение зрачков, возраст;\n"
        "- количество людей и предметов, их позы и расположение в кадре;\n"
        "- композицию, кадрирование, план, масштаб и перспективу;\n"
        "- одежду, причёски, аксессуары, фон и его детали.\n\n"
        "Верни ТОТ ЖЕ кадр, только чище и чётче. Это не реставрация и не творческая "
        "обработка — только техническая чистка."
    ),
    # Структура подсмотрена у Codex CLI: его imagegen оборачивает задачу в поля
    # (use case, subject, composition, avoid) — и именно он единственный не разрушил
    # групповой кадр. Ключевое отличие от сплошного текста: явный identity-preserve,
    # раздельные поля вместо абзаца и длинный перечень запретов.
    "identity": (
        "Use case: identity-preserve\n"
        "Asset type: enhanced personal photograph for print\n"
        "Primary request: restore this photo for printing while keeping photographic texture. "
        "Preserve and emphasise micro-texture: skin pores and relief, individual hairs and strands, "
        "fabric weave and knit, foliage and background detail. Do NOT smooth skin, do NOT apply "
        "beauty retouching, blur or denoising, do NOT create plastic or waxy surfaces, do not "
        "remove wrinkles or moles. Keep natural grain and micro-contrast, sharpness like a good lens.\n"
        "Input images: Image 1: edit target; preserve every person and the original photograph\n"
        "Scene/backdrop: original scene, background, architecture and objects, unchanged\n"
        "Subject: same people, same identity, facial features, expressions, age, pose, "
        "clothing and accessories\n"
        "Style/medium: realistic photographic restoration and enhancement\n"
        "Composition/framing: preserve the exact original composition, perspective, scale and crop; "
        "do not add or remove people or objects; do not zoom or re-frame\n"
        "Lighting/mood: preserve the original light of the scene\n"
        "Color palette: preserve natural original colors; no dramatic grading\n"
        "Constraints: change only image clarity, detail and print readiness; keep people and scene "
        "content unchanged\n"
        "Avoid: beauty retouching, plastic skin, face repainting, altered facial proportions or "
        "expression, changed hairstyle, changed clothing, added hats or accessories, invented "
        "details, new people, removed people, new objects, crop changes, re-framing, text, "
        "watermark, blur, denoising artifacts"
    ),
    # Дефолт прода: принят владельцем и на низкорес-селфи, и на скане старого отпечатка.
    "texture": (
        "Восстанови это фото для печати, сохранив фотографическую фактуру. "
        "Сохрани и подчеркни микротекстуру: поры и рельеф кожи, отдельные волоски и пряди, "
        "ворс и плетение ткани, вязку головного убора, листву и траву. "
        "НЕ сглаживай кожу, НЕ применяй бьюти-ретушь, размытие и шумоподавление, "
        "НЕ создавай пластиковых и восковых поверхностей, не убирай морщины и родинки. "
        "Сохрани естественное зерно и микроконтраст, резкость как у съёмки на хороший объектив. "
        "Черты лиц, мимику, очки, причёску и одежду оставь без изменений. "
        # На групповом снимке модель приблизила крайних людей и дорисовала два новых лица —
        # запрет на изменение состава и композиции добавлен после этого случая.
        "КОЛИЧЕСТВО ЛЮДЕЙ И ОБЪЕКТОВ В КАДРЕ ДОЛЖНО ОСТАТЬСЯ ТЕМ ЖЕ: не добавляй новых людей, "
        "лиц, предметов и деталей фона, не убирай существующих. "
        "Не меняй композицию, кадрирование, план и масштаб объектов: положение и размер каждого "
        "человека относительно кадра остаются прежними."
    ),
    # Для архивных серий: реставрирует, но не осовременивает — держит колорит эпохи.
    "old-photo": (
        "Restore and enhance an old damaged photo. Remove scratches, stains, dust and noise. "
        "Reconstruct faded or torn areas while preserving original details. "
        "Slightly sharpen the image for better clarity, but keep it realistic. "
        "Apply natural and era-appropriate colors to skin, hair and clothing; keep background "
        "colors natural and muted. "
        "PRESERVE EXACTLY: facial features, expressions, mouth and eye position, age and identity "
        "of every person, glasses, jewellery, clothing patterns, pose, composition and crop. "
        "Do NOT replace, simplify or repaint the background. "
        "Do NOT retouch or smooth skin, do not remove wrinkles, moles or scars, "
        "do not apply beauty filters or plastic-looking surfaces. "
        "The final result should look like an old photo that has been realistically restored "
        "and colorized, while respecting its original appearance."
    ),
    # Осторожный вариант: минимум вмешательства, максимум удержания оригинала.
    "restore": (
        "Восстанови это фото для печати в фотокниге. Убери размытие, шум и артефакты сжатия. "
        "Верни естественную детализацию кожи, волос, ткани и фона. "
        "СОХРАНИ без изменений черты лиц, пропорции, выражения, возраст и все детали одежды "
        "и аксессуаров. Сохрани композицию кадра, естественный свет и цвета сцены. "
        "Резкость естественная, без пластиковой кожи, без перерисовки лиц, без добавления "
        "новых объектов и без косметической ретуши."
    ),
    # Максимум микродеталей. Запреты на мимику стоят жёстко: агрессивная детализация
    # склонна «оживлять» лицо — в веб-эталоне модель дорисовала человеку улыбку с зубами.
    "detail": (
        "Восстанови это фото и усиль детализацию до уровня студийной съёмки. "
        "Проработай микродетали: текстуру и поры кожи, отдельные волоски и пряди, ресницы, "
        "плетение ткани рубашки, вязку головного убора, листву, траву и архитектуру на фоне. "
        "Высокая резкость, выразительный микроконтраст, глубокие естественные цвета. "
        "ОБЯЗАТЕЛЬНО сохрани узнаваемость: черты лиц, форму носа, глаз и губ, возраст, "
        "выражение и мимику ровно как в оригинале. "
        "НЕ меняй улыбку и положение губ, не открывай рот, не добавляй зубы, не наноси косметику, "
        "не меняй причёску, одежду, позу и композицию кадра."
    ),
    # Промпт из веб-версии Magnific — держим для сравнений, в прод не ставить.
    "web": (
        "подготовь это фото к профессиональной печати, улучши качество максимально без потери "
        "сходства, сделай цветокоррекцию, чтобы фото не было темным, разрешение 4к, сделай так, "
        "чтобы фото было четким и не смазанным. сделай фото четким"
    ),
}

DEFAULT_PRESET = "texture"

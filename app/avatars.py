from __future__ import annotations

import io
import os
import re
import uuid
import warnings
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import Settings

ALLOWED_FORMATS = {"JPEG": {"jpg", "jpeg"}, "PNG": {"png"}, "WEBP": {"webp"}}
ALLOWED_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
AVATAR_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def process_avatar(upload: UploadFile, settings: Settings) -> str:
    extension = Path(upload.filename or "").suffix.lower().lstrip(".")
    data = upload.file.read(settings.avatar_max_bytes + 1)
    if len(data) > settings.avatar_max_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Файл больше 2 MB.")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Файл пуст.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(data))
            image_format = (probe.format or "").upper()
            width, height = probe.size
            if (
                image_format not in ALLOWED_FORMATS
                or extension not in ALLOWED_FORMATS[image_format]
                or upload.content_type != ALLOWED_MIME[image_format]
            ):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "Разрешены только настоящие JPEG, PNG и WebP.",
                )
            if width <= 0 or height <= 0 or width * height > settings.avatar_max_pixels:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "Изображение имеет слишком большие размеры.",
                )
            probe.verify()
            source = Image.open(io.BytesIO(data))
            source.load()
    except HTTPException:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Файл повреждён или не является поддерживаемым изображением.",
        ) from exc

    source = ImageOps.exif_transpose(source)
    has_alpha = source.mode in {"RGBA", "LA"} or (
        source.mode == "P" and "transparency" in source.info
    )
    mode = "RGBA" if has_alpha else "RGB"
    source = source.convert(mode)
    fitted = ImageOps.fit(
        source,
        (settings.avatar_size, settings.avatar_size),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    clean = Image.new(mode, fitted.size)
    clean.paste(fitted)

    settings.avatar_storage_dir.mkdir(parents=True, exist_ok=True)
    avatar_id = uuid.uuid4().hex
    final_path = avatar_path(settings, avatar_id)
    temporary = settings.avatar_storage_dir / f".{avatar_id}.tmp"
    try:
        clean.save(temporary, format="WEBP", quality=86, method=6, exif=b"")
        os.replace(temporary, final_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        clean.close()
        fitted.close()
        source.close()
    return avatar_id


def avatar_path(settings: Settings, avatar_id: str) -> Path:
    if not AVATAR_ID_PATTERN.fullmatch(avatar_id):
        raise ValueError("Invalid avatar id")
    return settings.avatar_storage_dir / f"{avatar_id}.webp"


def delete_avatar_file(settings: Settings, avatar_id: str | None) -> None:
    if avatar_id and AVATAR_ID_PATTERN.fullmatch(avatar_id):
        avatar_path(settings, avatar_id).unlink(missing_ok=True)

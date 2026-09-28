"""Provider-neutral OCR with native PDF extraction and Tesseract fallback."""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from time import monotonic
from typing import Protocol
from flask import current_app
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


class OCRProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRResult:
    text: str
    confidence: float | None
    provider: str
    provider_version: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


class OCRProvider(Protocol):
    def extract_text(self, content: bytes, mime_type: str) -> OCRResult: ...


class DisabledOCRProvider:
    def extract_text(self, content: bytes, mime_type: str) -> OCRResult:
        raise OCRProcessingError("OCR processing is disabled by configuration.")


class TesseractOCRProvider:
    name = "tesseract"

    def _engine(self):
        try:
            import pytesseract
            version = str(pytesseract.get_tesseract_version()).splitlines()[0]
            return pytesseract, version
        except Exception as exc:
            raise OCRProcessingError("The configured OCR engine is unavailable.") from exc

    def _prepare(self, image: Image.Image) -> Image.Image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in {"RGB", "L"}:
            background = Image.new("RGB", image.size, "white")
            if "A" in image.getbands():
                background.paste(image, mask=image.getchannel("A"))
            else:
                background.paste(image)
            image = background
        if not current_app.config["OCR_PREPROCESS_IMAGES"]:
            return image
        if max(image.size) < 1600:
            scale = min(3, max(1, 1600 // max(image.size)))
            image = image.resize((image.width * scale, image.height * scale))
        gray = ImageOps.grayscale(image)
        gray = ImageEnhance.Contrast(gray).enhance(1.35)
        return gray.filter(ImageFilter.MedianFilter(3)) if min(gray.size) >= 100 else gray

    def _ocr_image(self, image: Image.Image, timeout=None):
        pytesseract, version = self._engine()
        try:
            data = pytesseract.image_to_data(self._prepare(image), output_type=pytesseract.Output.DICT,
                timeout=timeout or current_app.config["OCR_TIMEOUT_SECONDS"])
        except RuntimeError as exc:
            if "timeout" in str(exc).lower():
                raise OCRProcessingError("OCR processing timed out.") from exc
            raise OCRProcessingError("OCR could not process this image.") from exc
        lines, scores = {}, []
        count = len(data.get("text", []))
        for index in range(count):
            text, confidence = data["text"][index], data.get("conf", [])[index]
            if text and text.strip():
                key = tuple(data.get(name, [0] * count)[index] for name in ("block_num", "par_num", "line_num"))
                lines.setdefault(key, []).append(text.strip())
                try:
                    value = float(confidence)
                    if value >= 0:
                        scores.append(value / 100)
                except (TypeError, ValueError):
                    pass
        return "\n".join(" ".join(words) for words in lines.values()), \
            (sum(scores) / len(scores) if scores else None), version

    def _pdf(self, content: bytes) -> OCRResult:
        try:
            import fitz
            pdf = fitz.open(stream=content, filetype="pdf")
        except Exception as exc:
            raise OCRProcessingError("PDF text extraction failed.") from exc
        texts, scores, warnings = [], [], []
        tesseract_version = None
        deadline = monotonic() + current_app.config["OCR_TIMEOUT_SECONDS"]
        try:
            for page in pdf:
                native = page.get_text("text").strip()
                if len("".join(native.split())) >= 20:
                    texts.append(native)
                    scores.append(0.99)
                    continue
                try:
                    remaining = int(deadline - monotonic())
                    if remaining < 1:
                        warnings.append("OCR document timeout reached before all scanned pages were processed.")
                        break
                    matrix = fitz.Matrix(200 / 72, 200 / 72)
                    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                    image = Image.open(BytesIO(pixmap.tobytes("png")))
                    text, confidence, tesseract_version = self._ocr_image(image, remaining)
                    texts.append(text)
                    if confidence is not None:
                        scores.append(confidence)
                except OCRProcessingError:
                    warnings.append(f"Page {page.number + 1} could not be OCR processed.")
        finally:
            pdf.close()
        return OCRResult("\n\n".join(filter(None, texts)),
            sum(scores) / len(scores) if scores else None, "pymupdf+tesseract",
            tesseract_version or getattr(__import__("fitz"), "VersionBind", None), tuple(warnings))

    def extract_text(self, content: bytes, mime_type: str) -> OCRResult:
        if mime_type == "application/pdf":
            return self._pdf(content)
        try:
            with Image.open(BytesIO(content)) as image:
                image.load()
                text, confidence, version = self._ocr_image(image)
        except OCRProcessingError:
            raise
        except Exception as exc:
            raise OCRProcessingError("OCR could not decode this image.") from exc
        return OCRResult(text, confidence, self.name, version)


def get_ocr_provider() -> OCRProvider:
    if current_app.config["OCR_PROVIDER"] == "disabled":
        return DisabledOCRProvider()
    return TesseractOCRProvider()

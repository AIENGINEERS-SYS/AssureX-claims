"""Private document validation and storage with local/S3 adapters."""
import hashlib
from io import BytesIO
from pathlib import Path
import warnings
from uuid import uuid4
from flask import current_app
from PIL import Image
from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject
from werkzeug.exceptions import NotFound
from werkzeug.utils import secure_filename
from .document_errors import document_error

FILE_LIMIT = 10 * 1024 * 1024
TOTAL_LIMIT = 50 * 1024 * 1024
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".pdf": "application/pdf"}
SIGNATURES = {".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
              ".png": (b"\x89PNG\r\n\x1a\n",), ".pdf": (b"%PDF-",)}


def file_limit():
    return current_app.config["MAX_DOCUMENT_SIZE_MB"] * 1024 * 1024


def total_limit():
    return current_app.config["MAX_CLAIM_UPLOAD_SIZE_MB"] * 1024 * 1024


def validate_file(upload):
    if upload is None or not upload.filename:
        raise document_error("unsupported_document", "Supply one document in multipart/form-data.")
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in MIME:
        raise document_error("unsupported_file_type", "Only PDF, JPG, JPEG and PNG documents are supported.")
    if upload.mimetype != MIME[suffix]:
        raise document_error("mime_mismatch", "The file extension and declared content type do not match.")
    limit = file_limit()
    content = upload.read(limit + 1)
    if len(content) > limit:
        raise document_error("file_too_large",
            f"Each document must be at most {current_app.config['MAX_DOCUMENT_SIZE_MB']} MB.", 413)
    if not content:
        raise document_error("corrupt_file", "The uploaded document is empty.")
    if not any(content.startswith(signature) for signature in SIGNATURES[suffix]):
        raise document_error("mime_mismatch", "The file contents do not match the selected document type.")
    try:
        if suffix == ".pdf":
            reader = PdfReader(BytesIO(content), strict=True)
            if reader.is_encrypted:
                raise document_error("encrypted_pdf", "Password-protected or encrypted PDFs are not supported.")
            if not 1 <= len(reader.pages) <= current_app.config["OCR_MAX_PDF_PAGES"]:
                raise document_error("unsupported_document",
                    f"PDFs must contain between 1 and {current_app.config['OCR_MAX_PDF_PAGES']} pages.")
            # Reject active content and embedded payloads, including indirect objects.
            stack, seen, count = [reader.trailer], set(), 0
            forbidden = {"/JS", "/JavaScript", "/Launch", "/EmbeddedFiles", "/EF", "/OpenAction",
                         "/AA", "/RichMedia", "/XFA", "/SubmitForm", "/ImportData"}
            while stack:
                value = stack.pop()
                count += 1
                if count > 10000:
                    raise ValueError("PDF too complex")
                if isinstance(value, IndirectObject):
                    identity = (value.idnum, value.generation)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    stack.append(value.get_object())
                elif isinstance(value, DictionaryObject):
                    if forbidden.intersection(value) or str(value.get("/S", "")) in forbidden:
                        raise document_error("unsupported_document", "PDF scripts, actions and attachments are not supported.")
                    stack.extend(value.values())
                elif isinstance(value, ArrayObject):
                    stack.extend(value)
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as picture:
                    if picture.format != ("PNG" if suffix == ".png" else "JPEG"):
                        raise ValueError("Image type mismatch")
                    if picture.width <= 0 or picture.height <= 0:
                        raise ValueError("Empty image")
                    if picture.width * picture.height > 25_000_000:
                        raise ValueError("Image too large")
                    picture.verify()
                with Image.open(BytesIO(content)) as picture:
                    picture.load()
    except Exception as exc:
        if getattr(exc, "error_code", None):
            raise
        raise document_error("corrupt_file", "The document is corrupt or cannot be decoded safely.") from None
    name = secure_filename(upload.filename)[:255] or ("document" + suffix)
    return content, name, MIME[suffix], suffix, hashlib.sha256(content).hexdigest()


class LocalStorage:
    def __init__(self):
        self.root = Path(current_app.config["DOCUMENT_STORAGE_PATH"]).resolve()

    def path(self, key):
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise NotFound("Document not found.")
        return path

    def put(self, key, content, mime):
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as stream:
                stream.write(content)
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def save(self, key, content, mime):
        return self.put(key, content, mime)

    def read(self, key):
        try:
            return self.path(key).read_bytes()
        except FileNotFoundError:
            raise NotFound("Document not found.") from None

    def open(self, key):
        return self.read(key)

    def exists(self, key):
        return self.path(key).is_file()

    def delete(self, key):
        self.path(key).unlink(missing_ok=True)


class S3Storage:
    def __init__(self):
        import boto3
        self.client = boto3.client("s3", endpoint_url=current_app.config.get("S3_ENDPOINT_URL"))
        self.bucket = current_app.config["S3_BUCKET"]

    def put(self, key, content, mime):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content, ContentType=mime,
                               ServerSideEncryption="AES256")

    def save(self, key, content, mime):
        return self.put(key, content, mime)

    def read(self, key):
        from botocore.exceptions import ClientError
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            with response["Body"] as stream:
                return stream.read()
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"NoSuchKey", "404"}:
                raise NotFound("Document not found.") from None
            raise

    def open(self, key):
        return self.read(key)

    def exists(self, key):
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"NoSuchKey", "404"}:
                return False
            raise

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)


def storage():
    return S3Storage() if current_app.config["DOCUMENT_STORAGE_BACKEND"] == "s3" else LocalStorage()


def storage_key(claim_id, suffix):
    return f"documents/{claim_id}/{uuid4().hex}{suffix}"

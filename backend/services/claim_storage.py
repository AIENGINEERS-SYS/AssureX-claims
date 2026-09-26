"""Private object storage with interchangeable local and S3 adapters."""
import hashlib
from io import BytesIO
from pathlib import Path
import warnings
from uuid import uuid4
from flask import current_app
from PIL import Image
from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject
from werkzeug.exceptions import BadRequest, NotFound, RequestEntityTooLarge
from werkzeug.utils import secure_filename

FILE_LIMIT = 10 * 1024 * 1024
TOTAL_LIMIT = 50 * 1024 * 1024
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".pdf": "application/pdf"}


def validate_file(upload):
    if upload is None or not upload.filename:
        raise BadRequest("Supply a file in multipart/form-data.")
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in MIME or upload.mimetype != MIME[suffix]:
        raise BadRequest("File extension and MIME type must match JPEG, PNG or PDF.")
    content = upload.read(FILE_LIMIT + 1)
    if len(content) > FILE_LIMIT:
        raise RequestEntityTooLarge("Each file must be at most 10 MB.")
    try:
        if suffix == ".pdf":
            if not content.startswith(b"%PDF-"):
                raise ValueError("Invalid PDF header")
            reader = PdfReader(BytesIO(content), strict=True)
            if reader.is_encrypted or not 1 <= len(reader.pages) <= 100:
                raise ValueError("Encrypted or oversized PDF")
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
                        raise ValueError("Active PDF content")
                    stack.extend(value.values())
                elif isinstance(value, ArrayObject):
                    stack.extend(value)
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as picture:
                    if picture.format != ("PNG" if suffix == ".png" else "JPEG"):
                        raise ValueError("Image type mismatch")
                    if picture.width * picture.height > 25_000_000:
                        raise ValueError("Image too large")
                    picture.verify()
                with Image.open(BytesIO(content)) as picture:
                    picture.load()
    except Exception:
        raise BadRequest("Upload a valid JPEG, PNG or non-encrypted PDF (up to 100 pages), without active content or attachments.") from None
    name = secure_filename(upload.filename)[:255] or ("document" + suffix)
    return content, name, MIME[suffix], suffix, hashlib.sha256(content).hexdigest()


class LocalStorage:
    def __init__(self):
        self.root = Path(current_app.config["UPLOAD_FOLDER"]).resolve()

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

    def read(self, key):
        try:
            return self.path(key).read_bytes()
        except FileNotFoundError:
            raise NotFound("Document not found.") from None

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

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)


def storage():
    return S3Storage() if current_app.config["CLAIM_STORAGE"] == "s3" else LocalStorage()


def storage_key(claim_id, suffix):
    return f"claims/{claim_id}/{uuid4().hex}{suffix}"

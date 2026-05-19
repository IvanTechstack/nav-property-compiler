"""NAV-Property-Compiler — Streamlit app for managing property media via Cloudflare R2."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass

import boto3
import streamlit as st
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BUCKET_NAME = "nav-property-media"
STANDARD_MAX_HEIGHT = 800        # px — standard upload height cap
BANNER_WIDTH = 1920              # px — featured banner exact width
BANNER_HEIGHT = 810              # px — featured banner exact height
DEFAULT_QUALITY = 82             # WebP quality (1-100)
THUMBNAIL_EXPIRY = 300           # seconds — presigned URL lifetime for thumbnails
DOWNLOAD_EXPIRY = 3600           # seconds — presigned URL lifetime for downloads
SUPPORTED_UPLOAD_TYPES = ["jpg", "jpeg", "png", "webp", "tiff", "bmp"]
SUPPORTED_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "tiff": "image/tiff",
    "bmp": "image/bmp",
}
BANNER_FOCUS_OPTIONS = [
    "Center",
    "Top (Keep Roof/Sky)",
    "Bottom (Keep Lawn/Ground)",
]


# ---------------------------------------------------------------------------
# R2 client
# ---------------------------------------------------------------------------

@dataclass
class R2Config:
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket: str = BUCKET_NAME


def _load_r2_config() -> R2Config:
    """Read R2 credentials from environment variables."""
    endpoint = os.environ.get("R2_ENDPOINT_URL", "").strip()
    key_id = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()

    missing = [k for k, v in {
        "R2_ENDPOINT_URL": endpoint,
        "R2_ACCESS_KEY_ID": key_id,
        "R2_SECRET_ACCESS_KEY": secret,
    }.items() if not v]

    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    return R2Config(endpoint_url=endpoint, access_key_id=key_id, secret_access_key=secret)


@st.cache_resource(show_spinner=False)
def get_r2_client():
    """Create and cache a boto3 S3 client pointed at Cloudflare R2."""
    cfg = _load_r2_config()
    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint_url,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


# ---------------------------------------------------------------------------
# Image processing helpers
# ---------------------------------------------------------------------------

def _to_web_mode(img: Image.Image, output_format: str) -> Image.Image:
    """Ensure correct colour mode for the target format."""
    if output_format == "WEBP":
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
    else:
        if img.mode != "RGB":
            img = img.convert("RGB")
    return img


def process_standard(
    raw_bytes: bytes,
    *,
    quality: int = DEFAULT_QUALITY,
    strip_exif: bool = True,
    output_format: str = "WEBP",
) -> tuple[bytes, str]:
    """
    Standard upload: cap height at STANDARD_MAX_HEIGHT px (proportional scale).
    Returns (encoded_bytes, file_extension).
    """
    img = Image.open(io.BytesIO(raw_bytes))
    img = ImageOps.exif_transpose(img)
    img = _to_web_mode(img, output_format)

    w, h = img.size
    if h > STANDARD_MAX_HEIGHT:
        ratio = STANDARD_MAX_HEIGHT / h
        img = img.resize((int(w * ratio), STANDARD_MAX_HEIGHT), Image.LANCZOS)

    buf = io.BytesIO()
    save_kwargs: dict = {"quality": quality, "optimize": True}
    if output_format == "WEBP":
        save_kwargs["method"] = 6
    img.save(buf, format=output_format, **save_kwargs)
    buf.seek(0)

    ext = "webp" if output_format == "WEBP" else output_format.lower()
    return buf.read(), ext


def process_banner(
    raw_bytes: bytes,
    *,
    vertical_focus: str = "Center",
    quality: int = DEFAULT_QUALITY,
    strip_exif: bool = True,
    output_format: str = "WEBP",
) -> tuple[bytes, str]:
    """
    Featured banner: scale to fill 1920×810, then crop excess height based on
    vertical_focus ('Center', 'Top (Keep Roof/Sky)', 'Bottom (Keep Lawn/Ground)').
    Returns (encoded_bytes, file_extension).
    """
    img = Image.open(io.BytesIO(raw_bytes))
    img = ImageOps.exif_transpose(img)
    img = _to_web_mode(img, output_format)

    src_w, src_h = img.size
    target_w, target_h = BANNER_WIDTH, BANNER_HEIGHT

    # Scale so the image fully covers the target canvas (cover mode)
    scale = max(target_w / src_w, target_h / src_h)
    scaled_w = int(src_w * scale)
    scaled_h = int(src_h * scale)
    img = img.resize((scaled_w, scaled_h), Image.LANCZOS)

    # Crop to exact banner dimensions
    excess_h = scaled_h - target_h
    if vertical_focus == "Top (Keep Roof/Sky)":
        top = 0
    elif vertical_focus == "Bottom (Keep Lawn/Ground)":
        top = excess_h
    else:  # Center
        top = excess_h // 2

    left = (scaled_w - target_w) // 2
    img = img.crop((left, top, left + target_w, top + target_h))

    buf = io.BytesIO()
    save_kwargs: dict = {"quality": quality, "optimize": True}
    if output_format == "WEBP":
        save_kwargs["method"] = 6
    img.save(buf, format=output_format, **save_kwargs)
    buf.seek(0)

    ext = "webp" if output_format == "WEBP" else output_format.lower()
    return buf.read(), ext


# ---------------------------------------------------------------------------
# R2 operations
# ---------------------------------------------------------------------------

def list_objects(prefix: str = "") -> list[dict]:
    """Return a list of object metadata dicts from the bucket."""
    client = get_r2_client()
    paginator = client.get_paginator("list_objects_v2")
    objects: list[dict] = []
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=prefix):
        objects.extend(page.get("Contents", []))
    return objects


def upload_object(key: str, data: bytes, content_type: str) -> None:
    """Upload bytes to R2 under the given key."""
    client = get_r2_client()
    client.put_object(
        Bucket=BUCKET_NAME,
        Key=key,
        Body=data,
        ContentType=content_type,
    )


def download_object(key: str) -> bytes:
    """Download an object from R2 and return its raw bytes."""
    client = get_r2_client()
    response = client.get_object(Bucket=BUCKET_NAME, Key=key)
    return response["Body"].read()


def delete_object(key: str) -> None:
    """Delete an object from the bucket."""
    client = get_r2_client()
    client.delete_object(Bucket=BUCKET_NAME, Key=key)


def generate_presigned_url(key: str, expires_in: int = DOWNLOAD_EXPIRY) -> str:
    """Generate a presigned GET URL for a bucket object."""
    client = get_r2_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET_NAME, "Key": key},
        ExpiresIn=expires_in,
    )


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


def _image_key(key: str) -> bool:
    return key.lower().rsplit(".", 1)[-1] in SUPPORTED_UPLOAD_TYPES


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def page_browse() -> None:
    st.header("Browse bucket")

    prefix = st.text_input("Filter by prefix (folder)", placeholder="e.g. properties/sydney/")

    with st.spinner("Fetching object list…"):
        try:
            objects = list_objects(prefix=prefix)
        except (BotoCoreError, ClientError) as exc:
            st.error(f"Could not list objects: {exc}")
            return

    if not objects:
        st.info("No objects found.")
        return

    objects.sort(key=lambda o: o.get("LastModified", ""), reverse=True)

    total_size = sum(o.get("Size", 0) for o in objects)
    st.caption(f"{len(objects)} objects · {_fmt_bytes(total_size)} total")

    image_keys = [o["Key"] for o in objects if _image_key(o["Key"])]
    other_keys = [o["Key"] for o in objects if not _image_key(o["Key"])]

    if image_keys:
        st.subheader("Images")
        cols_per_row = 3
        for row_start in range(0, len(image_keys), cols_per_row):
            cols = st.columns(cols_per_row)
            for col, key in zip(cols, image_keys[row_start: row_start + cols_per_row]):
                with col:
                    # Use a short-lived presigned URL so the browser fetches
                    # the image directly from R2 — no server-side download needed.
                    try:
                        thumb_url = generate_presigned_url(key, expires_in=THUMBNAIL_EXPIRY)
                        st.image(thumb_url, caption=key.split("/")[-1], use_container_width=True)
                    except Exception:
                        st.warning(f"Could not load thumbnail for {key.split('/')[-1]}")

                    with st.expander("Actions"):
                        obj_meta = next((o for o in objects if o["Key"] == key), {})
                        st.write(f"Size: {_fmt_bytes(obj_meta.get('Size', 0))}")
                        st.write(f"Modified: {obj_meta.get('LastModified', '—')}")

                        dl_url = generate_presigned_url(key, expires_in=DOWNLOAD_EXPIRY)
                        st.markdown(f"[Presigned link (1h)]({dl_url})")

                        dl_raw = download_object(key)
                        st.download_button(
                            "Download original",
                            data=dl_raw,
                            file_name=key.split("/")[-1],
                            key=f"dl_{key}",
                        )

                        if st.button("Delete", key=f"del_{key}", type="secondary"):
                            delete_object(key)
                            st.success(f"Deleted {key}")
                            st.rerun()

    if other_keys:
        st.subheader("Other files")
        for key in other_keys:
            obj_meta = next((o for o in objects if o["Key"] == key), {})
            with st.expander(key):
                st.write(f"Size: {_fmt_bytes(obj_meta.get('Size', 0))}")
                st.write(f"Modified: {obj_meta.get('LastModified', '—')}")
                url = generate_presigned_url(key)
                st.markdown(f"[Presigned link (1h)]({url})")


def page_upload() -> None:
    st.header("Upload & optimise images")

    with st.form("upload_form", clear_on_submit=True):
        uploaded_files = st.file_uploader(
            "Select images",
            type=SUPPORTED_UPLOAD_TYPES,
            accept_multiple_files=True,
        )
        folder = st.text_input(
            "Destination folder (optional)",
            placeholder="e.g. properties/sydney",
            help="No trailing slash needed",
        )

        st.markdown("**Web optimisation settings**")
        col1, col2, col3 = st.columns(3)
        with col1:
            output_fmt = st.selectbox("Output format", ["WebP", "JPEG", "Original"])
        with col2:
            quality = st.slider("Quality", min_value=50, max_value=100, value=DEFAULT_QUALITY)
        with col3:
            strip_exif = st.checkbox("Strip EXIF metadata", value=True)

        st.markdown("---")
        st.markdown("**Sizing**")

        is_banner = st.checkbox("Is Featured Banner?", value=False)

        banner_focus = st.selectbox(
            "Banner Vertical Focus",
            BANNER_FOCUS_OPTIONS,
            help=(
                "Controls which part of the image is kept when cropping to "
                f"{BANNER_WIDTH}×{BANNER_HEIGHT}px. Only applied when "
                "'Is Featured Banner?' is checked."
            ),
            disabled=False,
        )

        if is_banner:
            st.caption(
                f"Output will be forced to exactly {BANNER_WIDTH}×{BANNER_HEIGHT}px "
                f"and cropped using the '{banner_focus}' focus."
            )
        else:
            st.caption(
                f"Standard mode: height capped at {STANDARD_MAX_HEIGHT}px "
                "(width scaled proportionally)."
            )

        submitted = st.form_submit_button("Upload", type="primary")

    if not submitted or not uploaded_files:
        return

    folder_prefix = folder.strip().rstrip("/") + "/" if folder.strip() else ""
    pil_fmt = "WEBP" if output_fmt == "WebP" else ("JPEG" if output_fmt == "JPEG" else None)
    content_type_map = {"WEBP": "image/webp", "JPEG": "image/jpeg"}

    progress = st.progress(0, text="Starting…")
    results: list[tuple[str, bool, str]] = []

    for idx, file in enumerate(uploaded_files):
        progress.progress(idx / len(uploaded_files), text=f"Processing {file.name}…")
        raw = file.read()
        original_name = file.name
        stem = original_name.rsplit(".", 1)[0]

        try:
            if pil_fmt is None:
                # Original — no re-encoding
                data = raw
                ext = original_name.rsplit(".", 1)[-1].lower()
                content_type = SUPPORTED_MIME.get(ext, "application/octet-stream")
                key = f"{folder_prefix}{original_name}"
            elif is_banner:
                data, ext = process_banner(
                    raw,
                    vertical_focus=banner_focus,
                    quality=quality,
                    strip_exif=strip_exif,
                    output_format=pil_fmt,
                )
                content_type = content_type_map[pil_fmt]
                key = f"{folder_prefix}{stem}_banner.{ext}"
            else:
                data, ext = process_standard(
                    raw,
                    quality=quality,
                    strip_exif=strip_exif,
                    output_format=pil_fmt,
                )
                content_type = content_type_map[pil_fmt]
                key = f"{folder_prefix}{stem}.{ext}"

            upload_object(key, data, content_type)

            savings = (1 - len(data) / len(raw)) * 100 if len(raw) else 0
            size_note = f"{_fmt_bytes(len(data))}"
            if pil_fmt:
                size_note += f" ({savings:+.0f}% vs original)"
            if is_banner and pil_fmt:
                size_note += f" · {BANNER_WIDTH}×{BANNER_HEIGHT}px banner"
            results.append((file.name, True, f"Saved as `{key}` · {size_note}"))

        except Exception as exc:
            results.append((file.name, False, str(exc)))

    progress.progress(1.0, text="Done")

    for name, ok, msg in results:
        if ok:
            st.success(f"**{name}** — {msg}")
        else:
            st.error(f"**{name}** — {msg}")


def page_settings() -> None:
    st.header("Connection settings")

    st.info(
        "Credentials are loaded from environment variables. "
        "Update them in the Replit Secrets vault — no code changes required."
    )

    # Pure-markdown table — no numpy/pandas dependency
    vars_to_check = ["R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]
    rows_md = ["| Variable | Status |", "|---|:---:|"]
    for var in vars_to_check:
        val = os.environ.get(var, "")
        status = "✅ Set" if val else "❌ Missing"
        rows_md.append(f"| `{var}` | {status} |")
    st.markdown("\n".join(rows_md))

    st.markdown("---")
    st.subheader("Bucket health check")
    if st.button("Test connection"):
        with st.spinner("Connecting…"):
            try:
                client = get_r2_client()
                client.head_bucket(Bucket=BUCKET_NAME)
                st.success(f"Connected to bucket **{BUCKET_NAME}** successfully.")
            except ClientError as exc:
                code = exc.response["Error"]["Code"]
                st.error(f"Connection failed ({code}): {exc}")
            except EnvironmentError as exc:
                st.error(str(exc))


# ---------------------------------------------------------------------------
# App entry point
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="NAV Property Compiler",
        page_icon="🏠",
        layout="wide",
    )

    st.sidebar.title("NAV Property\nCompiler")
    st.sidebar.markdown("---")

    page = st.sidebar.radio(
        "Navigate",
        ["Browse bucket", "Upload & optimise", "Settings"],
        label_visibility="collapsed",
    )

    try:
        _load_r2_config()
    except EnvironmentError as exc:
        st.error(str(exc))
        st.stop()

    if page == "Browse bucket":
        page_browse()
    elif page == "Upload & optimise":
        page_upload()
    elif page == "Settings":
        page_settings()


if __name__ == "__main__":
    main()

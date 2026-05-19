"""Ivan's Image Optimizer — Property media management via Cloudflare R2."""

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

APP_NAME = "Ivan's Image Optimizer"
BUCKET_NAME = "nav-property-media"

GALLERY_MAX_HEIGHT = 800     # px — portfolio gallery height cap
BANNER_WIDTH = 1920          # px — featured banner exact output width

DEFAULT_QUALITY = 82
THUMBNAIL_EXPIRY = 300       # 5-minute presigned URLs for browse thumbnails
DOWNLOAD_EXPIRY = 3600       # 1-hour presigned URLs for download links

SUPPORTED_UPLOAD_TYPES = ["jpg", "jpeg", "png", "webp", "tiff", "bmp"]
SUPPORTED_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "tiff": "image/tiff",
    "bmp": "image/bmp",
}

# Corporate palette injected via CSS
CRIMSON = "#990000"
SLATE = "#708090"
BLACK = "#0d0d0d"

# Path to Ivan's avatar image (workspace root)
_IVAN_IMG_PATH = "/home/runner/workspace/Ivan .png"

# CORS configuration block for Cloudflare R2
R2_CORS_CONFIG = [
    {
        "AllowedOrigins": ["*"],
        "AllowedMethods": ["GET", "HEAD"],
        "AllowedHeaders": ["Authorization", "Content-Type", "Range"],
        "ExposeHeaders": ["ETag", "Content-Length", "Content-Type"],
        "MaxAgeSeconds": 300,
    }
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
    endpoint = os.environ.get("R2_ENDPOINT_URL", "").strip()
    key_id = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    missing = [k for k, v in {
        "R2_ENDPOINT_URL": endpoint,
        "R2_ACCESS_KEY_ID": key_id,
        "R2_SECRET_ACCESS_KEY": secret,
    }.items() if not v]
    if missing:
        raise EnvironmentError(f"Missing environment variables: {', '.join(missing)}")
    return R2Config(endpoint_url=endpoint, access_key_id=key_id, secret_access_key=secret)


@st.cache_resource(show_spinner=False)
def get_r2_client():
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
# Image processing
# ---------------------------------------------------------------------------

def _to_web_mode(img: Image.Image, fmt: str) -> Image.Image:
    if fmt == "WEBP":
        return img.convert("RGBA") if img.mode not in ("RGB", "RGBA") else img
    return img.convert("RGB") if img.mode != "RGB" else img


def _encode(img: Image.Image, fmt: str, quality: int) -> tuple[bytes, str]:
    buf = io.BytesIO()
    kwargs: dict = {"quality": quality, "optimize": True}
    if fmt == "WEBP":
        kwargs["method"] = 6
    img.save(buf, format=fmt, **kwargs)
    buf.seek(0)
    ext = "webp" if fmt == "WEBP" else fmt.lower()
    return buf.read(), ext


def process_gallery(
    raw_bytes: bytes,
    *,
    quality: int = DEFAULT_QUALITY,
    output_format: str = "WEBP",
) -> tuple[bytes, str]:
    """Scale down so height ≤ GALLERY_MAX_HEIGHT; width follows proportionally."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes)))
    img = _to_web_mode(img, output_format)
    w, h = img.size
    if h > GALLERY_MAX_HEIGHT:
        img = img.resize((int(w * GALLERY_MAX_HEIGHT / h), GALLERY_MAX_HEIGHT), Image.LANCZOS)
    return _encode(img, output_format, quality)


def process_banner(
    raw_bytes: bytes,
    *,
    quality: int = DEFAULT_QUALITY,
    output_format: str = "WEBP",
) -> tuple[bytes, str]:
    """Scale to exactly BANNER_WIDTH wide; height follows the original aspect ratio — no crop."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes)))
    img = _to_web_mode(img, output_format)
    w, h = img.size
    new_h = int(h * BANNER_WIDTH / w)
    img = img.resize((BANNER_WIDTH, new_h), Image.LANCZOS)
    return _encode(img, output_format, quality)


# ---------------------------------------------------------------------------
# R2 helpers
# ---------------------------------------------------------------------------

def list_objects(prefix: str = "") -> list[dict]:
    client = get_r2_client()
    paginator = client.get_paginator("list_objects_v2")
    out: list[dict] = []
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=prefix):
        out.extend(page.get("Contents", []))
    return out


def upload_object(key: str, data: bytes, content_type: str) -> None:
    get_r2_client().put_object(Bucket=BUCKET_NAME, Key=key, Body=data, ContentType=content_type)


def download_object(key: str) -> bytes:
    return get_r2_client().get_object(Bucket=BUCKET_NAME, Key=key)["Body"].read()


def delete_object(key: str) -> None:
    get_r2_client().delete_object(Bucket=BUCKET_NAME, Key=key)


def presigned_url(key: str, expires_in: int = DOWNLOAD_EXPIRY) -> str:
    return get_r2_client().generate_presigned_url(
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


def _is_image(key: str) -> bool:
    return key.lower().rsplit(".", 1)[-1] in SUPPORTED_UPLOAD_TYPES


def _inject_css() -> None:
    st.markdown(f"""
<style>
    /* Primary buttons → Crimson */
    div.stButton > button[kind="primary"],
    div.stFormSubmitButton > button[kind="primary"] {{
        background-color: {CRIMSON} !important;
        border-color: {CRIMSON} !important;
        color: #ffffff !important;
    }}
    div.stButton > button[kind="primary"]:hover,
    div.stFormSubmitButton > button[kind="primary"]:hover {{
        background-color: #7a0000 !important;
        border-color: #7a0000 !important;
    }}
    /* Sidebar accent line */
    section[data-testid="stSidebar"] {{
        border-right: 3px solid {CRIMSON};
    }}
    /* Column card-style separators */
    .upload-col {{
        border: 1px solid {SLATE};
        border-radius: 8px;
        padding: 1rem;
    }}
</style>
""", unsafe_allow_html=True)


def _upload_bay(
    *,
    bay_key: str,
    title: str,
    subtitle: str,
    mode: str,          # "gallery" | "banner"
    folder_default: str = "",
) -> None:
    """Render one upload bay (used in both left and right columns)."""
    st.markdown(f"### {title}")
    st.caption(subtitle)

    with st.form(key=f"form_{bay_key}", clear_on_submit=True):
        files = st.file_uploader(
            "Drop images here or click to browse",
            type=SUPPORTED_UPLOAD_TYPES,
            accept_multiple_files=True,
            key=f"files_{bay_key}",
        )
        folder = st.text_input(
            "Destination folder (optional)",
            value=folder_default,
            placeholder="e.g. properties/sydney",
            key=f"folder_{bay_key}",
        )
        col_a, col_b = st.columns(2)
        with col_a:
            fmt = st.selectbox("Format", ["WebP", "JPEG", "Original"], key=f"fmt_{bay_key}")
        with col_b:
            quality = st.slider("Quality", 50, 100, DEFAULT_QUALITY, key=f"q_{bay_key}")

        submitted = st.form_submit_button("Upload", type="primary", use_container_width=True)

    if not submitted or not files:
        return

    folder_prefix = folder.strip().rstrip("/") + "/" if folder.strip() else ""
    pil_fmt: str | None = {"WebP": "WEBP", "JPEG": "JPEG"}.get(fmt)
    ct_map = {"WEBP": "image/webp", "JPEG": "image/jpeg"}

    progress = st.progress(0, text="Starting…")
    results: list[tuple[str, bool, str]] = []

    for idx, f in enumerate(files):
        progress.progress(idx / len(files), text=f"Processing {f.name}…")
        raw = f.read()
        stem = f.name.rsplit(".", 1)[0]

        try:
            if pil_fmt is None:
                data, ext = raw, f.name.rsplit(".", 1)[-1].lower()
                ct = SUPPORTED_MIME.get(ext, "application/octet-stream")
                key = f"{folder_prefix}{f.name}"
            elif mode == "banner":
                data, ext = process_banner(raw, quality=quality, output_format=pil_fmt)
                ct = ct_map[pil_fmt]
                key = f"{folder_prefix}{stem}_banner.{ext}"
            else:
                data, ext = process_gallery(raw, quality=quality, output_format=pil_fmt)
                ct = ct_map[pil_fmt]
                key = f"{folder_prefix}{stem}.{ext}"

            upload_object(key, data, ct)
            savings = (1 - len(data) / len(raw)) * 100 if len(raw) else 0
            note = f"`{key}` · {_fmt_bytes(len(data))}"
            if pil_fmt:
                note += f" ({savings:+.0f}%)"
            results.append((f.name, True, note))
        except Exception as exc:
            results.append((f.name, False, str(exc)))

    progress.progress(1.0, text="Done")
    for name, ok, msg in results:
        (st.success if ok else st.error)(f"**{name}** — {msg}")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def page_browse() -> None:
    st.header("Browse bucket")
    prefix = st.text_input("Filter by prefix", placeholder="e.g. properties/sydney/")

    with st.spinner("Fetching…"):
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

    image_keys = [o["Key"] for o in objects if _is_image(o["Key"])]
    other_keys = [o["Key"] for o in objects if not _is_image(o["Key"])]

    if image_keys:
        st.subheader("Images")
        cols_per_row = 3
        for row_start in range(0, len(image_keys), cols_per_row):
            cols = st.columns(cols_per_row)
            for col, key in zip(cols, image_keys[row_start: row_start + cols_per_row]):
                with col:
                    try:
                        # Short-lived presigned URL → browser fetches directly from R2
                        thumb = presigned_url(key, expires_in=THUMBNAIL_EXPIRY)
                        st.image(thumb, caption=key.split("/")[-1], use_container_width=True)
                    except Exception:
                        st.warning(f"Preview unavailable: {key.split('/')[-1]}")

                    with st.expander("Actions"):
                        meta = next((o for o in objects if o["Key"] == key), {})
                        st.write(f"Size: {_fmt_bytes(meta.get('Size', 0))}")
                        st.write(f"Modified: {meta.get('LastModified', '—')}")
                        dl = presigned_url(key, expires_in=DOWNLOAD_EXPIRY)
                        st.markdown(f"[Presigned link (1h)]({dl})")
                        st.download_button(
                            "Download original",
                            data=download_object(key),
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
            meta = next((o for o in objects if o["Key"] == key), {})
            with st.expander(key):
                st.write(f"Size: {_fmt_bytes(meta.get('Size', 0))}")
                st.write(f"Modified: {meta.get('LastModified', '—')}")
                st.markdown(f"[Presigned link (1h)]({presigned_url(key)})")


def page_upload() -> None:
    st.header("Upload Images")
    st.caption("Two independent upload bays — choose the right pipeline for each batch.")

    left, right = st.columns(2, gap="large")

    with left:
        st.markdown(
            f"<div style='border-left:4px solid {SLATE};padding-left:0.75rem;margin-bottom:0.5rem'>"
            f"<strong style='color:{SLATE}'>Portfolio Gallery</strong></div>",
            unsafe_allow_html=True,
        )
        _upload_bay(
            bay_key="gallery",
            title="",
            subtitle=f"Scales photos to max **{GALLERY_MAX_HEIGHT}px height** — width follows proportionally.",
            mode="gallery",
        )

    with right:
        st.markdown(
            f"<div style='border-left:4px solid {CRIMSON};padding-left:0.75rem;margin-bottom:0.5rem'>"
            f"<strong style='color:{CRIMSON}'>Featured Banner</strong></div>",
            unsafe_allow_html=True,
        )
        _upload_bay(
            bay_key="banner",
            title="",
            subtitle=f"Forces width to exactly **{BANNER_WIDTH}px** — height scales naturally, zero cropping.",
            mode="banner",
        )


def page_settings() -> None:
    st.header("Connection settings")
    st.info("Credentials are loaded from environment variables — edit them in the Replit Secrets vault.")

    vars_to_check = ["R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]
    rows_md = ["| Variable | Status |", "|---|:---:|"]
    for var in vars_to_check:
        status = "✅ Set" if os.environ.get(var, "") else "❌ Missing"
        rows_md.append(f"| `{var}` | {status} |")
    st.markdown("\n".join(rows_md))

    st.markdown("---")
    st.subheader("Bucket health check")
    if st.button("Test connection"):
        with st.spinner("Connecting…"):
            try:
                get_r2_client().head_bucket(Bucket=BUCKET_NAME)
                st.success(f"Connected to **{BUCKET_NAME}** successfully.")
            except ClientError as exc:
                st.error(f"Connection failed ({exc.response['Error']['Code']}): {exc}")
            except EnvironmentError as exc:
                st.error(str(exc))

    st.markdown("---")
    st.subheader("R2 CORS configuration")
    st.markdown(
        "Add this JSON block to your Cloudflare R2 bucket **CORS policy** "
        "(R2 → Bucket → Settings → CORS) to allow browser-direct thumbnail fetches:"
    )
    import json
    st.code(json.dumps(R2_CORS_CONFIG, indent=2), language="json")
    st.caption(
        "These rules allow any origin to make GET/HEAD requests (presigned URL fetches). "
        "Once saved in R2, browser thumbnails on the Browse page will load without CORS errors."
    )


# ---------------------------------------------------------------------------
# App entry point
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title=APP_NAME,
        page_icon="🏠",
        layout="wide",
    )

    _inject_css()

    # Sidebar — Ivan avatar + branding (base64 embed avoids numpy/libstdc++ dependency)
    if os.path.isfile(_IVAN_IMG_PATH):
        import base64
        with open(_IVAN_IMG_PATH, "rb") as _f:
            _b64 = base64.b64encode(_f.read()).decode()
        st.sidebar.markdown(
            f"<img src='data:image/png;base64,{_b64}' style='width:100%;border-radius:8px;'>",
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.markdown(
            "<div style='text-align:center;font-size:3rem'>🧭</div>",
            unsafe_allow_html=True,
        )

    st.sidebar.markdown(
        f"<h2 style='color:{CRIMSON};margin-top:0.25rem;text-align:center'>{APP_NAME}</h2>",
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("---")

    page = st.sidebar.radio(
        "Navigate",
        ["Browse bucket", "Upload Images", "Settings"],
        label_visibility="collapsed",
    )

    try:
        _load_r2_config()
    except EnvironmentError as exc:
        st.error(str(exc))
        st.stop()

    if page == "Browse bucket":
        page_browse()
    elif page == "Upload Images":
        page_upload()
    elif page == "Settings":
        page_settings()


if __name__ == "__main__":
    main()

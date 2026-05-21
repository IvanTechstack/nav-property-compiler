"""Ivan's Image Optimizer — Property media management via Cloudflare R2."""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass

import boto3
import streamlit as st
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps

# ---------------------------------------------------------------------------
# Constants & Configurations
# ---------------------------------------------------------------------------

APP_NAME = "Ivan's Image Optimizer"
BUCKET_NAME = "nav-property-media"

GALLERY_MAX_HEIGHT = 800     # px — portfolio gallery height cap
BANNER_WIDTH = 1920          # px — featured banner exact output width
STORY_COVER_MAX_WIDTH = 600  # px — interactive story cover width cap

DEFAULT_QUALITY = 82
THUMBNAIL_EXPIRY = 300       # 5-minute presigned URLs for browse thumbnails
DOWNLOAD_EXPIRY = 3600       # 1-hour presigned URLs for download links

SUPPORTED_UPLOAD_TYPES = ["jpg", "jpeg", "png", "webp", "tiff", "bmp", "gif"]
SUPPORTED_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "tiff": "image/tiff",
    "bmp": "image/bmp",
    "gif": "image/gif",
}
GALLERY_TYPES = ["jpg", "jpeg", "png", "webp", "tiff", "bmp"]  # no gif

CRIMSON = "#990000"
SLATE = "#708090"
BLACK = "#0d0d0d"

_IVAN_IMG_PATH = "Ivan .png"

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
# R2 Client Connection
# ---------------------------------------------------------------------------

@dataclass
class R2Config:
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket: str = BUCKET_NAME


def _get_secret(key: str) -> str:
    try:
        val = st.secrets.get(key, "")
        if val:
            return str(val).strip()
    except Exception:
        pass
    return os.environ.get(key, "").strip()


def _load_r2_config() -> R2Config:
    endpoint = _get_secret("R2_ENDPOINT_URL")
    key_id   = _get_secret("R2_ACCESS_KEY_ID")
    secret   = _get_secret("R2_SECRET_ACCESS_KEY")
    missing = [k for k, v in {
        "R2_ENDPOINT_URL": endpoint,
        "R2_ACCESS_KEY_ID": key_id,
        "R2_SECRET_ACCESS_KEY": secret,
    }.items() if not v]
    if missing:
        raise EnvironmentError(f"Missing secrets/env vars: {', '.join(missing)}")
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
# Core Image Optimizers
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


def process_gallery(raw_bytes: bytes, *, quality: int = DEFAULT_QUALITY, output_format: str = "WEBP") -> tuple[bytes, str]:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes)))
    img = _to_web_mode(img, output_format)
    w, h = img.size
    if h > GALLERY_MAX_HEIGHT:
        img = img.resize((int(w * GALLERY_MAX_HEIGHT / h), GALLERY_MAX_HEIGHT), Image.LANCZOS)
    return _encode(img, output_format, quality)


def process_banner(raw_bytes: bytes, *, quality: int = DEFAULT_QUALITY, output_format: str = "WEBP") -> tuple[bytes, str]:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes)))
    img = _to_web_mode(img, output_format)
    w, h = img.size
    new_h = int(h * BANNER_WIDTH / w)
    img = img.resize((BANNER_WIDTH, new_h), Image.LANCZOS)
    return _encode(img, output_format, quality)


def process_story_cover(raw_bytes: bytes, *, quality: int = DEFAULT_QUALITY, src_ext: str = "") -> tuple[bytes, str]:
    if src_ext == "gif":
        img = Image.open(io.BytesIO(raw_bytes))
        w, h = img.size
        if w > STORY_COVER_MAX_WIDTH:
            img = img.resize((STORY_COVER_MAX_WIDTH, int(h * STORY_COVER_MAX_WIDTH / w)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        buf.seek(0)
        return buf.read(), "gif"

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes)))
    img = _to_web_mode(img, "WEBP")
    w, h = img.size
    if w > STORY_COVER_MAX_WIDTH:
        img = img.resize((STORY_COVER_MAX_WIDTH, int(h * STORY_COVER_MAX_WIDTH / w)), Image.LANCZOS)
    return _encode(img, "WEBP", quality)


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
    return get_r2_client().generate_presigned_url("get_object", Params={"Bucket": BUCKET_NAME, "Key": key}, ExpiresIn=expires_in)


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _is_image(key: str) -> bool:
    return key.lower().rsplit(".", 1)[-1] in SUPPORTED_UPLOAD_TYPES


def _thumbnail_b64(key: str, max_w: int = 400) -> str | None:
    import base64
    try:
        raw = download_object(key)
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        elif img.mode == "RGBA":
            img = img.convert("RGB")
        w, h = img.size
        if w > max_w:
            img = img.resize((max_w, int(h * max_w / w)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=72, optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _inject_css() -> None:
    st.markdown(f"""
<style>
    div.stButton > button[kind="primary"] {{
        background-color: {CRIMSON} !important;
        border-color: {CRIMSON} !important;
        color: #ffffff !important;
    }}
    div.stButton > button[kind="primary"]:hover {{
        background-color: #7a0000 !important;
        border-color: #7a0000 !important;
    }}
    section[data-testid="stSidebar"] {{
        background-color: #ffffff !important;
        border-right: 3px solid {CRIMSON};
    }}
    section[data-testid="stSidebar"] > div {{
        background-color: #ffffff !important;
    }}
    .sidebar-divider {{
        border: none;
        border-top: 1px solid #d9d9d9;
        margin: 0.75rem 0 1rem 0;
    }}
</style>
""", unsafe_allow_html=True)


def _col_header(label: str, spec: str, color: str) -> None:
    st.markdown(
        f"<div style='border-top:3px solid {color};padding-top:0.6rem;margin-bottom:0.6rem'>"
        f"<span style='font-weight:700;font-size:0.95rem;color:{color}'>{label}</span><br>"
        f"<span style='font-size:0.75rem;color:#888'>{spec}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Interactive Browse Directory
# ---------------------------------------------------------------------------

def page_browse() -> None:
    st.header("Browse bucket")

    with st.spinner("Loading bucket…"):
        try:
            all_objects = list_objects(prefix="")
        except (BotoCoreError, ClientError) as exc:
            st.error(f"Could not list objects: {exc}")
            return

    if not all_objects:
        st.info("Bucket is empty.")
        return

    seen_folders = set()
    prop_folders = []
    for o in all_objects:
        parts = o["Key"].split("/")
        if len(parts) >= 2 and parts[0] == "properties" and parts[1]:
            f = f"properties/{parts[1]}/"
            if f not in seen_folders:
                seen_folders.add(f)
                prop_folders.append(f)
    prop_folders.sort()

    ALL_LABEL = "— All properties —"
    selected = st.selectbox("📁 Property folder", [ALL_LABEL] + prop_folders, key="browse_folder")

    # Master state initialization
    if "selected_images" not in st.session_state:
        st.session_state.selected_images = set()
    if "current_folder_route" not in st.session_state:
        st.session_state.current_folder_route = ALL_LABEL

    # Wipe memory smoothly when folder switching occurs
    if st.session_state.current_folder_route != selected:
        st.session_state.selected_images = set()
        st.session_state.current_folder_route = selected

    objects = all_objects if selected == ALL_LABEL else [o for o in all_objects if o["Key"].startswith(selected)]
    
    if not objects:
        st.info("No objects found in this folder.")
        return

    objects.sort(key=lambda o: o.get("LastModified", ""), reverse=True)
    image_keys = [o["Key"] for o in objects if _is_image(o["Key"])]
    other_keys = [o["Key"] for o in objects if not _is_image(o["Key"])]

    # Filter out selections belonging to different folders
    active_selected = [k for k in st.session_state.selected_images if k in image_keys]

    # 📁 COMMAND ACCENT ROW (CLEAN ALIGNMENT)
    hdr_left, hdr_right = st.columns([5, 3])
    with hdr_left:
        st.subheader("Images Directory Grid")
    with hdr_right:
        if selected == ALL_LABEL:
            st.markdown("<div style='text-align: right; margin-top: 0.6rem; color: #708090; font-size: 0.85rem; font-weight: 600;'>ℹ️ Select a folder to unlock bulk tools</div>", unsafe_allow_html=True)
        else:
            st.markdown(f"<div style='text-align: right; margin-top: 0.4rem; color: {CRIMSON}; font-size: 0.9rem; font-weight: 700;'>📁 Active Folder Mode</div>", unsafe_allow_html=True)

    # Bulk Actions Center
    if selected != ALL_LABEL and image_keys:
        st.markdown("### 🛠️ Bulk Actions Command Center")
        ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([1.5, 1.5, 2.5, 2.5])
        
        with ctrl_col1:
            if st.button("✅ Select All", use_container_width=True):
                st.session_state.selected_images = set(image_keys)
                st.rerun()
        with ctrl_col2:
            if st.button("❌ Clear All", use_container_width=True):
                st.session_state.selected_images = set()
                st.rerun()
        with ctrl_col3:
            if active_selected:
                try:
                    zip_io = io.BytesIO()
                    with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zip_file:
                        for key in active_selected:
                            zip_file.writestr(key.split("/")[-1], download_object(key))
                    zip_io.seek(0)
                    st.download_button(
                        label=f"📥 Download Selected ({len(active_selected)})",
                        data=zip_io.getvalue(),
                        file_name=f"{selected.split('/')[-2]}-media.zip",
                        mime="application/zip",
                        use_container_width=True,
                        type="primary"
                    )
                except Exception as zip_err:
                    st.error(f"ZIP error: {zip_err}")
            else:
                st.button("📥 Select images to download", disabled=True, use_container_width=True)
        with ctrl_col4:
            if active_selected:
                if st.button("🗑️ Delete Selected", type="secondary", use_container_width=True):
                    with st.spinner("Deleting files…"):
                        for key in active_selected:
                            delete_object(key)
                            st.session_state.selected_images.discard(key)
                    st.success("Selected photos removed.")
                    st.rerun()
            else:
                if st.button("🚨 Purge Folder", type="secondary", use_container_width=True):
                    with st.spinner("Wiping directory path…"):
                        for key in [o["Key"] for o in objects]:
                            delete_object(key)
                    st.session_state.selected_images = set()
                    st.success("Folder cleared completely.")
                    st.rerun()

        st.markdown("---")

    # ── Grid Render Engine ────────────────────────────────────────────────
    if image_keys:
        cols_per_row = 3
        for row_start in range(0, len(image_keys), cols_per_row):
            cols = st.columns(cols_per_row)
            for col, key in zip(cols, image_keys[row_start: row_start + cols_per_row]):
                with col:
                    b64 = _thumbnail_b64(key)
                    filename = key.split("/")[-1]
                    if b64:
                        st.markdown(f"<img src='data:image/jpeg;base64,{b64}' style='width:100%;border-radius:6px;display:block;'>", unsafe_allow_html=True)
                    else:
                        st.markdown(f"<div style='background:#f5f5f5;border-radius:6px;padding:2rem;text-align:center;color:#999;font-size:0.8rem'>⚠ {filename}</div>", unsafe_allow_html=True)
                    
                    # Direct interaction handling
                    if selected == ALL_LABEL:
                        if st.checkbox("Select asset", key=f"root_box_{key}", value=False):
                            st.warning("⚠️ Please pick a property listings folder first before staging photos!")
                            st.rerun()
                    else:
                        # Clean layout verification pass matching memory records smoothly
                        is_checked = key in st.session_state.selected_images
                        box_click = st.checkbox("Select asset", key=f"active_box_{key}", value=is_checked)
                        
                        if box_click != is_checked:
                            if box_click:
                                st.session_state.selected_images.add(key)
                            else:
                                st.session_state.selected_images.discard(key)
                            st.rerun()

                    # Individual Actions Exploded Panel Map
                    with st.expander(f"📄 Actions ({filename})"):
                        meta = next((o for o in objects if o["Key"] == key), {})
                        st.write(f"Size: {_fmt_bytes(meta.get('Size', 0))}")
                        dl = presigned_url(key, expires_in=DOWNLOAD_EXPIRY)
                        st.markdown(f"[Link (1h)]({dl})")
                        st.download_button("Download individual file", data=download_object(key), file_name=filename, key=f"dl_single_{key}")
                        if st.button("Delete individual asset", key=f"del_single_{key}", type="secondary"):
                            delete_object(key)
                            st.session_state.selected_images.discard(key)
                            st.success("Deleted.")
                            st.rerun()

    if other_keys:
        st.subheader("Other files")
        for key in other_keys:
            with st.expander(key):
                dl = presigned_url(key)
                st.markdown(f"[Link (1h)]({dl})")


def page_upload() -> None:
    st.header("Upload Images")
    prop_input = st.text_input("🏠 Property Name or ID", placeholder="e.g. 369 Kendrick Ln")
    st.markdown("**You must enter a valid Property Name or ID above BEFORE staging files or clicking upload.**")
    prefix = prop_input.strip().replace(" ", "-").lower()

    if prefix:
        st.caption(f"Upload path: **`properties/{prefix}/`**")
    else:
        st.info("⬆ Enter a Property Name or ID above, then stage files in any column.")

    st.markdown("<div style='margin:0.75rem 0 0.25rem 0'></div>", unsafe_allow_html=True)
    col_gallery, col_banner, col_story = st.columns(3, gap="medium")

    with col_gallery:
        _col_header("Portfolio Gallery", f"Max {GALLERY_MAX_HEIGHT}px height · WebP · sequential naming", CRIMSON)
        gallery_files = st.file_uploader("Images", type=GALLERY_TYPES, accept_multiple_files=True, key="files_gallery", label_visibility="collapsed")
        gallery_quality = st.slider("Quality", 60, 100, DEFAULT_QUALITY, key="q_gallery")

    with col_banner:
        _col_header("Featured Banner", f"Exactly {BANNER_WIDTH}px wide · WebP · no crop", CRIMSON)
        banner_file = st.file_uploader("One image", type=GALLERY_TYPES, accept_multiple_files=False, key="files_banner", label_visibility="collapsed")
        banner_quality = st.slider("Quality", 60, 100, DEFAULT_QUALITY, key="q_banner")

    with col_story:
        _col_header("Story Cover", f"Max {STORY_COVER_MAX_WIDTH}px wide · GIF preserved · WebP otherwise", CRIMSON)
        story_file = st.file_uploader("One GIF or image", type=SUPPORTED_UPLOAD_TYPES, accept_multiple_files=False, key="files_story", label_visibility="collapsed")
        story_quality = st.slider("Quality", 60, 100, DEFAULT_QUALITY, key="q_story")

    st.markdown("<div style='margin:1.25rem 0 0.5rem 0'></div>", unsafe_allow_html=True)
    _, btn_col, _ = st.columns([1, 2, 1])
    with btn_col:
        master_clicked = st.button("🚀  Process & Upload Property Media Package", type="primary", use_container_width=True, disabled=not prefix)

    if not master_clicked or not prefix:
        return

    st.markdown("---")
    total_ok = 0

    if gallery_files:
        st.markdown(f"<span style='font-weight:600;color:{SLATE}'>Portfolio Gallery</span>", unsafe_allow_html=True)
        prog = st.progress(0)
        for idx, f in enumerate(gallery_files):
            prog.progress(idx / len(gallery_files))
            try:
                data, _ = process_gallery(f.read(), quality=gallery_quality)
                seq = str(idx + 1).zfill(2)
                upload_object(f"properties/{prefix}/{prefix}-{seq}.webp", data, "image/webp")
                st.success(f"✓ `{prefix}-{seq}.webp` — {_fmt_bytes(len(data))}")
                total_ok += 1
            except Exception as exc:
                st.error(f"✗ {f.name}: {exc}")
        prog.progress(1.0)

    if banner_file:
        st.markdown(f"<span style='font-weight:600;color:{CRIMSON}'>Featured Banner</span>", unsafe_allow_html=True)
        try:
            data, _ = process_banner(banner_file.read(), quality=banner_quality)
            upload_object(f"properties/{prefix}/{prefix}-banner.webp", data, "image/webp")
            st.success(f"✓ `{prefix}-banner.webp` — {_fmt_bytes(len(data))}")
            total_ok += 1
        except Exception as exc:
            st.error(f"✗ {banner_file.name}: {exc}")

    if story_file:
        st.markdown(f"<span style='font-weight:600;color:{BLACK}'>Story Cover</span>", unsafe_allow_html=True)
        try:
            raw = story_file.read()
            src_ext = story_file.name.rsplit(".", 1)[-1].lower()
            data, out_ext = process_story_cover(raw, quality=story_quality, src_ext=src_ext)
            ct = "image/gif" if out_ext == "gif" else "image/webp"
            upload_object(f"properties/{prefix}/{prefix}-story-cover.{out_ext}", data, ct)
            st.success(f"✓ `{prefix}-story-cover.{out_ext}` — {_fmt_bytes(len(data))}")
            total_ok += 1
        except Exception as exc:
            st.error(f"✗ {story_file.name}: {exc}")

    st.markdown("---")
    st.success(f"🎉 Package complete — {total_ok} file(s) uploaded to `properties/{prefix}/`")


def page_settings() -> None:
    st.header("Connection settings")
    st.info("Credentials are loaded from environment variables.")
    vars_to_check = ["R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]
    rows_md = ["| Variable | Status |", "|---|:---:|"]
    for var in vars_to_check:
        status = "✅ Set" if os.environ.get(var, "") else "❌ Missing"
        rows_md.append(f"| `{var}` | {status} |")
    st.markdown("\n".join(rows_md))


def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="🏠", layout="wide")
    _inject_css()

    if os.path.isfile(_IVAN_IMG_PATH):
        import base64
        with open(_IVAN_IMG_PATH, "rb") as _f:
            _b64 = base64.b64encode(_f.read()).decode()
        st.sidebar.markdown(f"<img src='data:image/png;base64,{_b64}' style='width:100%;border-radius:8px;'>", unsafe_allow_html=True)
    else:
        st.sidebar.markdown("<div style='text-align:center;font-size:3rem'>🧭</div>", unsafe_allow_html=True)

    st.sidebar.markdown(f"<h2 style='color:{CRIMSON};margin-top:0.25rem;text-align:center;font-size:1.15rem'>{APP_NAME}</h2>", unsafe_allow_html=True)
    st.sidebar.sidebar_divider = st.sidebar.markdown("<hr class='sidebar-divider'>", unsafe_allow_html=True)

    page = st.sidebar.radio("Navigate", ["Browse bucket", "Upload Images", "Settings"], label_visibility="collapsed")

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

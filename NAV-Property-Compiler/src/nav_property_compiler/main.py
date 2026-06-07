"""Ivan's Image Optimizer — Property media management via Cloudflare R2."""

from __future__ import annotations

import io
import json
import os
import re
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

MASTER_FEATURED_PREFIX = "000_MASTER_FEATURED_IMAGES"  # root folder for all featured banners

GALLERY_MAX_HEIGHT = 800     # px — portfolio gallery height cap
BANNER_WIDTH = 1920          # px — featured banner exact output width
STORY_COVER_MAX_WIDTH = 600  # px — interactive story cover width cap

DEFAULT_QUALITY = 82
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
GALLERY_TYPES = ["jpg", "jpeg", "png", "webp", "tiff", "bmp"]

# Corporate palette
CRIMSON = "#990000"
SLATE = "#708090"
BLACK = "#0d0d0d"

_IVAN_IMG_PATH = "/home/runner/workspace/Ivan .png"

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
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes)))
    img = _to_web_mode(img, output_format)
    w, h = img.size
    new_h = int(h * BANNER_WIDTH / w)
    img = img.resize((BANNER_WIDTH, new_h), Image.LANCZOS)
    return _encode(img, output_format, quality)


def process_story_cover(
    raw_bytes: bytes,
    *,
    quality: int = DEFAULT_QUALITY,
    src_ext: str = "",
) -> tuple[bytes, str]:
    if src_ext == "gif":
        img = Image.open(io.BytesIO(raw_bytes))
        w, h = img.size
        if w > STORY_COVER_MAX_WIDTH:
            img = img.resize(
                (STORY_COVER_MAX_WIDTH, int(h * STORY_COVER_MAX_WIDTH / w)), Image.LANCZOS
            )
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        buf.seek(0)
        return buf.read(), "gif"

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes)))
    img = _to_web_mode(img, "WEBP")
    w, h = img.size
    if w > STORY_COVER_MAX_WIDTH:
        img = img.resize(
            (STORY_COVER_MAX_WIDTH, int(h * STORY_COVER_MAX_WIDTH / w)), Image.LANCZOS
        )
    return _encode(img, "WEBP", quality)


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


def _next_gallery_seq(prefix: str) -> int:
    """Return the next available gallery sequence number for a given property prefix."""
    existing = list_objects(prefix=f"properties/{prefix}/")
    max_seq = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)\.webp$")
    for obj in existing:
        fname = obj["Key"].split("/")[-1]
        m = pattern.match(fname)
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return max_seq + 1


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


def _is_master_featured(key: str) -> bool:
    """Return True if the object lives in the master featured images folder."""
    return key.startswith(f"{MASTER_FEATURED_PREFIX}/")


def _thumbnail_b64(key: str, max_w: int = 400) -> str | None:
    """Download from R2, resize, return base64 JPEG — pure PIL, no numpy."""
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
    .upload-col {{
        border: 1px solid {SLATE};
        border-radius: 8px;
        padding: 1rem;
    }}
    .folder-card {{
        background: #fafafa;
        border: 1px solid #e8e8e8;
        border-radius: 12px;
        padding: 1.4rem 0.75rem 0.9rem;
        text-align: center;
        margin-bottom: 0.35rem;
        transition: box-shadow 0.15s;
    }}
    .folder-card:hover {{
        box-shadow: 0 2px 12px rgba(0,0,0,0.08);
    }}
    .folder-card.master {{
        background: #fff8f8;
        border-color: {CRIMSON};
    }}
    .folder-icon {{ font-size: 2.6rem; line-height: 1; }}
    .folder-name {{
        font-size: 0.76rem;
        font-weight: 700;
        margin-top: 0.45rem;
        color: #222;
        word-break: break-all;
        line-height: 1.3;
    }}
    .folder-count {{ font-size: 0.7rem; color: #aaa; margin-top: 0.2rem; }}
    .img-card {{
        position: relative;
        border-radius: 8px;
        overflow: hidden;
        margin-bottom: 0.25rem;
    }}
    .img-card img {{
        width: 100%;
        display: block;
        border-radius: 8px;
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
# Browse — Finder-style helpers
# ---------------------------------------------------------------------------

def _render_folder_grid(
    prop_folders: list[str],
    all_objects: list[dict],
    master_objects: list[dict],
) -> None:
    """Render the top-level visual folder grid."""

    # ── Master Featured pinned card ────────────────────────────────────────
    n_master = len(master_objects)
    master_size = sum(o.get("Size", 0) for o in master_objects)

    st.markdown(
        f"<div class='folder-card master'>"
        f"<div class='folder-icon'>⭐</div>"
        f"<div class='folder-name'>{MASTER_FEATURED_PREFIX}</div>"
        f"<div class='folder-count'>{n_master} featured · {_fmt_bytes(master_size)}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    open_master, del_master_trigger = st.columns(2)
    with open_master:
        if st.button("📂 Open", key="open_master", use_container_width=True):
            st.session_state["browse_open_folder"] = f"{MASTER_FEATURED_PREFIX}/"
            st.rerun()
    with del_master_trigger:
        st.markdown("<span style='font-size:0.7rem;color:#bbb'>🔒 Archive — protected</span>",
                    unsafe_allow_html=True)

    st.markdown("<div style='margin:1.25rem 0 0.75rem'></div>", unsafe_allow_html=True)

    if not prop_folders:
        st.info("No property folders yet. Upload images to create one.")
        return

    # ── Property folder grid (4 columns) ──────────────────────────────────
    st.markdown(
        f"<span style='font-size:0.8rem;font-weight:600;color:{SLATE};text-transform:uppercase;"
        f"letter-spacing:0.06em'>Property Folders</span>",
        unsafe_allow_html=True,
    )
    st.markdown("<div style='margin:0.5rem 0'></div>", unsafe_allow_html=True)

    COLS = 4
    rows = [prop_folders[i:i + COLS] for i in range(0, len(prop_folders), COLS)]

    for row in rows:
        # Pad row to COLS so zip works cleanly
        padded = row + [None] * (COLS - len(row))
        cols = st.columns(COLS)
        for col, folder in zip(cols, padded):
            if folder is None:
                continue
            with col:
                folder_name = folder.rstrip("/").split("/")[-1]
                n_items = len([o for o in all_objects if o["Key"].startswith(folder)])
                folder_size = sum(o.get("Size", 0) for o in all_objects if o["Key"].startswith(folder))

                st.markdown(
                    f"<div class='folder-card'>"
                    f"<div class='folder-icon'>📁</div>"
                    f"<div class='folder-name'>{folder_name}</div>"
                    f"<div class='folder-count'>{n_items} files · {_fmt_bytes(folder_size)}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                if st.button("📂 Open", key=f"open_{folder}", use_container_width=True):
                    st.session_state["browse_open_folder"] = folder
                    # Clear any stale checkbox state for this folder
                    for k in list(st.session_state.keys()):
                        if k.startswith("chk_"):
                            del st.session_state[k]
                    st.rerun()

                if st.button("🗑 Delete folder", key=f"grid_del_{folder}", use_container_width=True):
                    st.session_state["confirm_folder_delete"] = folder
                    st.rerun()

    # ── Inline confirmation for folder delete from grid ────────────────────
    target = st.session_state.get("confirm_folder_delete")
    if target and target != f"{MASTER_FEATURED_PREFIX}/":
        folder_name = target.rstrip("/").split("/")[-1]
        n_del = len([o for o in all_objects if o["Key"].startswith(target)])
        st.warning(
            f"⚠ Delete **{n_del} file(s)** from `{folder_name}/`? "
            f"Featured banners are safe in the Master archive."
        )
        ok_col, cancel_col = st.columns(2)
        with ok_col:
            if st.button("✅ Yes, delete entire folder", key="grid_confirm_yes", type="primary"):
                keys_to_delete = [o["Key"] for o in all_objects if o["Key"].startswith(target)]
                for k in keys_to_delete:
                    delete_object(k)
                st.success(f"Deleted {len(keys_to_delete)} file(s) from `{folder_name}/`.")
                st.session_state.pop("confirm_folder_delete", None)
                st.rerun()
        with cancel_col:
            if st.button("Cancel", key="grid_confirm_cancel"):
                st.session_state.pop("confirm_folder_delete", None)
                st.rerun()


def _render_folder_contents(folder: str, all_objects: list[dict]) -> None:
    """Render the opened folder view with visual image grid + checkbox selection."""

    is_master = _is_master_featured(folder + "placeholder")
    folder_label = folder.rstrip("/").split("/")[-1]

    # ── Navigation header ──────────────────────────────────────────────────
    back_col, title_col = st.columns([1, 5])
    with back_col:
        if st.button("⬅ Back", key="back_to_grid", use_container_width=True):
            st.session_state.pop("browse_open_folder", None)
            # Clear checkbox state
            for k in list(st.session_state.keys()):
                if k.startswith("chk_"):
                    del st.session_state[k]
            st.rerun()
    with title_col:
        icon = "⭐" if is_master else "📁"
        st.markdown(
            f"<h3 style='margin:0;padding-top:0.3rem'>{icon} {folder_label}</h3>",
            unsafe_allow_html=True,
        )

    objects = [o for o in all_objects if o["Key"].startswith(folder)]
    if not objects:
        st.info("This folder is empty.")
        return

    meta_by_key = {o["Key"]: o for o in objects}
    image_keys = [o["Key"] for o in objects if _is_image(o["Key"])]
    other_keys = [o["Key"] for o in objects if not _is_image(o["Key"])]
    total_size = sum(o.get("Size", 0) for o in objects)

    st.caption(f"{len(objects)} objects · {_fmt_bytes(total_size)}")

    # ── Sort & reverse controls ────────────────────────────────────────────
    if image_keys:
        sort_col, rev_col = st.columns([4, 1])
        with sort_col:
            sort_mode = st.radio(
                "Sort",
                ["Filename A→Z", "Filename Z→A", "Date ↓", "Date ↑"],
                horizontal=True,
                key="browse_sort",
                label_visibility="collapsed",
            )
        with rev_col:
            if st.button("↕ Reverse Order", key="browse_rev_btn", use_container_width=True):
                st.session_state["browse_reversed"] = not st.session_state.get("browse_reversed", False)

        if sort_mode == "Filename A→Z":
            image_keys.sort()
        elif sort_mode == "Filename Z→A":
            image_keys.sort(reverse=True)
        elif sort_mode == "Date ↓":
            image_keys.sort(key=lambda k: str(meta_by_key[k].get("LastModified", "")), reverse=True)
        elif sort_mode == "Date ↑":
            image_keys.sort(key=lambda k: str(meta_by_key[k].get("LastModified", "")))

        if st.session_state.get("browse_reversed", False):
            image_keys.reverse()
            st.caption("↕ Display order reversed")

        # ── Image grid with checkboxes ─────────────────────────────────────
        st.markdown("---")
        if not is_master:
            st.markdown(
                "<span style='font-size:0.78rem;color:#888'>"
                "Check images below to select, then use the action buttons.</span>",
                unsafe_allow_html=True,
            )

        COLS = 3
        for row_start in range(0, len(image_keys), COLS):
            cols = st.columns(COLS)
            for col, key in zip(cols, image_keys[row_start: row_start + COLS]):
                with col:
                    b64 = _thumbnail_b64(key)
                    filename = key.split("/")[-1]

                    if b64:
                        st.markdown(
                            f"<div class='img-card'>"
                            f"<img src='data:image/jpeg;base64,{b64}'>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            f"<div style='background:#f5f5f5;border-radius:8px;padding:2.5rem 1rem;"
                            f"text-align:center;color:#bbb;font-size:0.8rem;margin-bottom:0.25rem'>"
                            f"⚠ preview unavailable</div>",
                            unsafe_allow_html=True,
                        )

                    # Checkbox for selection
                    st.checkbox(filename, key=f"chk_{key}", label_visibility="visible")

                    # Quick download link
                    meta = meta_by_key.get(key, {})
                    st.caption(f"{_fmt_bytes(meta.get('Size', 0))}")

        # ── Bulk action bar ────────────────────────────────────────────────
        selected_keys = [k for k in image_keys if st.session_state.get(f"chk_{k}", False)]

        st.markdown("---")
        action_left, action_right = st.columns(2)

        with action_left:
            if selected_keys:
                label = f"🗑 Delete {len(selected_keys)} selected image{'s' if len(selected_keys) != 1 else ''}"
                if st.button(label, key="del_selected", type="primary"):
                    for k in selected_keys:
                        delete_object(k)
                    # Clear checkbox state for deleted keys
                    for k in selected_keys:
                        st.session_state.pop(f"chk_{k}", None)
                    st.success(f"Deleted {len(selected_keys)} image(s).")
                    st.rerun()
            else:
                st.markdown(
                    "<span style='font-size:0.78rem;color:#ccc'>Check images above to enable deletion.</span>",
                    unsafe_allow_html=True,
                )

        with action_right:
            if not is_master:
                n_all = len(image_keys)
                if st.button(
                    f"🗑️ Delete Entire Folder ({n_all} file{'s' if n_all != 1 else ''})",
                    key="del_entire_folder",
                    type="secondary",
                ):
                    st.session_state["confirm_folder_delete"] = folder

        # Inline confirmation for Delete Entire Folder
        if st.session_state.get("confirm_folder_delete") == folder:
            n_del = len([o for o in objects])
            st.warning(
                f"⚠ Permanently delete all **{n_del} file(s)** from `{folder_label}/`? "
                f"Featured banners are safely archived in `{MASTER_FEATURED_PREFIX}/`."
            )
            yes_col, no_col = st.columns(2)
            with yes_col:
                if st.button("✅ Yes, wipe folder", key="wipe_confirm_yes", type="primary"):
                    all_keys = [o["Key"] for o in objects]
                    for k in all_keys:
                        delete_object(k)
                    st.success(f"Folder `{folder_label}/` cleared — {len(all_keys)} file(s) removed.")
                    st.session_state.pop("confirm_folder_delete", None)
                    st.session_state.pop("browse_open_folder", None)
                    st.rerun()
            with no_col:
                if st.button("Cancel", key="wipe_confirm_cancel"):
                    st.session_state.pop("confirm_folder_delete", None)
                    st.rerun()

        # ── Download links (expander) ──────────────────────────────────────
        with st.expander("Download links for all images in this folder"):
            for key in image_keys:
                filename = key.split("/")[-1]
                dl = presigned_url(key, expires_in=DOWNLOAD_EXPIRY)
                st.markdown(f"[{filename}]({dl})")

    if other_keys:
        with st.expander(f"Other files ({len(other_keys)})"):
            for key in other_keys:
                meta = meta_by_key.get(key, {})
                st.write(f"`{key.split('/')[-1]}` — {_fmt_bytes(meta.get('Size', 0))}")
                st.markdown(f"[Presigned link (1h)]({presigned_url(key)})")


# ---------------------------------------------------------------------------
# Pages
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

    # Partition into master-featured and property-folder objects
    master_objects = [o for o in all_objects if o["Key"].startswith(f"{MASTER_FEATURED_PREFIX}/")]
    prop_objects   = [o for o in all_objects if not o["Key"].startswith(f"{MASTER_FEATURED_PREFIX}/")]

    # Build sorted unique property folders
    seen: set[str] = set()
    prop_folders: list[str] = []
    for o in prop_objects:
        parts = o["Key"].split("/")
        if len(parts) >= 2 and parts[0] == "properties" and parts[1]:
            f = f"properties/{parts[1]}/"
            if f not in seen:
                seen.add(f)
                prop_folders.append(f)
    prop_folders.sort()

    open_folder = st.session_state.get("browse_open_folder")

    if open_folder:
        _render_folder_contents(open_folder, all_objects)
    else:
        _render_folder_grid(prop_folders, prop_objects, master_objects)


def page_upload() -> None:
    st.header("Upload Images")

    prop_input = st.text_input(
        "🏠 Property Name or ID",
        placeholder="e.g. 369 Kendrick Ln",
        help="Sets the folder and filename prefix for every upload on this page.",
    )
    st.markdown(
        "**Enter a Property Name or ID above before staging files or uploading.**"
    )
    prefix = prop_input.strip().replace(" ", "-").lower()

    if prefix:
        st.caption(
            f"Gallery path: **`properties/{prefix}/`** · "
            f"Banner path: **`{MASTER_FEATURED_PREFIX}/`**"
        )
    else:
        st.info("⬆ Enter a Property Name or ID above, then stage files in any column.")

    st.markdown("<div style='margin:0.75rem 0 0.25rem'></div>", unsafe_allow_html=True)

    col_gallery, col_banner, col_story = st.columns(3, gap="medium")

    with col_gallery:
        _col_header(
            "Portfolio Gallery",
            f"Max {GALLERY_MAX_HEIGHT}px height · WebP · continues existing sequence",
            SLATE,
        )
        gallery_files = st.file_uploader(
            "Images (multiple allowed)",
            type=GALLERY_TYPES,
            accept_multiple_files=True,
            key="files_gallery",
            label_visibility="collapsed",
        )
        gallery_quality = st.slider("Quality", 60, 100, DEFAULT_QUALITY, key="q_gallery")
        if gallery_files:
            st.caption(f"{len(gallery_files)} file(s) staged — sequence continues from existing assets")

    with col_banner:
        _col_header(
            "Featured Banner",
            f"Exactly {BANNER_WIDTH}px wide · Archived to Master Featured folder",
            CRIMSON,
        )
        banner_file = st.file_uploader(
            "One image",
            type=GALLERY_TYPES,
            accept_multiple_files=False,
            key="files_banner",
            label_visibility="collapsed",
        )
        banner_quality = st.slider("Quality", 60, 100, DEFAULT_QUALITY, key="q_banner")
        if banner_file:
            dest = f"{MASTER_FEATURED_PREFIX}/{prefix or '<prefix>'}-featured.webp"
            st.caption(f"Staged → `{dest}` ⭐")

    with col_story:
        _col_header(
            "Story Cover",
            f"Max {STORY_COVER_MAX_WIDTH}px wide · GIF preserved · WebP otherwise",
            BLACK,
        )
        story_file = st.file_uploader(
            "One GIF or image",
            type=SUPPORTED_UPLOAD_TYPES,
            accept_multiple_files=False,
            key="files_story",
            label_visibility="collapsed",
        )
        story_quality = st.slider("Quality", 60, 100, DEFAULT_QUALITY, key="q_story")
        if story_file:
            src_ext_preview = story_file.name.rsplit(".", 1)[-1].lower()
            out_ext_preview = "gif" if src_ext_preview == "gif" else "webp"
            st.caption(f"Staged → `{prefix or '<prefix>'}-story-cover.{out_ext_preview}`")

    st.markdown("<div style='margin:1.25rem 0 0.5rem'></div>", unsafe_allow_html=True)
    _, btn_col, _ = st.columns([1, 2, 1])
    with btn_col:
        master_clicked = st.button(
            "🚀  Process & Upload Property Media Package",
            type="primary",
            use_container_width=True,
            disabled=not prefix,
        )

    if not master_clicked:
        return

    if not prefix:
        st.error("Enter a Property Name or ID first.")
        return

    has_any = bool(gallery_files) or bool(banner_file) or bool(story_file)
    if not has_any:
        st.warning("Stage at least one file in a column before uploading.")
        return

    st.markdown("---")
    total_ok = 0
    total_err = 0

    # ── Gallery ────────────────────────────────────────────────────────────
    if gallery_files:
        st.markdown(
            f"<span style='font-weight:600;color:{SLATE}'>Portfolio Gallery</span>",
            unsafe_allow_html=True,
        )
        with st.spinner("Checking existing sequence in folder…"):
            start_seq = _next_gallery_seq(prefix)

        prog = st.progress(0, text="Processing gallery…")
        for idx, f in enumerate(gallery_files):
            prog.progress(idx / len(gallery_files), text=f"{f.name}…")
            raw = f.read()
            try:
                data, _ = process_gallery(raw, quality=gallery_quality)
                seq = str(start_seq + idx).zfill(2)
                r2_key = f"properties/{prefix}/{prefix}-{seq}.webp"
                upload_object(r2_key, data, "image/webp")
                savings = (1 - len(data) / len(raw)) * 100 if len(raw) else 0
                st.success(f"✓ `{prefix}-{seq}.webp` — {_fmt_bytes(len(data))} ({savings:+.0f}%)")
                total_ok += 1
            except Exception as exc:
                st.error(f"✗ {f.name}: {exc}")
                total_err += 1
        prog.progress(1.0, text="Gallery done")
        if start_seq > 1:
            st.caption(
                f"ℹ Continued from existing sequence — "
                f"started at `{prefix}-{str(start_seq).zfill(2)}.webp`"
            )

    # ── Banner → Master Featured folder ───────────────────────────────────
    if banner_file:
        st.markdown(
            f"<span style='font-weight:600;color:{CRIMSON}'>Featured Banner → Master Archive</span>",
            unsafe_allow_html=True,
        )
        with st.spinner(f"Processing {banner_file.name}…"):
            try:
                raw = banner_file.read()
                data, _ = process_banner(raw, quality=banner_quality)
                r2_key = f"{MASTER_FEATURED_PREFIX}/{prefix}-featured.webp"
                upload_object(r2_key, data, "image/webp")
                savings = (1 - len(data) / len(raw)) * 100 if len(raw) else 0
                st.success(
                    f"✓ `{MASTER_FEATURED_PREFIX}/{prefix}-featured.webp` — "
                    f"{_fmt_bytes(len(data))} ({savings:+.0f}%) ⭐ Archived"
                )
                total_ok += 1
            except Exception as exc:
                st.error(f"✗ {banner_file.name}: {exc}")
                total_err += 1

    # ── Story cover ────────────────────────────────────────────────────────
    if story_file:
        st.markdown(
            f"<span style='font-weight:600;color:{BLACK}'>Story Cover</span>",
            unsafe_allow_html=True,
        )
        with st.spinner(f"Processing {story_file.name}…"):
            try:
                raw = story_file.read()
                src_ext = story_file.name.rsplit(".", 1)[-1].lower()
                data, out_ext = process_story_cover(raw, quality=story_quality, src_ext=src_ext)
                ct = "image/gif" if out_ext == "gif" else "image/webp"
                r2_key = f"properties/{prefix}/{prefix}-story-cover.{out_ext}"
                upload_object(r2_key, data, ct)
                savings = (1 - len(data) / len(raw)) * 100 if len(raw) else 0
                st.success(
                    f"✓ `{prefix}-story-cover.{out_ext}` — "
                    f"{_fmt_bytes(len(data))} ({savings:+.0f}%)"
                )
                total_ok += 1
            except Exception as exc:
                st.error(f"✗ {story_file.name}: {exc}")
                total_err += 1

    st.markdown("---")
    if total_err == 0:
        st.success(f"🎉 Package complete — {total_ok} file(s) uploaded.")
    else:
        st.warning(f"{total_ok} uploaded · {total_err} failed — check errors above.")


def page_settings() -> None:
    st.header("Connection settings")
    st.info("Credentials are loaded from Streamlit secrets (Streamlit Cloud) or environment variables (Replit/local).")

    vars_to_check = ["R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]
    rows_md = ["| Variable | Status |", "|---|:---:|"]
    for var in vars_to_check:
        status = "✅ Set" if _get_secret(var) else "❌ Missing"
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
        "(R2 → Bucket → Settings → CORS):"
    )
    st.code(json.dumps(R2_CORS_CONFIG, indent=2), language="json")


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
        f"<h2 style='color:{CRIMSON};margin-top:0.25rem;text-align:center;font-size:1.15rem'>"
        f"{APP_NAME}</h2>",
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("<hr class='sidebar-divider'>", unsafe_allow_html=True)

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

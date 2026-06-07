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

MASTER_FEATURED_PREFIX = "000_MASTER_FEATURED_IMAGES"

GALLERY_MAX_HEIGHT = 800
BANNER_WIDTH = 1920
STORY_COVER_MAX_WIDTH = 600

DEFAULT_QUALITY = 82
DOWNLOAD_EXPIRY = 3600

SUPPORTED_UPLOAD_TYPES = ["jpg", "jpeg", "png", "webp", "tiff", "bmp", "gif"]
SUPPORTED_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "webp": "image/webp", "tiff": "image/tiff", "bmp": "image/bmp", "gif": "image/gif",
}
GALLERY_TYPES = ["jpg", "jpeg", "png", "webp", "tiff", "bmp"]

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
    return buf.read(), "webp" if fmt == "WEBP" else fmt.lower()


def process_gallery(raw_bytes: bytes, *, quality: int = DEFAULT_QUALITY,
                    output_format: str = "WEBP") -> tuple[bytes, str]:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes)))
    img = _to_web_mode(img, output_format)
    w, h = img.size
    if h > GALLERY_MAX_HEIGHT:
        img = img.resize((int(w * GALLERY_MAX_HEIGHT / h), GALLERY_MAX_HEIGHT), Image.LANCZOS)
    return _encode(img, output_format, quality)


def process_banner(raw_bytes: bytes, *, quality: int = DEFAULT_QUALITY,
                   output_format: str = "WEBP") -> tuple[bytes, str]:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes)))
    img = _to_web_mode(img, output_format)
    w, h = img.size
    img = img.resize((BANNER_WIDTH, int(h * BANNER_WIDTH / w)), Image.LANCZOS)
    return _encode(img, output_format, quality)


def process_story_cover(raw_bytes: bytes, *, quality: int = DEFAULT_QUALITY,
                        src_ext: str = "") -> tuple[bytes, str]:
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


# ---------------------------------------------------------------------------
# R2 helpers
# ---------------------------------------------------------------------------

def list_objects(prefix: str = "") -> list[dict]:
    paginator = get_r2_client().get_paginator("list_objects_v2")
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
        "get_object", Params={"Bucket": BUCKET_NAME, "Key": key}, ExpiresIn=expires_in
    )


def _next_gallery_seq(prefix: str) -> int:
    existing = list_objects(prefix=f"properties/{prefix}/")
    max_seq = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)\.webp$")
    for obj in existing:
        m = pattern.match(obj["Key"].split("/")[-1])
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return max_seq + 1


def _load_sort_order(folder: str) -> list[str] | None:
    try:
        data = download_object(f"{folder}sort_order.json")
        return json.loads(data).get("order")
    except Exception:
        return None


def _save_sort_order(folder: str, ordered_keys: list[str]) -> None:
    upload_object(
        f"{folder}sort_order.json",
        json.dumps({"order": ordered_keys}).encode(),
        "application/json",
    )


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _safe_key(s: str) -> str:
    """Sanitise an arbitrary string for use as a Streamlit widget key."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", s)


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


def _is_image(key: str) -> bool:
    return key.lower().rsplit(".", 1)[-1] in SUPPORTED_UPLOAD_TYPES


def _thumbnail_b64(key: str, max_w: int = 400) -> str | None:
    """Server-side thumbnail → base64 JPEG. Pure PIL, no numpy."""
    import base64
    try:
        raw = download_object(key)
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
        img = img.convert("RGB") if img.mode != "RGB" else img
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
/* ── Global primary buttons ──────────────────────────────── */
div.stButton > button[kind="primary"],
div.stFormSubmitButton > button[kind="primary"] {{
    background-color:{CRIMSON}!important;
    border-color:{CRIMSON}!important;color:#fff!important;
}}
div.stButton > button[kind="primary"]:hover,
div.stFormSubmitButton > button[kind="primary"]:hover {{
    background-color:#7a0000!important;border-color:#7a0000!important;
}}

/* ── Sidebar ─────────────────────────────────────────────── */
section[data-testid="stSidebar"]{{
    background-color:#ffffff!important;border-right:3px solid {CRIMSON};
}}
section[data-testid="stSidebar"] > div{{background-color:#ffffff!important}}
.sidebar-divider{{border:none;border-top:1px solid #d9d9d9;margin:.75rem 0 1rem}}

/* ─────────────────────────────────────────────────────────
   MAC FINDER FOLDER CARDS
   Each card uses two wrappers:
     div.finder-card        (standard property folder)
     div.finder-card-master (⭐ Master Featured archive)

   Architecture: the card border/background lives on the
   wrapper div; the st.button() inside is transparent so
   the whole card is one clickable surface.
   ─────────────────────────────────────────────────────── */

/* Card shell */
div.finder-card,
div.finder-card-master {{
    border:1.5px solid #eaeaea;
    border-radius:16px;
    background:#fafafa;
    padding:18px 10px 12px;
    text-align:center;
    transition:transform .18s ease,box-shadow .18s ease,
               border-color .18s ease,background-color .18s ease;
    margin-bottom:.2rem;
    cursor:pointer;
}}
div.finder-card:hover {{
    transform:translateY(-5px);
    box-shadow:0 10px 30px rgba(0,0,0,.10);
    border-color:#c0c0c0;
    background:#fff;
}}
div.finder-card-master {{
    background:#fff9f9;border-color:#f0d0d0;
}}
div.finder-card-master:hover {{
    transform:translateY(-5px);
    box-shadow:0 10px 30px rgba(153,0,0,.12);
    border-color:#d08080;background:#fff5f5;
}}

/* Emoji icon row inside each card */
div.finder-card-icon {{
    font-size:2.8rem;line-height:1.25;
    margin-bottom:.3rem;pointer-events:none;
}}

/* The st.button() inside the card: transparent, just the name */
div.finder-card button,
div.finder-card-master button {{
    background:transparent!important;
    border:none!important;box-shadow:none!important;
    padding:.15rem 4px .1rem!important;
    min-height:unset!important;height:auto!important;
    font-size:.9rem!important;font-weight:600!important;
    color:#1a1a1a!important;width:100%!important;
    line-height:1.3!important;
    transition:none!important;
}}
div.finder-card button:hover,
div.finder-card-master button:hover {{
    background:transparent!important;
    text-decoration:underline!important;
    transform:none!important;box-shadow:none!important;
}}
div.finder-card button p,
div.finder-card-master button p {{
    text-align:center!important;margin:0!important;
    white-space:normal!important;word-break:break-word!important;
}}

/* Folder meta line (file count + size) rendered below button */
div.finder-card-meta {{
    font-size:.72rem;color:#aaa;
    padding:.1rem 0 .3rem;pointer-events:none;
}}

/* ── Folder selection checkboxes — circle style ─────────── */
div.sel-circle [data-testid="stCheckbox"] {{
    display:flex!important;justify-content:center!important;
    margin-top:.3rem!important;
}}
div.sel-circle [data-testid="stCheckbox"] input[type="checkbox"] {{
    width:20px!important;height:20px!important;
    border-radius:50%!important;cursor:pointer!important;
    accent-color:{CRIMSON};
}}
div.sel-circle [data-testid="stCheckbox"] label {{display:none!important}}

/* ── Image select-toggle button ──────────────────────────── */
div.img-sel-btn button {{
    min-height:unset!important;height:auto!important;
    padding:.3rem 0!important;
    font-size:.75rem!important;
    border-radius:0 0 10px 10px!important;
    margin-top:-1px!important;
    width:100%!important;
    transition:background-color .15s,color .15s!important;
}}

/* ── Image position number inputs ────────────────────────── */
div.img-order-input [data-testid="stNumberInput"] {{
    margin-top:.2rem!important;
}}
div.img-order-input [data-testid="stNumberInput"] input {{
    text-align:center!important;font-size:.78rem!important;
    padding:.25rem .3rem!important;
}}
div.img-order-input label {{display:none!important}}

/* ── Action bar ──────────────────────────────────────────── */
div.action-bar {{
    background:#f6f6f6;border:1px solid #ebebeb;border-radius:10px;
    padding:.5rem .75rem;margin-bottom:.75rem;
    display:flex;align-items:center;gap:.6rem;
}}

/* ── Upload column boxes ─────────────────────────────────── */
.upload-col{{border:1px solid {SLATE};border-radius:8px;padding:1rem}}
</style>
""", unsafe_allow_html=True)


def _col_header(label: str, spec: str, color: str) -> None:
    st.markdown(
        f"<div style='border-top:3px solid {color};padding-top:.6rem;margin-bottom:.6rem'>"
        f"<span style='font-weight:700;font-size:.95rem;color:{color}'>{label}</span><br>"
        f"<span style='font-size:.75rem;color:#888'>{spec}</span></div>",
        unsafe_allow_html=True,
    )


def _render_image_card(key: str, b64: str | None, selected: bool) -> None:
    """16:9 aspect-ratio image card via pure HTML (no numpy / no st.image)."""
    border = f"3px solid {CRIMSON}" if selected else "3px solid transparent"
    filename = key.split("/")[-1]
    if b64:
        inner = (
            f"<img src='data:image/jpeg;base64,{b64}' "
            f"style='position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover'>"
        )
    else:
        inner = (
            "<div style='position:absolute;top:0;left:0;width:100%;height:100%;"
            "display:flex;align-items:center;justify-content:center;"
            "color:#ccc;font-size:1.2rem'>⚠</div>"
        )
    st.markdown(
        f"<div style='border:{border};border-radius:10px;overflow:hidden;"
        f"transition:border-color .15s;margin-bottom:0'>"
        f"<div style='position:relative;padding-bottom:56.25%;background:#e8e8e8'>"
        f"{inner}</div>"
        f"<div style='font-size:.66rem;color:#999;padding:3px 7px;"
        f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
        f"background:#fafafa;border-top:1px solid #f0f0f0'>{filename}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Browse — Finder folder grid (pure native Streamlit)
# ---------------------------------------------------------------------------

def _render_folder_grid(
    prop_folders: list[str],
    all_objects: list[dict],
    master_objects: list[dict],
) -> None:
    """
    Mac Finder-style 4-column folder grid built entirely from native Streamlit:
    • st.button()   → open folder on click
    • st.checkbox() → styled as selection circle below each card
    • Selected ≥ 1  → Delete Selected Folders bar at top
    """

    # ── Read current selection from widget state ────────────────────────────
    selected_folders = [
        f for f in prop_folders
        if st.session_state.get(f"sel_folder_{_safe_key(f)}", False)
    ]

    # ── Action bar (visible only when folders are selected) ─────────────────
    if selected_folders:
        n_sel = len(selected_folders)
        st.markdown("<div class='action-bar'>", unsafe_allow_html=True)
        bar_a, bar_b, bar_c = st.columns([2, 4, 2])
        with bar_a:
            if st.button(
                f"🗑 Delete {n_sel} Folder{'s' if n_sel > 1 else ''}",
                key="bulk_del_folders",
                type="primary",
                use_container_width=True,
            ):
                st.session_state["confirm_bulk_delete"] = selected_folders[:]
        with bar_c:
            if st.button("✕ Clear selection", key="clear_folder_sel", use_container_width=True):
                for f in prop_folders:
                    k = f"sel_folder_{_safe_key(f)}"
                    if k in st.session_state:
                        st.session_state[k] = False
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # ── Bulk-delete confirmation ─────────────────────────────────────────────
    if "confirm_bulk_delete" in st.session_state:
        targets = st.session_state["confirm_bulk_delete"]
        names = ", ".join(t.rstrip("/").split("/")[-1] for t in targets)
        st.warning(f"⚠ Permanently delete folders: **{names}**? All contents will be removed.")
        yes_c, no_c = st.columns(2)
        with yes_c:
            if st.button("✅ Confirm delete", key="bulk_del_confirm", type="primary"):
                for folder in targets:
                    if folder.startswith(f"{MASTER_FEATURED_PREFIX}/"):
                        continue
                    for obj in all_objects:
                        if obj["Key"].startswith(folder):
                            delete_object(obj["Key"])
                    st.session_state.pop(f"thumbs_{folder}", None)
                    st.session_state.pop(f"sel_folder_{_safe_key(folder)}", None)
                st.session_state.pop("confirm_bulk_delete", None)
                st.rerun()
        with no_c:
            if st.button("Cancel", key="bulk_del_cancel"):
                st.session_state.pop("confirm_bulk_delete", None)
                st.rerun()

    # ── Master Featured card (full-width, always first) ─────────────────────
    master_n = len(master_objects)
    master_size = _fmt_bytes(sum(o.get("Size", 0) for o in master_objects))
    st.markdown(
        "<div class='finder-card-master'>"
        "<div class='finder-card-icon'>⭐</div>",
        unsafe_allow_html=True,
    )
    if st.button("Master Featured", key="open_master", use_container_width=True):
        st.session_state["browse_open_folder"] = f"{MASTER_FEATURED_PREFIX}/"
        st.session_state.pop(f"thumbs_{MASTER_FEATURED_PREFIX}/", None)
        st.rerun()
    st.markdown(
        f"<div class='finder-card-meta'>{master_n} files · {master_size}</div>"
        f"</div>"
        f"<div style='text-align:center;font-size:.7rem;color:#bbb;"
        f"margin:.2rem 0 1.4rem'>🔒 Protected archive — never deleted</div>",
        unsafe_allow_html=True,
    )

    if not prop_folders:
        st.info("No property folders yet. Upload images to create one.")
        return

    st.markdown(
        "<div style='font-size:.72rem;font-weight:700;color:#aaa;"
        "text-transform:uppercase;letter-spacing:.07em;margin-bottom:.75rem'>"
        "Property Folders</div>",
        unsafe_allow_html=True,
    )

    # ── 4-column property folder grid ───────────────────────────────────────
    COLS = 4
    rows = [prop_folders[i:i + COLS] for i in range(0, len(prop_folders), COLS)]

    for row in rows:
        padded = row + [None] * (COLS - len(row))
        cols = st.columns(COLS)
        for col, folder in zip(cols, padded):
            if folder is None:
                continue
            safe_f = _safe_key(folder)
            folder_name = folder.rstrip("/").split("/")[-1]
            folder_objs = [o for o in all_objects if o["Key"].startswith(folder)]
            n_items = len(folder_objs)
            folder_size = _fmt_bytes(sum(o.get("Size", 0) for o in folder_objs))

            with col:
                # Card shell: emoji + transparent name button + meta line
                st.markdown(
                    "<div class='finder-card'>"
                    "<div class='finder-card-icon'>📁</div>",
                    unsafe_allow_html=True,
                )
                if st.button(folder_name, key=f"fc_{safe_f}", use_container_width=True):
                    st.session_state["browse_open_folder"] = folder
                    st.session_state.pop(f"thumbs_{folder}", None)
                    st.rerun()
                st.markdown(
                    f"<div class='finder-card-meta'>{n_items} files · {folder_size}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                # Selection circle checkbox
                st.markdown("<div class='sel-circle'>", unsafe_allow_html=True)
                st.checkbox(
                    "Select",
                    key=f"sel_folder_{safe_f}",
                    label_visibility="collapsed",
                )
                st.markdown("</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Browse — Opened folder view (pure native Streamlit)
# ---------------------------------------------------------------------------

def _render_folder_contents(folder: str, all_objects: list[dict]) -> None:
    """
    Opened folder: 16:9 image grid with:
    • Checkbox-based image selection (circle-styled)
    • Delete Selected + Clear Folder pinned to top header
    • Sort controls + Save Current Order → sort_order.json
    • Silent spinner thumbnail loading (session-cached)
    """
    is_master = folder.startswith(f"{MASTER_FEATURED_PREFIX}/")
    folder_label = folder.rstrip("/").split("/")[-1]
    icon = "⭐" if is_master else "📁"

    objects = [o for o in all_objects if o["Key"].startswith(folder)]
    meta_by_key = {o["Key"]: o for o in objects}

    image_keys: list[str] = [
        o["Key"] for o in objects
        if _is_image(o["Key"])
    ]
    other_keys: list[str] = [
        o["Key"] for o in objects
        if not _is_image(o["Key"]) and not o["Key"].endswith("sort_order.json")
    ]
    total_size = sum(o.get("Size", 0) for o in objects)

    # ── Apply saved sort order ──────────────────────────────────────────────
    saved_order = _load_sort_order(folder)
    if saved_order:
        existing_set = set(image_keys)
        ordered = [k for k in saved_order if k in existing_set]
        new_keys = [k for k in image_keys if k not in set(ordered)]
        image_keys = ordered + new_keys

    # ── Apply sort radio ────────────────────────────────────────────────────
    sort_mode = st.session_state.get("browse_sort", "Custom order")
    if sort_mode == "Filename A→Z":
        image_keys = sorted(image_keys)
    elif sort_mode == "Filename Z→A":
        image_keys = sorted(image_keys, reverse=True)
    elif sort_mode == "Date ↓":
        image_keys = sorted(image_keys,
                            key=lambda k: str(meta_by_key[k].get("LastModified", "")),
                            reverse=True)
    elif sort_mode == "Date ↑":
        image_keys = sorted(image_keys,
                            key=lambda k: str(meta_by_key[k].get("LastModified", "")))

    if st.session_state.get("browse_reversed", False):
        image_keys.reverse()

    # ── Read image selections from widget state (previous rerun) ───────────
    selected_imgs: list[str] = [
        k for k in image_keys
        if st.session_state.get(f"sel_img_{_safe_key(k)}", False)
    ]
    n_img_sel = len(selected_imgs)

    # ── TOP HEADER ROW ─────────────────────────────────────────────────────
    back_c, title_c, del_c, wipe_c = st.columns([1, 5, 2, 2])

    with back_c:
        if st.button("⬅ Back", key="back_btn", use_container_width=True):
            st.session_state.pop("browse_open_folder", None)
            st.session_state.pop(f"thumbs_{folder}", None)
            st.session_state.pop("browse_reversed", None)
            st.rerun()

    with title_c:
        st.markdown(
            f"<h3 style='margin:0;padding-top:.2rem'>{icon} {folder_label}</h3>"
            f"<span style='font-size:.76rem;color:#aaa'>"
            f"{len(image_keys)} images · {_fmt_bytes(total_size)}</span>",
            unsafe_allow_html=True,
        )

    with del_c:
        if n_img_sel:
            if st.button(
                f"🗑 Delete {n_img_sel} Photo{'s' if n_img_sel > 1 else ''}",
                key="del_sel_imgs",
                type="primary",
                use_container_width=True,
            ):
                for k in selected_imgs:
                    delete_object(k)
                    st.session_state.pop(f"sel_img_{_safe_key(k)}", None)
                st.session_state.pop(f"thumbs_{folder}", None)
                st.rerun()

    with wipe_c:
        if not is_master and image_keys:
            if st.button("🗑️ Clear Folder", key="clear_folder_btn", use_container_width=True):
                st.session_state["confirm_wipe"] = folder

    st.markdown("<div style='margin:.3rem 0'></div>", unsafe_allow_html=True)

    # ── Clear-folder confirmation ───────────────────────────────────────────
    if st.session_state.get("confirm_wipe") == folder:
        st.warning(
            f"⚠ Permanently wipe all **{len(image_keys)} image(s)** from `{folder_label}/`? "
            f"Featured banners are safe in `{MASTER_FEATURED_PREFIX}/`."
        )
        yw, nw = st.columns(2)
        with yw:
            if st.button("✅ Yes, wipe folder", key="wipe_yes", type="primary"):
                for o in objects:
                    delete_object(o["Key"])
                st.session_state.pop("confirm_wipe", None)
                st.session_state.pop("browse_open_folder", None)
                st.session_state.pop(f"thumbs_{folder}", None)
                st.success(f"`{folder_label}/` cleared.")
                st.rerun()
        with nw:
            if st.button("Cancel", key="wipe_no"):
                st.session_state.pop("confirm_wipe", None)
                st.rerun()

    if not image_keys:
        st.info("No images in this folder.")
        if other_keys:
            with st.expander(f"Other files ({len(other_keys)})"):
                for k in other_keys:
                    st.write(f"`{k.split('/')[-1]}`")
        return

    # ── Sort controls + Save Order ──────────────────────────────────────────
    sort_a, sort_b = st.columns([6, 2])
    with sort_a:
        new_sort = st.radio(
            "Sort", ["Custom order", "Filename A→Z", "Filename Z→A", "Date ↓", "Date ↑"],
            horizontal=True,
            key="browse_sort",
            label_visibility="collapsed",
        )
    with sort_b:
        if st.button(
            "📌 Save Order",
            key="save_order_btn",
            use_container_width=True,
            help="Reads the position numbers below each photo and saves that sequence as the default order for this folder",
        ):
            # Read each image's position number_input, sort by it, save to R2
            order_pairs = [
                (int(st.session_state.get(f"order_{_safe_key(k)}", i + 1)), i, k)
                for i, k in enumerate(image_keys)
            ]
            order_pairs.sort(key=lambda x: (x[0], x[1]))
            sorted_keys = [k for _, _, k in order_pairs]
            _save_sort_order(folder, sorted_keys)
            # Clear position inputs so they reset to the new sequence
            for k in image_keys:
                st.session_state.pop(f"order_{_safe_key(k)}", None)
            st.session_state.pop(f"thumbs_{folder}", None)
            st.success("Order saved.", icon="📌")
            st.rerun()

    # Re-apply sort if radio just changed
    if new_sort != sort_mode:
        st.rerun()

    # ── Load thumbnails — silent, session-cached ────────────────────────────
    cache_key = f"thumbs_{folder}"
    if cache_key not in st.session_state:
        with st.spinner("Preparing images…"):
            thumbs: dict[str, str | None] = {k: _thumbnail_b64(k) for k in image_keys}
        st.session_state[cache_key] = thumbs
    else:
        thumbs = st.session_state[cache_key]
        missing = [k for k in image_keys if k not in thumbs]
        if missing:
            with st.spinner(f"Loading {len(missing)} new image{'s' if len(missing) > 1 else ''}…"):
                for k in missing:
                    thumbs[k] = _thumbnail_b64(k)
            st.session_state[cache_key] = thumbs

    # ── 3-column image grid ─────────────────────────────────────────────────
    st.markdown("<div style='margin-top:.5rem'></div>", unsafe_allow_html=True)
    img_cols = st.columns(3)
    for idx, key in enumerate(image_keys):
        safe_k = _safe_key(key)
        is_sel = st.session_state.get(f"sel_img_{safe_k}", False)
        with img_cols[idx % 3]:
            # 16:9 image with crimson border when selected
            _render_image_card(key, thumbs.get(key), is_sel)

            # ── Click-to-select toggle button ────────────────────────────
            # Full-width button fused to the bottom of the image card.
            # Clicking anywhere on it toggles selection (crimson = selected).
            st.markdown("<div class='img-sel-btn'>", unsafe_allow_html=True)
            btn_label = "✓  Selected" if is_sel else "○  Select"
            btn_type  = "primary"    if is_sel else "secondary"
            if st.button(
                btn_label,
                key=f"selb_img_{safe_k}",
                type=btn_type,
                use_container_width=True,
            ):
                st.session_state[f"sel_img_{safe_k}"] = not is_sel
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

            # ── Position number input for manual reordering ──────────────
            st.markdown("<div class='img-order-input'>", unsafe_allow_html=True)
            st.number_input(
                "Position",
                min_value=1,
                max_value=len(image_keys),
                value=idx + 1,
                step=1,
                key=f"order_{safe_k}",
                label_visibility="collapsed",
                help=f"Set display position for this photo, then click 📌 Save Order",
            )
            st.markdown("</div>", unsafe_allow_html=True)

    # ── Other (non-image) files ─────────────────────────────────────────────
    if other_keys:
        with st.expander(f"Other files ({len(other_keys)})"):
            for key in other_keys:
                m = meta_by_key.get(key, {})
                st.write(f"`{key.split('/')[-1]}` — {_fmt_bytes(m.get('Size', 0))}")
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

    master_objects = [o for o in all_objects if o["Key"].startswith(f"{MASTER_FEATURED_PREFIX}/")]
    prop_objects   = [o for o in all_objects if not o["Key"].startswith(f"{MASTER_FEATURED_PREFIX}/")]

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
    st.markdown("**Enter a Property Name or ID above before staging files or uploading.**")
    prefix = prop_input.strip().replace(" ", "-").lower()

    if prefix:
        st.caption(
            f"Gallery path: **`properties/{prefix}/`** · "
            f"Banner path: **`{MASTER_FEATURED_PREFIX}/`**"
        )
    else:
        st.info("⬆ Enter a Property Name or ID above, then stage files in any column.")

    st.markdown("<div style='margin:.75rem 0 .25rem'></div>", unsafe_allow_html=True)

    col_gallery, col_banner, col_story = st.columns(3, gap="medium")

    with col_gallery:
        _col_header(
            "Portfolio Gallery",
            f"Max {GALLERY_MAX_HEIGHT}px height · WebP · continues existing sequence",
            SLATE,
        )
        gallery_files = st.file_uploader(
            "Images (multiple allowed)", type=GALLERY_TYPES,
            accept_multiple_files=True, key="files_gallery", label_visibility="collapsed",
        )
        gallery_quality = st.slider("Quality", 60, 100, DEFAULT_QUALITY, key="q_gallery")
        if gallery_files:
            st.caption(f"{len(gallery_files)} file(s) staged — sequence auto-continues")

    with col_banner:
        _col_header(
            "Featured Banner",
            f"Exactly {BANNER_WIDTH}px wide · Archived to Master Featured folder",
            CRIMSON,
        )
        banner_file = st.file_uploader(
            "One image", type=GALLERY_TYPES, accept_multiple_files=False,
            key="files_banner", label_visibility="collapsed",
        )
        banner_quality = st.slider("Quality", 60, 100, DEFAULT_QUALITY, key="q_banner")
        if banner_file:
            st.caption(
                f"Staged → `{MASTER_FEATURED_PREFIX}/{prefix or '<prefix>'}-featured.webp` ⭐"
            )

    with col_story:
        _col_header(
            "Story Cover",
            f"Max {STORY_COVER_MAX_WIDTH}px wide · GIF preserved · WebP otherwise",
            BLACK,
        )
        story_file = st.file_uploader(
            "One GIF or image", type=SUPPORTED_UPLOAD_TYPES, accept_multiple_files=False,
            key="files_story", label_visibility="collapsed",
        )
        story_quality = st.slider("Quality", 60, 100, DEFAULT_QUALITY, key="q_story")
        if story_file:
            src_ext_p = story_file.name.rsplit(".", 1)[-1].lower()
            st.caption(
                f"Staged → `{prefix or '<prefix>'}-story-cover."
                f"{'gif' if src_ext_p == 'gif' else 'webp'}`"
            )

    st.markdown("<div style='margin:1.25rem 0 .5rem'></div>", unsafe_allow_html=True)
    _, btn_col, _ = st.columns([1, 2, 1])
    with btn_col:
        master_clicked = st.button(
            "🚀  Process & Upload Property Media Package",
            type="primary", use_container_width=True, disabled=not prefix,
        )

    if not master_clicked:
        return

    if not prefix:
        st.error("Enter a Property Name or ID first.")
        return

    if not (gallery_files or banner_file or story_file):
        st.warning("Stage at least one file in a column before uploading.")
        return

    st.markdown("---")
    total_ok = 0
    total_err = 0

    if gallery_files:
        st.markdown(
            f"<span style='font-weight:600;color:{SLATE}'>Portfolio Gallery</span>",
            unsafe_allow_html=True,
        )
        with st.spinner("Checking existing sequence…"):
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
                    f"{_fmt_bytes(len(data))} ({savings:+.0f}%) ⭐"
                )
                total_ok += 1
            except Exception as exc:
                st.error(f"✗ {banner_file.name}: {exc}")
                total_err += 1

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
    st.info(
        "Credentials are loaded from Streamlit secrets (Streamlit Cloud) "
        "or environment variables (Replit/local)."
    )
    rows_md = ["| Variable | Status |", "|---|:---:|"]
    for var in ["R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]:
        rows_md.append(f"| `{var}` | {'✅ Set' if _get_secret(var) else '❌ Missing'} |")
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
    st.set_page_config(page_title=APP_NAME, page_icon="🏠", layout="wide")
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
        f"<h2 style='color:{CRIMSON};margin-top:.25rem;text-align:center;"
        f"font-size:1.15rem'>{APP_NAME}</h2>",
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("<hr class='sidebar-divider'>", unsafe_allow_html=True)

    page = st.sidebar.radio(
        "Navigate", ["Browse bucket", "Upload Images", "Settings"],
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

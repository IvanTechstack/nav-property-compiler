# ── Three staging columns (no per-column submit buttons) ───────────────
    col_gallery, col_banner, col_story = st.columns(3, gap="medium")

    with col_gallery:
        _col_header(
            "Portfolio Gallery",
            f"Max {GALLERY_MAX_HEIGHT}px height · WebP · sequential naming",
            CRIMSON,
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
            st.caption(f"{len(gallery_files)} file(s) staged → `{prefix or '<prefix>'}-01.webp` …")

    with col_banner:
        _col_header(
            "Featured Banner",
            f"Exactly {BANNER_WIDTH}px wide · WebP · no crop",
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
            st.caption(f"Staged → `{prefix or '<prefix>'}-banner.webp`")

    with col_story:
        _col_header(
            "Story Cover",
            f"Max {STORY_COVER_MAX_WIDTH}px wide · GIF preserved · WebP otherwise",
            CRIMSON,
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

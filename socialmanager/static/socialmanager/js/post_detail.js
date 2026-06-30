const getPostDetailCookieValue = (name) =>
    document.cookie
        .split(";")
        .map((cookie) => cookie.trim())
        .find((cookie) => cookie.startsWith(`${name}=`))
        ?.slice(name.length + 1) || "";

function setupPostDetailActions() {
    if (!document.body.classList.contains("post-detail-page")) {
        return;
    }

    const likeButton = document.querySelector("[data-detail-like]");
    const likeCount = document.querySelector("[data-detail-like-count]");
    const shareButton = document.querySelector("[data-detail-share]");
    const shareCount = document.querySelector("[data-detail-share-count]");
    const getVideoSecond = () => {
        const video = document.querySelector("video[data-track-watch-url]");
        const watchedSeconds = Number(video?.dataset.maxWatchedSeconds || video?.currentTime || 0);
        return Number.isFinite(watchedSeconds) ? Math.max(Math.round(watchedSeconds), 0) : 0;
    };

    const updateCounts = (data) => {
        if (likeCount && typeof data.likes_count === "number") {
            likeCount.textContent = String(data.likes_count);
        }

        if (shareCount && typeof data.shares_count === "number") {
            shareCount.textContent = String(data.shares_count);
        }
    };

    const toggleEngagement = async (button, activeClass) => {
        const url = button?.dataset.engagementUrl;

        if (!button || !url) {
            return;
        }

        button.disabled = true;
        const body = new FormData();
        body.append("video_second", String(getVideoSecond()));

        try {
            const response = await fetch(url, {
                method: "POST",
                body,
                headers: {
                    "X-CSRFToken": decodeURIComponent(getPostDetailCookieValue("csrftoken")),
                    "X-Requested-With": "XMLHttpRequest",
                },
                credentials: "same-origin",
            });

            if (!response.ok) {
                return;
            }

            const data = await response.json();
            button.classList.toggle(activeClass, data.active);
            button.setAttribute("aria-pressed", String(data.active));
            updateCounts(data);
        } finally {
            button.disabled = false;
        }
    };

    likeButton?.addEventListener("click", () =>
        toggleEngagement(likeButton, "is-liked"),
    );

    shareButton?.addEventListener("click", async () => {
        await toggleEngagement(shareButton, "is-shared");

        try {
            await navigator.clipboard?.writeText(window.location.href);
        } catch (error) {
            // Visual share feedback still works when clipboard access is unavailable.
        }
    });

    document.querySelectorAll("[data-video-engagement-second]").forEach((input) => {
        input.closest("form")?.addEventListener("submit", () => {
            input.value = String(getVideoSecond());
        });
    });
}

function setupCommentHashFocus() {
    if (window.location.hash !== "#comments") {
        return;
    }

    const commentSection = document.querySelector("#comments");
    const textarea = commentSection?.querySelector("textarea");

    if (!commentSection) {
        return;
    }

    window.requestAnimationFrame(() => {
        commentSection.scrollIntoView({
            behavior: "smooth",
            block: "start",
        });
        textarea?.focus();
    });
}

function setupCommentListToggle() {
    const commentsList = document.querySelector("[data-comments-list]");
    const toggleButton = document.querySelector("[data-comments-toggle]");

    if (!commentsList || !toggleButton) {
        return;
    }

    toggleButton.addEventListener("click", () => {
        const shouldExpand = commentsList.classList.contains("is-collapsed");
        const showMoreLabel = toggleButton.dataset.showMoreLabel || "Show more";
        const showLessLabel = toggleButton.dataset.showLessLabel || "Show less";
        commentsList.classList.toggle("is-collapsed", !shouldExpand);
        toggleButton.textContent = shouldExpand ? showLessLabel : showMoreLabel;
    });
}

function setupCommentInteractions() {
    if (!document.body.classList.contains("post-detail-page")) {
        return;
    }

    const closeMenus = () => {
        document.querySelectorAll("[data-comment-menu-toggle]").forEach((button) => {
            button.setAttribute("aria-expanded", "false");
        });
        document.querySelectorAll("[data-comment-menu]").forEach((menu) => {
            menu.hidden = true;
        });
    };

    const closeEditForm = (commentCard) => {
        const body = commentCard?.querySelector("[data-comment-body-copy]");
        const form = commentCard?.querySelector("[data-comment-edit-form]");

        if (body) {
            body.hidden = false;
        }

        if (form) {
            form.hidden = true;
        }
    };

    const openEditForm = (button) => {
        const commentCard = button.closest(".comment-card");
        const body = commentCard?.querySelector("[data-comment-body-copy]");
        const form = commentCard?.querySelector("[data-comment-edit-form]");

        if (!commentCard || !form) {
            return;
        }

        closeMenus();
        if (body) {
            body.hidden = true;
        }
        form.hidden = false;
        form.querySelector("textarea")?.focus();
    };

    const toggleCommentLike = async (button) => {
        const url = button.dataset.likeUrl;
        const commentCard = button.closest(".comment-card");
        const count = commentCard?.querySelector("[data-comment-like-count]");

        if (!url) {
            return;
        }

        button.disabled = true;
        try {
            const csrfToken =
                document.querySelector("[name=csrfmiddlewaretoken]")?.value ||
                decodeURIComponent(getPostDetailCookieValue("csrftoken"));

            const response = await fetch(url, {
                method: "POST",
                headers: {
                    "X-CSRFToken": csrfToken,
                    "X-Requested-With": "XMLHttpRequest",
                },
                credentials: "same-origin",
            });

            if (response.redirected) {
                window.location.href = response.url;
                return;
            }

            if (!response.ok) {
                console.error("comment like failed", response.status, await response.text());
                return;
            }

            const contentType = response.headers.get("content-type") || "";
            if (!contentType.includes("application/json")) {
                console.error("comment like returned non-JSON", await response.text());
                return;
            }

            const data = await response.json();
            const liked = Boolean(data.liked ?? data.active);
            const likeCount = data.like_count ?? data.likes_count;
            button.classList.toggle("is-active", liked);
            button.setAttribute("aria-pressed", String(liked));
            if (count && typeof likeCount === "number") {
                count.textContent = String(likeCount);
            }
        } finally {
            button.disabled = false;
        }
    };

    document.addEventListener("click", (event) => {
        const likeButton = event.target.closest("[data-comment-like-button]");
        if (likeButton) {
            event.preventDefault();
            toggleCommentLike(likeButton);
            return;
        }

        const replyButton = event.target.closest("[data-comment-reply-toggle]");
        if (replyButton) {
            event.preventDefault();
            const commentId = replyButton.dataset.commentId;
            const form = document.querySelector(`[data-comment-reply-form="${commentId}"]`);
            if (form) {
                form.hidden = !form.hidden;
                if (!form.hidden) {
                    form.querySelector("textarea")?.focus();
                }
            }
            closeMenus();
            return;
        }

        const menuButton = event.target.closest("[data-comment-menu-toggle]");
        if (menuButton) {
            event.preventDefault();
            event.stopPropagation();
            const commentId = menuButton.dataset.commentId;
            const menu = document.querySelector(`[data-comment-menu="${commentId}"]`);
            if (!menu) {
                return;
            }
            const shouldOpen = menu.hidden;
            closeMenus();
            menu.hidden = !shouldOpen;
            menuButton.setAttribute("aria-expanded", String(shouldOpen));
            return;
        }

        const editButton = event.target.closest("[data-comment-edit-trigger]");
        if (editButton) {
            event.preventDefault();
            openEditForm(editButton);
            return;
        }

        const editCancel = event.target.closest("[data-comment-edit-cancel]");
        if (editCancel) {
            event.preventDefault();
            closeEditForm(editCancel.closest(".comment-card"));
            return;
        }

        if (!event.target.closest(".comment-card__menu")) {
            closeMenus();
        }
    });

    document.addEventListener("submit", (event) => {
        if (event.target.matches("[data-comment-delete-form]")) {
            const confirmMessage = event.target.dataset.deleteConfirm || "Delete comment?";
            if (!window.confirm(confirmMessage)) {
                event.preventDefault();
            }
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closeMenus();
            document
                .querySelectorAll(".comment-card")
                .forEach((commentCard) => closeEditForm(commentCard));
        }
    });
}

function setupDetailBodyToggle() {
    const content = document.querySelector("[data-detail-body-content]");
    const toggle = document.querySelector("[data-detail-body-toggle]");

    if (!content || !toggle) {
        return;
    }

    const collapsedLabel = "View full article";
    const expandedLabel = "Show less";
    const hasOverflow = content.scrollHeight > content.clientHeight + 1;

    if (!hasOverflow) {
        content.classList.remove("is-collapsed");
        toggle.hidden = true;
        return;
    }

    toggle.hidden = false;
    toggle.textContent = collapsedLabel;

    toggle.addEventListener("click", () => {
        const shouldExpand = content.classList.contains("is-collapsed");
        content.classList.toggle("is-collapsed", !shouldExpand);
        toggle.textContent = shouldExpand ? expandedLabel : collapsedLabel;
    });
}

function setupDetailTagEditors() {
    document.querySelectorAll("[data-tag-editor]").forEach((editor) => {
        if (editor.dataset.ready === "true") {
            return;
        }

        editor.dataset.ready = "true";
        const input = editor.querySelector(".tag-input-chip-field");
        const chipList = editor.querySelector(".tag-chip-list");
        const hidden = editor.querySelector(".tag-hidden-field");
        const form = editor.closest("form");
        const maxTags = 5;

        if (!input || !chipList || !hidden) {
            return;
        }

        const normalizeTag = (value) => {
            const clean = value
                .trim()
                .replace(/,+$/g, "")
                .replace(/\s+/g, "")
                .replace(/^#+/, "");

            return clean ? `#${clean}` : "";
        };

        const syncTags = () => {
            const tags = [...chipList.querySelectorAll(".tag-chip")].map(
                (chip) => chip.dataset.value,
            );
            hidden.value = tags.join(" ");
        };

        const createChip = (value) => {
            const tag = normalizeTag(value);

            if (!tag) {
                return;
            }

            const duplicate = [...chipList.querySelectorAll(".tag-chip")].some(
                (chip) => chip.dataset.value.toLowerCase() === tag.toLowerCase(),
            );

            if (duplicate) {
                return;
            }

            if (chipList.querySelectorAll(".tag-chip").length >= maxTags) {
                showValidationToast(`Add no more than ${maxTags} hashtags.`);
                return;
            }

            const chip = document.createElement("button");
            chip.type = "button";
            chip.className = "tag-chip";
            chip.dataset.value = tag;
            chip.textContent = tag;

            chip.addEventListener("click", () => {
                chip.remove();
                syncTags();
                input.focus();
            });

            chipList.append(chip);
            syncTags();
        };

        const addTagsFromValue = (value) => {
            value
                .split(/[\s,]+/)
                .filter(Boolean)
                .forEach((tag) => createChip(tag));
            syncTags();
        };

        hidden.value
            .split(/\s+/)
            .filter(Boolean)
            .forEach((tag) => createChip(tag));
        syncTags();

        input.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === ",") {
                event.preventDefault();
                addTagsFromValue(input.value);
                input.value = "";
            }

            if (event.key === "Backspace" && !input.value) {
                const lastChip = chipList.querySelector(".tag-chip:last-child");
                if (lastChip) {
                    lastChip.remove();
                    syncTags();
                }
            }
        });

        input.addEventListener("paste", (event) => {
            const pastedText = event.clipboardData?.getData("text") || "";
            if (!/[\s,]/.test(pastedText)) {
                return;
            }
            event.preventDefault();
            addTagsFromValue(`${input.value} ${pastedText}`);
            input.value = "";
        });

        form?.addEventListener("submit", () => {
            if (input.value.trim()) {
                addTagsFromValue(input.value);
                input.value = "";
            }

            syncTags();
        });
    });
}

function setupDetailCarousel() {
    document.querySelectorAll("[data-detail-carousel]").forEach((carousel) => {
        const track = carousel.querySelector(".detail-carousel-track");
        const prev = carousel.querySelector("[data-carousel-prev]");
        const next = carousel.querySelector("[data-carousel-next]");
        const count = carousel.querySelector("[data-carousel-count]");
        const getSlides = () => Array.from(carousel.querySelectorAll("[data-carousel-slide]"));
        let slides = getSlides();
        let current = slides.findIndex((slide) => slide.classList.contains("is-active"));

        if (current < 0) {
            current = 0;
        }

        const updateControls = () => {
            slides = getSlides();
            const hasMultipleSlides = slides.length > 1;
            if (prev) {
                prev.hidden = !hasMultipleSlides;
            }
            if (next) {
                next.hidden = !hasMultipleSlides;
            }
            if (count) {
                count.hidden = !hasMultipleSlides;
                count.textContent = slides.length ? `${current + 1} / ${slides.length}` : "0 / 0";
            }
        };

        const showSlide = (index) => {
            slides = getSlides();
            if (!slides.length) {
                current = 0;
                updateControls();
                return;
            }
            current = (index + slides.length) % slides.length;
            slides.forEach((slide, slideIndex) => {
                slide.classList.toggle("is-active", slideIndex === current);
            });
            updateControls();
        };

        prev?.addEventListener("click", () => showSlide(current - 1));
        next?.addEventListener("click", () => showSlide(current + 1));
        carousel.detailCarousel = {
            appendSlide(src, options = {}) {
                if (!track) {
                    return null;
                }
                const slide = document.createElement("div");
                slide.className = "detail-carousel-slide";
                slide.dataset.carouselSlide = "";
                if (options.newFileIndex !== undefined) {
                    slide.dataset.newFileIndex = String(options.newFileIndex);
                }
                const image = document.createElement("img");
                image.className = "detail-cover";
                image.alt = options.alt || "New photo preview";
                image.src = src;
                image.setAttribute("role", "button");
                image.tabIndex = 0;
                image.dataset.lightboxMedia = "";
                image.dataset.mediaType = "image";
                image.dataset.mediaSrc = src;
                slide.appendChild(image);
                track.appendChild(slide);
                showSlide(getSlides().length - 1);
                return slide;
            },
            getActiveSlide() {
                slides = getSlides();
                return slides[current] || null;
            },
            removeActiveSlide() {
                slides = getSlides();
                const removed = slides[current] || null;
                if (!removed) {
                    return null;
                }
                const nextIndex = current < slides.length - 1 ? current : current - 1;
                removed.remove();
                showSlide(Math.max(nextIndex, 0));
                return removed;
            },
            getSlideCount() {
                return getSlides().length;
            },
            showSlide,
        };
        showSlide(current);
    });
}

function setupDetailImageEditValidation() {
    document.querySelectorAll("[data-detail-edit-form]").forEach((form) => {
        const newImagesInput = form.querySelector("[data-new-images-input]");
        const deletedImageIdsInput = form.querySelector("[data-deleted-image-ids]");
        const error = form.querySelector("[data-image-edit-error]");
        const carousel = document.querySelector("[data-detail-carousel]");
        const deleteButton = carousel?.querySelector("[data-carousel-delete]");
        const deletedImageIds = new Set();
        let pendingFiles = [];

        if (!newImagesInput || !deletedImageIdsInput || !carousel?.detailCarousel) {
            return;
        }

        const rebuildFileInput = () => {
            const transfer = new DataTransfer();
            pendingFiles.forEach((file) => transfer.items.add(file));
            newImagesInput.files = transfer.files;
        };

        const syncDeletedImageIds = () => {
            deletedImageIdsInput.value = Array.from(deletedImageIds).join(",");
        };

        const validate = () => {
            const isValid = carousel.detailCarousel.getSlideCount() > 0;

            if (error) {
                error.hidden = isValid;
            }

            return isValid;
        };

        if (deleteButton) {
            deleteButton.hidden = false;
        }

        newImagesInput.addEventListener("change", () => {
            const selectedFiles = Array.from(newImagesInput.files || []);
            selectedFiles.forEach((file) => {
                const fileIndex = pendingFiles.length;
                pendingFiles.push(file);
                const previewUrl = URL.createObjectURL(file);
                carousel.detailCarousel.appendSlide(previewUrl, {
                    newFileIndex: fileIndex,
                    alt: file.name || "New photo preview",
                });
            });
            rebuildFileInput();
            validate();
        });

        deleteButton?.addEventListener("click", () => {
            if (carousel.detailCarousel.getSlideCount() <= 1) {
                if (error) {
                    error.hidden = false;
                }
                showValidationToast("At least one image is required.");
                return;
            }

            const activeSlide = carousel.detailCarousel.getActiveSlide();
            if (!activeSlide) {
                return;
            }

            const existingImageId = activeSlide.dataset.existingImageId;
            const newFileIndex = activeSlide.dataset.newFileIndex;
            const image = activeSlide.querySelector("img");

            if (existingImageId) {
                deletedImageIds.add(existingImageId);
                syncDeletedImageIds();
            }

            if (newFileIndex !== undefined) {
                const indexToRemove = Number(newFileIndex);
                if (Number.isInteger(indexToRemove)) {
                    pendingFiles.splice(indexToRemove, 1);
                    rebuildFileInput();
                    carousel
                        .querySelectorAll("[data-carousel-slide][data-new-file-index]")
                        .forEach((slide) => {
                            const index = Number(slide.dataset.newFileIndex);
                            if (Number.isInteger(index) && index > indexToRemove) {
                                slide.dataset.newFileIndex = String(index - 1);
                            }
                        });
                }
                if (image?.src?.startsWith("blob:")) {
                    URL.revokeObjectURL(image.src);
                }
            }

            carousel.detailCarousel.removeActiveSlide();
            validate();
        });

        form.addEventListener("submit", (event) => {
            if (!validate()) {
                event.preventDefault();
                showValidationToast("At least one image is required.");
            }
        });
    });
}

function setupDetailCardMenus() {
    if (!document.body.classList.contains("post-detail-page")) {
        return;
    }

    const menuButtons = Array.from(
        document.querySelectorAll("[data-detail-menu-button]"),
    );
    const editButtons = Array.from(
        document.querySelectorAll("[data-detail-edit-target]"),
    );
    const deleteForms = Array.from(
        document.querySelectorAll("[data-confirm-delete]"),
    );

    const closeMenus = () => {
        menuButtons.forEach((button) => {
            button.setAttribute("aria-expanded", "false");
            const dropdown = button
                .closest(".detail-card-actions-menu")
                ?.querySelector("[data-detail-menu-dropdown]");
            if (dropdown) {
                dropdown.hidden = true;
            }
        });
    };

    const stopEditing = () => {
        document.body.classList.remove("is-editing-detail");
        document
            .querySelectorAll(".detail-article.is-editing")
            .forEach((container) => container.classList.remove("is-editing"));
        document
            .querySelectorAll("[data-detail-edit-form]")
            .forEach((form) => {
                form.hidden = true;
            });
    };

    const startEditing = (button) => {
        const target = button.dataset.detailEditTarget;
        const container = document.querySelector(".detail-article");
        const form = container?.querySelector(`[data-detail-edit-form="${target}"]`);
        const bodyContent = document.querySelector("[data-detail-body-content]");
        const bodyToggle = document.querySelector("[data-detail-body-toggle]");

        if (!container || !form) {
            return;
        }

        stopEditing();
        closeMenus();

        document.body.classList.add("is-editing-detail");
        container.classList.add("is-editing");
        form.hidden = false;

        if (target === "main" && bodyContent) {
            bodyContent.classList.remove("is-collapsed");
        }

        if (target === "main" && bodyToggle) {
            bodyToggle.hidden = true;
        }

        form.querySelector("input, textarea, select, button")?.focus();
    };

    menuButtons.forEach((button) => {
        button.addEventListener("click", (event) => {
            event.stopPropagation();
            const dropdown = button
                .closest(".detail-card-actions-menu")
                ?.querySelector("[data-detail-menu-dropdown]");

            if (!dropdown) {
                return;
            }

            const shouldOpen = dropdown.hidden;
            closeMenus();
            dropdown.hidden = !shouldOpen;
            button.setAttribute("aria-expanded", String(shouldOpen));
        });
    });

    editButtons.forEach((button) => {
        button.addEventListener("click", () => startEditing(button));
    });

    deleteForms.forEach((form) => {
        form.addEventListener("submit", (event) => {
            if (!window.confirm("Delete this post? This cannot be undone.")) {
                event.preventDefault();
            }
        });
    });

    document.addEventListener("click", closeMenus);
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closeMenus();
        }
    });
}

function initializePostDetail() {
    setupPostDetailActions();
    setupCommentHashFocus();
    setupCommentListToggle();
    setupCommentInteractions();
    setupDetailBodyToggle();
    setupDetailTagEditors();
    setupDetailCarousel();
    setupDetailImageEditValidation();
    setupDetailCardMenus();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializePostDetail, { once: true });
} else {
    initializePostDetail();
}

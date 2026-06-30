function setupFeedFilters() {
    const searchInput = document.getElementById("feed-search-input");
    const emptyState = document.querySelector(".feed-empty-state[hidden]");

    if (!searchInput) {
        return;
    }

    let currentFilter = { tags: [], query: "" };

    const applyFilters = ({ tags = [], query = "" } = {}) => {
        currentFilter = { tags, query };
        const normalizedQuery = query.trim().toLowerCase();
        const normalizedTags = tags.map((tag) => tag.toLowerCase());
        const posts = Array.from(document.querySelectorAll(".feed-post"));
        let visibleCount = 0;

        posts.forEach((post) => {
            const searchableText =
                post.dataset.search?.toLowerCase() ||
                post.querySelector(".feed-post-copy")?.textContent?.toLowerCase() ||
                post.textContent.toLowerCase();
            const matchesTag =
                !normalizedTags.length ||
                normalizedTags.every((tag) => searchableText.includes(tag));
            const matchesQuery =
                !normalizedQuery || searchableText.includes(normalizedQuery);
            const isVisible = matchesTag && matchesQuery;

            post.hidden = !isVisible;

            if (isVisible) {
                visibleCount += 1;
            }
        });

        if (emptyState) {
            emptyState.hidden = visibleCount > 0;
        }
    };

    window.socialManagerApplyFeedFilters = () => applyFilters(currentFilter);

    createHashtagInput(searchInput, {
        ariaLabel: "Filter by hashtag",
        initialValue: "",
        onChange: applyFilters,
        placeholder: searchInput.getAttribute("placeholder") || "Search by hashtag or keyword",
        syncSource: false,
    });
}

function setupFeedInfiniteScroll() {
    if (!document.body.classList.contains("feed-page")) {
        return;
    }

    const postStack = document.querySelector("[data-feed-posts]");
    const sentinel = document.querySelector("[data-feed-sentinel]");
    const loader = document.querySelector("[data-feed-loader]");
    const endMessage = document.querySelector("[data-feed-end]");

    if (!postStack || !sentinel || !loader || !("IntersectionObserver" in window)) {
        return;
    }

    let nextPage = Number(loader.dataset.nextPage || 0);
    let isLoading = false;
    let hasNext = nextPage > 0;

    const setLoading = (loading) => {
        isLoading = loading;
        loader.hidden = !loading;
    };

    const markDone = () => {
        hasNext = false;
        nextPage = 0;
        loader.dataset.nextPage = "";
        loader.hidden = true;
        if (endMessage) {
            endMessage.hidden = false;
        }
        observer.disconnect();
    };

    const loadNextPage = async () => {
        if (isLoading || !hasNext || !nextPage) {
            return;
        }

        setLoading(true);

        const url = new URL(window.location.href);
        url.searchParams.set("page", String(nextPage));

        try {
            const response = await fetch(url.toString(), {
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                    Accept: "application/json",
                },
                credentials: "same-origin",
            });

            if (!response.ok) {
                throw new Error("Unable to load more posts.");
            }

            const data = await response.json();
            const template = document.createElement("template");
            template.innerHTML = data.html || "";
            postStack.append(template.content);

            setupFeedEngagementActions();
            setupFeedPostCards();
            setupVideoWatchTracking();
            window.socialManagerApplyFeedFilters?.();

            if (data.has_next && data.next_page) {
                nextPage = Number(data.next_page);
                loader.dataset.nextPage = String(nextPage);
            } else {
                markDone();
            }
        } catch (error) {
            hasNext = false;
            if (endMessage) {
                endMessage.hidden = false;
                endMessage.textContent = "No more posts";
            }
            observer.disconnect();
        } finally {
            setLoading(false);
        }
    };

    const observer = new IntersectionObserver(
        (entries) => {
            if (entries.some((entry) => entry.isIntersecting)) {
                loadNextPage();
            }
        },
        {
            rootMargin: "600px 0px",
            threshold: 0,
        },
    );

    if (hasNext) {
        observer.observe(sentinel);
    } else if (endMessage) {
        endMessage.hidden = false;
    }
}

function setupFeedPostCards() {
    document.querySelectorAll(".feed-post[data-post-url]").forEach((card) => {
        card.addEventListener("click", (event) => {
            if (event.target.closest("a, button, input, textarea, select, video, [data-lightbox-media]")) {
                return;
            }

            window.location.href = card.dataset.postUrl;
        });
    });
}

function setupAnnouncementModal() {
    const modal = document.querySelector("[data-announcement-modal]");
    if (!modal) {
        return;
    }

    const titleTarget = modal.querySelector("[data-announcement-modal-title]");
    const contentTarget = modal.querySelector("[data-announcement-modal-content]");
    const editLink = modal.querySelector("[data-announcement-modal-edit]");
    const deleteLink = modal.querySelector("[data-announcement-modal-delete]");
    let previousFocus = null;

    const closeModal = () => {
        if (modal.hidden) {
            return;
        }
        modal.hidden = true;
        document.body.classList.remove("announcement-modal-open");
        previousFocus?.focus();
    };

    const openModal = (trigger) => {
        previousFocus = document.activeElement;
        titleTarget.textContent = trigger.dataset.announcementTitle || "";
        contentTarget.textContent = trigger.dataset.announcementContent || "";
        if (editLink && trigger.dataset.announcementEditUrl) {
            editLink.href = trigger.dataset.announcementEditUrl;
        }
        if (deleteLink && trigger.dataset.announcementDeleteUrl) {
            deleteLink.href = trigger.dataset.announcementDeleteUrl;
        }
        modal.hidden = false;
        document.body.classList.add("announcement-modal-open");
        modal.querySelector("[data-announcement-close]")?.focus();
    };

    document.addEventListener("click", (event) => {
        const trigger = event.target.closest(".announcement-item__link");
        if (trigger) {
            event.preventDefault();
            openModal(trigger);
            return;
        }

        if (event.target.closest("[data-announcement-close]")) {
            event.preventDefault();
            closeModal();
        }
    });

    document.addEventListener("keydown", (event) => {
        if (!modal.hidden && event.key === "Escape") {
            closeModal();
        }
    });
}

function initializeFeed() {
    setupAnnouncementModal();
    setupFeedFilters();
    setupFeedInfiniteScroll();
    setupFeedPostCards();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeFeed, { once: true });
} else {
    initializeFeed();
}

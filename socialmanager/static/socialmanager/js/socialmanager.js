const SIDEBAR_BREAKPOINT = 991.98;
let creanaServiceWorkerRegistrationPromise = null;
let deferredInstallPrompt = null;

function registerCreanaServiceWorker() {
    if (!("serviceWorker" in navigator)) {
        return Promise.reject(new Error("Service workers are not supported."));
    }
    const isLocalhost = ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
    if (!window.isSecureContext && !isLocalhost) {
        return Promise.reject(new Error("A secure context is required."));
    }
    if (!creanaServiceWorkerRegistrationPromise) {
        creanaServiceWorkerRegistrationPromise = navigator.serviceWorker.register(
            "/service-worker.js",
            { scope: "/", updateViaCache: "none" },
        );
    }
    return creanaServiceWorkerRegistrationPromise;
}

window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredInstallPrompt = event;
    window.dispatchEvent(new Event("creana:installavailable"));
});

window.addEventListener("appinstalled", () => {
    deferredInstallPrompt = null;
    window.dispatchEvent(new Event("creana:appinstalled"));
});

function getCookieValue(name) {
    return (
        document.cookie
            .split(";")
            .map((cookie) => cookie.trim())
            .find((cookie) => cookie.startsWith(`${name}=`))
            ?.slice(name.length + 1) || ""
    );
}

function escapeHTML(value) {
    const element = document.createElement("div");
    element.textContent = value || "";
    return element.innerHTML;
}

function closeResponsiveSidebar() {
    document.body.classList.remove("app-sidebar-open");
}

function openAiMembershipModal(message = "") {
    const modal = document.querySelector("[data-ai-members-modal]");
    if (!modal) {
        return false;
    }

    const labels = {
        message:
            message ||
            document.body.dataset.labelAiMembersUpgradePrompt ||
            "AI features are available for members only. Would you like to upgrade your plan?",
        notNow: document.body.dataset.labelAiMembersNotNow || "Not now",
        viewPlans: document.body.dataset.labelAiMembersViewPlans || "View plans",
    };
    const messageElement = modal.querySelector("[data-ai-members-modal-message]");
    const closeButton = modal.querySelector("[data-ai-members-modal-close]");
    const plansLink = modal.querySelector("[data-ai-members-modal-plans]");

    if (messageElement) {
        messageElement.textContent = labels.message;
    }
    if (closeButton) {
        closeButton.textContent = labels.notNow;
    }
    if (plansLink) {
        plansLink.textContent = labels.viewPlans;
        const plansUrl = document.body.dataset.subscriptionPlansUrl || plansLink.href;
        const url = new URL(plansUrl, window.location.origin);
        url.searchParams.set("next", `${window.location.pathname}${window.location.search}`);
        plansLink.href = url.toString();
    }

    modal.hidden = false;
    modal.classList.add("is-active");
    closeButton?.focus();
    return true;
}

function requireAiMembershipBeforeAction(callback = null, event = null) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }

    const isMember = document.body?.dataset?.aiMember === "true";

    if (!isMember) {
        if (typeof window.openAiMembershipModal === "function") {
            window.openAiMembershipModal();
        } else {
            alert("AI features are available for members only. Would you like to upgrade your plan?");
        }
        return false;
    }

    if (typeof callback === "function") {
        callback();
    }
    return true;
}

window.openAiMembershipModal = openAiMembershipModal;
window.showAiMembersOnlyModal = openAiMembershipModal;
window.requireAiMembershipBeforeAction = requireAiMembershipBeforeAction;

function setupAiMembersOnlyModal() {
    const modal = document.querySelector("[data-ai-members-modal]");
    if (!modal || modal.dataset.bound === "true") {
        return;
    }
    modal.dataset.bound = "true";

    const closeModal = () => {
        modal.classList.remove("is-active");
        modal.hidden = true;
    };

    modal.querySelectorAll("[data-ai-members-modal-close]").forEach((button) => {
        button.addEventListener("click", closeModal);
    });
    modal.addEventListener("click", (event) => {
        if (event.target === modal) {
            closeModal();
        }
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !modal.hidden) {
            closeModal();
        }
    });
}

function setupResponsiveSidebar() {
    const sidebar = document.querySelector(".app-shell .sidebar");
    const topbar = document.querySelector(".app-shell .topbar");

    if (!sidebar || !topbar || topbar.querySelector(".sidebar-toggle")) {
        return;
    }

    const brandIconSrc =
        sidebar.querySelector(".brand-icon")?.getAttribute("src") ||
        document.body.dataset.brandIconUrl ||
        "";
    const brandTextSrc =
        sidebar.querySelector(".brand-word")?.getAttribute("src") ||
        document.body.dataset.brandWordUrl ||
        "";
    const dashboardUrl =
        document.body.dataset.dashboardUrl || window.location.pathname;
    const notificationsUrl =
        document.body.dataset.notificationsUrl || window.location.pathname;
    const settingsUrl =
        document.body.dataset.settingsUrl || window.location.pathname;
    const unreadNotificationCount = Number(
        document.body.dataset.unreadNotificationCount || 0,
    );
    const labels = {
        openSidebar: document.body.dataset.labelOpenSidebar || "Open sidebar",
        closeSidebar: document.body.dataset.labelCloseSidebar || "Close sidebar",
        notifications: document.body.dataset.labelNotifications || "Notifications",
        settings: document.body.dataset.labelSettings || "Settings",
    };
    const unreadNotificationLabel =
        unreadNotificationCount > 99 ? "99+" : String(unreadNotificationCount);

    const toggleButton = document.createElement("button");
    toggleButton.type = "button";
    toggleButton.className = "icon-button sidebar-toggle";
    toggleButton.setAttribute("aria-expanded", "false");
    toggleButton.setAttribute("aria-label", labels.openSidebar);
    toggleButton.innerHTML =
        '<span class="material-symbols-outlined">menu</span>';

    const brandLink = document.createElement("a");
    brandLink.className = "topbar-brand";
    brandLink.href = dashboardUrl;
    brandLink.setAttribute("aria-label", "Creana");
    brandLink.innerHTML = `
        <img alt="Creana" class="brand-icon topbar-brand-icon" src="${escapeHTML(brandIconSrc)}" width="38" height="38">
        <span class="topbar-brand-title">
            <img alt="Creana" class="brand-word" src="${escapeHTML(brandTextSrc)}" width="128" height="28">
        </span>
    `;

    const notificationLink = document.createElement("a");
    notificationLink.className = "app-icon-button topbar-action-link topbar-notification-link";
    notificationLink.href = notificationsUrl;
    notificationLink.setAttribute("aria-label", labels.notifications);
    notificationLink.setAttribute("title", labels.notifications);
    notificationLink.innerHTML = `
        <span class="material-symbols-outlined" aria-hidden="true">notifications</span>
        ${
            unreadNotificationCount > 0
                ? `<span class="notification-badge">${unreadNotificationLabel}</span>`
                : ""
        }
    `;

    const settingsLink = document.createElement("a");
    settingsLink.className = "app-icon-button topbar-action-link topbar-settings-link";
    settingsLink.href = settingsUrl;
    settingsLink.setAttribute("aria-label", labels.settings);
    settingsLink.setAttribute("title", labels.settings);
    settingsLink.innerHTML =
        '<span class="material-symbols-outlined" aria-hidden="true">settings</span>';

    const backdrop = document.createElement("button");
    backdrop.type = "button";
    backdrop.className = "sidebar-drawer-backdrop";
    backdrop.setAttribute("aria-label", labels.closeSidebar);
    backdrop.setAttribute("aria-hidden", "true");

    const setOpen = (isOpen) => {
        document.body.classList.toggle("app-sidebar-open", isOpen);
        toggleButton.setAttribute("aria-expanded", String(isOpen));
        toggleButton.setAttribute(
            "aria-label",
            isOpen ? labels.closeSidebar : labels.openSidebar,
        );
    };

    toggleButton.addEventListener("click", () => {
        setOpen(!document.body.classList.contains("app-sidebar-open"));
    });

    backdrop.addEventListener("click", () => setOpen(false));

    sidebar.querySelectorAll("a").forEach((link) => {
        link.addEventListener("click", () => setOpen(false));
    });

    window.addEventListener("resize", () => {
        if (window.innerWidth > SIDEBAR_BREAKPOINT) {
            closeResponsiveSidebar();
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            setOpen(false);
        }
    });

    topbar.replaceChildren(toggleButton, brandLink, notificationLink, settingsLink);
    document.body.append(backdrop);
}

function setupAIInsightToggles() {
    const buttons = document.querySelectorAll("[data-ai-insight-url][data-ai-insight-target]");
    const labels = {
        generating: document.body.dataset.labelGeneratingInsight || "Generating insight...",
        noInsight: document.body.dataset.labelNoInsight || "No insight is available yet.",
        error: document.body.dataset.labelAiInsightError || "Unable to generate AI insight.",
    };
    if (!buttons.length) {
        return;
    }

    buttons.forEach((button) => {
        const targetId = button.dataset.aiInsightTarget;
        const panel = targetId ? document.getElementById(targetId) : null;
        const label = button.querySelector(".ai-insight-label") || button;
        const originalText = label.textContent.trim() || "AI Insight";

        if (!panel || button.dataset.aiInsightBound === "true") {
            return;
        }
        button.dataset.aiInsightBound = "true";

        button.addEventListener("click", async (event) => {
            if (!requireAiMembershipBeforeAction(null, event)) {
                return;
            }

            const isLoaded = button.dataset.aiInsightLoaded === "true";
            if (isLoaded) {
                panel.hidden = !panel.hidden;
                return;
            }

            button.disabled = true;
            panel.hidden = false;
            panel.innerHTML = `<p class="ai-insight-body">${escapeHTML(labels.generating)}</p>`;
            label.textContent = labels.generating;

            try {
                const response = await fetch(button.dataset.aiInsightUrl, {
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                    },
                });
                const data = await response.json();
                if (response.status === 403) {
                    openAiMembershipModal();
                    panel.hidden = true;
                    return;
                }
                if (!response.ok || !data.success) {
                    throw new Error(data.error || labels.error);
                }
                panel.innerHTML = data.insight_html || `<p class="ai-insight-body">${escapeHTML(labels.noInsight)}</p>`;
                button.dataset.aiInsightLoaded = "true";
            } catch (error) {
                panel.innerHTML = `<p class="ai-insight-body ai-insight-error">${escapeHTML(error.message || labels.error)}</p>`;
            } finally {
                button.disabled = false;
                label.textContent = originalText;
            }
        });
    });
}

function setupAiMembershipFormGuards() {
    document.addEventListener("click", (event) => {
        const trigger = event.target.closest(
            "button[name='generate_ai'], input[name='generate_ai'], [data-ai-requires-membership='true']",
        );
        if (
            trigger &&
            document.body?.dataset?.aiMember !== "true" &&
            !requireAiMembershipBeforeAction(null, event)
        ) {
            return;
        }
    }, true);

    document.addEventListener("submit", (event) => {
        const submitter = event.submitter;
        const requiresAiMembership =
            submitter?.name === "generate_ai" ||
            submitter?.dataset.aiRequiresMembership === "true" ||
            event.target?.dataset.aiRequiresMembership === "true";

        if (
            requiresAiMembership &&
            document.body?.dataset?.aiMember !== "true" &&
            !requireAiMembershipBeforeAction(null, event)
        ) {
            return;
        }
    });
}

function setupScrollTargets() {
    document.querySelectorAll("[data-scroll-target]").forEach((button) => {
        button.addEventListener("click", () => {
            const selector = button.getAttribute("data-scroll-target");
            const target = selector ? document.querySelector(selector) : null;

            if (target) {
                target.scrollIntoView({ behavior: "smooth", block: "start" });
            }
        });
    });
}

function setupFlashNotifications() {
    const messageStack = document.querySelector(".message-stack");
    if (!messageStack) {
        return;
    }

    const alerts = Array.from(messageStack.querySelectorAll(".alert"));
    if (!alerts.length) {
        return;
    }

    window.setTimeout(() => {
        alerts.forEach((alert) => {
            alert.classList.add("is-hiding");
        });

        window.setTimeout(() => {
            messageStack.remove();
        }, 320);
    }, 2000);
}

function showValidationToast(message) {
    const existingToast = document.querySelector(".validation-toast");
    existingToast?.remove();

    const toast = document.createElement("div");
    toast.className = "validation-toast";
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");

    const messageText = document.createElement("span");
    messageText.textContent = message;

    const closeButton = document.createElement("button");
    closeButton.className = "validation-toast-close";
    closeButton.type = "button";
    closeButton.setAttribute("aria-label", document.body.dataset.labelDismissMessage || "Dismiss message");
    closeButton.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">close</span>';

    toast.append(messageText, closeButton);
    document.body.append(toast);

    const dismiss = () => {
        toast.classList.add("is-hiding");
        window.setTimeout(() => toast.remove(), 260);
    };

    const timeoutId = window.setTimeout(dismiss, 3000);
    closeButton.addEventListener("click", () => {
        window.clearTimeout(timeoutId);
        dismiss();
    });
}

function createHashtagInput(sourceInput, options = {}) {
    if (!sourceInput || sourceInput.dataset.tagInputReady === "true") {
        return;
    }

    const {
        ariaLabel = document.body.dataset.labelAddHashtag || "Add hashtag",
        chipListLabel = document.body.dataset.labelHashtags || "Hashtags",
        initialValue = sourceInput.value,
        normalizeTag = null,
        onChange = null,
        parseInitial = null,
        placeholder =
            sourceInput.getAttribute("placeholder") ||
            document.body.dataset.labelHashtagPlaceholder ||
            "",
        serializeTags = null,
        splitPattern = /[\s,]+/,
        syncSource = true,
        maxTags = Number(sourceInput.dataset.maxTags || 0),
    } = options;

    sourceInput.dataset.tagInputReady = "true";
    sourceInput.classList.add("hashtag-source-input");

    const shell = document.createElement("div");
    shell.className = "hashtag-input-shell";

    const chipList = document.createElement("div");
    chipList.className = "hashtag-chip-list";
    chipList.setAttribute("aria-label", chipListLabel);

    const entryInput = document.createElement("input");
    entryInput.className = "hashtag-entry-input";
    entryInput.type = "text";
    entryInput.placeholder = placeholder;
    entryInput.autocomplete = "off";
    entryInput.setAttribute("aria-label", ariaLabel);

    shell.append(chipList, entryInput);
    sourceInput.insertAdjacentElement("afterend", shell);

    const tags = [];

    const defaultNormalizeTag = (value) => {
        const cleaned = value
            .trim()
            .replace(/,+$/g, "")
            .replace(/\s+/g, "")
            .replace(/^#+/, "");

        return cleaned ? `#${cleaned}` : "";
    };

    const normalizeValue = normalizeTag || defaultNormalizeTag;
    const serialize = serializeTags || ((tagValues) => tagValues.join(" "));

    const syncSourceInput = () => {
        if (syncSource) {
            sourceInput.value = serialize(tags);
        }
    };

    const notifyChange = () => {
        syncSourceInput();
        onChange?.({ tags: [...tags], query: entryInput.value });
    };

    const renderTags = () => {
        chipList.replaceChildren();

        tags.forEach((tag) => {
            const chip = document.createElement("span");
            chip.className = "hashtag-chip";

            const label = document.createElement("span");
            label.textContent = tag;

            const removeButton = document.createElement("button");
            removeButton.className = "hashtag-chip-remove";
            removeButton.type = "button";
            removeButton.setAttribute("aria-label", `Remove ${tag}`);
            removeButton.innerHTML =
                '<span class="material-symbols-outlined" aria-hidden="true">close</span>';

            removeButton.addEventListener("click", () => {
                const index = tags.indexOf(tag);
                if (index >= 0) {
                    tags.splice(index, 1);
                    renderTags();
                    notifyChange();
                    entryInput.focus();
                }
            });

            chip.append(label, removeButton);
            chipList.append(chip);
        });
    };

    const addTagsFromValue = (value) => {
        const candidates = value.split(splitPattern).map(normalizeValue).filter(Boolean);
        let didAdd = false;

        candidates.forEach((tag) => {
            const duplicate = tags.some(
                (existingTag) => existingTag.toLowerCase() === tag.toLowerCase(),
            );

            if (!duplicate) {
                if (maxTags && tags.length >= maxTags) {
                    showValidationToast(`Add no more than ${maxTags} hashtags.`);
                    return;
                }
                tags.push(tag);
                didAdd = true;
            }
        });

        if (didAdd) {
            renderTags();
        }

        entryInput.value = "";
        notifyChange();
    };

    if (parseInitial) {
        parseInitial(initialValue).map(normalizeValue).filter(Boolean).forEach((tag) => {
            const duplicate = tags.some(
                (existingTag) => existingTag.toLowerCase() === tag.toLowerCase(),
            );

            if (!duplicate && (!maxTags || tags.length < maxTags)) {
                tags.push(tag);
            }
        });
        renderTags();
        notifyChange();
    } else {
        addTagsFromValue(initialValue);
    }

    entryInput.addEventListener("keydown", (event) => {
        if (event.key === "Backspace" && !entryInput.value && tags.length) {
            tags.pop();
            renderTags();
            notifyChange();
            return;
        }

        if (event.key !== "Enter" && event.key !== ",") {
            return;
        }

        event.preventDefault();
        addTagsFromValue(entryInput.value);
        entryInput.focus();
    });

    entryInput.addEventListener("blur", () => {
        if (entryInput.value.trim()) {
            addTagsFromValue(entryInput.value);
        }
    });

    entryInput.addEventListener("input", notifyChange);

    entryInput.addEventListener("paste", (event) => {
        const pastedText = event.clipboardData?.getData("text") || "";

        if (!/[\s,]/.test(pastedText)) {
            return;
        }

        event.preventDefault();
        addTagsFromValue(`${entryInput.value} ${pastedText}`);
    });

    shell.addEventListener("click", () => {
        entryInput.focus();
    });

    sourceInput.addEventListener("create-post:replace-tags", (event) => {
        tags.splice(0, tags.length);
        entryInput.value = "";
        addTagsFromValue(event.detail?.value || sourceInput.value || "");
    });

    notifyChange();

    return {
        addTagsFromValue,
        entryInput,
        getTags: () => [...tags],
        syncSourceInput,
    };
}

function setupFeedEngagementActions() {
    const buttons = Array.from(document.querySelectorAll("[data-feed-engagement]"));

    if (!buttons.length) {
        return;
    }

    const getCookie = (name) =>
        document.cookie
            .split(";")
            .map((cookie) => cookie.trim())
            .find((cookie) => cookie.startsWith(`${name}=`))
            ?.slice(name.length + 1) || "";

    const updateFeedCounts = (card, data) => {
        const likeCounts = card.querySelectorAll("[data-feed-like-count]");
        const shareCounts = card.querySelectorAll("[data-feed-share-count]");

        if (typeof data.likes_count === "number") {
            likeCounts.forEach((count) => {
                count.textContent = String(data.likes_count);
            });
        }

        if (typeof data.shares_count === "number") {
            shareCounts.forEach((count) => {
                count.textContent = String(data.shares_count);
            });
        }
    };

    const focusCommentSection = () => {
        const commentSection = document.querySelector("#comments");
        const textarea = commentSection?.querySelector("textarea");

        if (!commentSection) {
            return false;
        }

        commentSection.scrollIntoView({
            behavior: "smooth",
            block: "start",
        });
        textarea?.focus();
        return true;
    };

    buttons.forEach((button) => {
        if (button.dataset.feedEngagementReady === "true") {
            return;
        }
        button.dataset.feedEngagementReady = "true";

        button.addEventListener("click", async () => {
            const url = button.dataset.engagementUrl;
            const kind = button.dataset.feedEngagement;
            const card = button.closest(".feed-post");

            if (!url || !kind || !card) {
                return;
            }

            button.disabled = true;

            try {
                const response = await fetch(url, {
                    method: "POST",
                    headers: {
                        "X-CSRFToken": decodeURIComponent(getCookie("csrftoken")),
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    credentials: "same-origin",
                });

                if (!response.ok) {
                    return;
                }

                const data = await response.json();
                const activeClass = kind === "share" ? "is-shared" : "is-liked";
                button.classList.toggle(activeClass, data.active);
                button.setAttribute("aria-pressed", String(data.active));
                updateFeedCounts(card, data);
            } finally {
                button.disabled = false;
            }
        });
    });

    document.querySelectorAll(".feed-comment-button").forEach((button) => {
        if (button.dataset.feedCommentReady === "true") {
            return;
        }
        button.dataset.feedCommentReady = "true";

        button.addEventListener("click", (event) => {
            const href = button.getAttribute("href") || "";
            const targetUrl = new URL(href, window.location.href);
            const isCurrentPage =
                targetUrl.origin === window.location.origin &&
                targetUrl.pathname === window.location.pathname &&
                targetUrl.search === window.location.search &&
                targetUrl.hash === "#comments";

            if (!isCurrentPage || !focusCommentSection()) {
                return;
            }

            event.preventDefault();
            window.history.replaceState(null, "", "#comments");
        });
    });
}

function setupMediaLightbox() {
    const lightbox = document.querySelector("[data-media-lightbox]");
    const frame = lightbox?.querySelector("[data-media-lightbox-frame]");
    const closeButton = lightbox?.querySelector("[data-media-lightbox-close]");
    let lastFocusedElement = null;

    if (!lightbox || !frame || !closeButton) {
        return;
    }

    const closeLightbox = () => {
        if (lightbox.hidden) {
            return;
        }

        lightbox.hidden = true;
        document.body.classList.remove("media-lightbox-open");
        frame.replaceChildren();

        if (lastFocusedElement && typeof lastFocusedElement.focus === "function") {
            lastFocusedElement.focus();
        }
        lastFocusedElement = null;
    };

    const openLightbox = (trigger) => {
        const src = trigger.dataset.mediaSrc || trigger.currentSrc || trigger.src;

        if (!src || trigger.tagName !== "IMG") {
            return;
        }

        lastFocusedElement = document.activeElement;
        frame.replaceChildren();

        const image = document.createElement("img");
        image.src = src;
        image.alt = trigger.getAttribute("alt") || "Expanded post media";
        frame.append(image);

        lightbox.hidden = false;
        document.body.classList.add("media-lightbox-open");
        closeButton.focus();
    };

    document.addEventListener("click", (event) => {
        if (event.target.closest("video")) {
            return;
        }

        const trigger = event.target.closest("[data-lightbox-media]");
        if (!trigger) {
            return;
        }

        if (trigger.tagName !== "IMG") {
            return;
        }

        event.preventDefault();
        event.stopPropagation();
        openLightbox(trigger);
    }, true);

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closeLightbox();
            return;
        }

        if ((event.key === "Enter" || event.key === " ") && event.target.matches("[data-lightbox-media]")) {
            if (event.target.tagName !== "IMG") {
                return;
            }
            event.preventDefault();
            openLightbox(event.target);
        }
    });

    closeButton.addEventListener("click", closeLightbox);
    lightbox.addEventListener("click", (event) => {
        if (event.target === lightbox || event.target === frame) {
            closeLightbox();
        }
    });
}

function setupVideoWatchTracking() {
    const videos = Array.from(
        document.querySelectorAll("video[data-track-watch-url]:not([data-watch-tracking-ready])"),
    );

    if (!videos.length) {
        return;
    }

    const csrfToken = decodeURIComponent(getCookieValue("csrftoken"));
    const getDebugPanel = (video) => {
        if (!video.dataset.postId) {
            return null;
        }

        return document.querySelector(
            `[data-video-watch-debug-for="${CSS.escape(video.dataset.postId)}"]`,
        );
    };
    const setDebugValue = (video, selector, value) => {
        const panel = getDebugPanel(video);
        const target = panel?.querySelector(selector);

        if (target) {
            target.textContent = String(value);
        }
    };
    const setDebugStatus = (video, status) => {
        setDebugValue(video, "[data-watch-debug-status]", status);
    };
    const buildPayload = (video) => {
        const duration = Number.isFinite(video.duration) ? video.duration : 0;
        const maxWatchedSeconds = Number(video.dataset.maxWatchedSeconds || 0);
        const watchedSeconds = Math.min(Math.max(maxWatchedSeconds, 0), Math.max(duration, maxWatchedSeconds, 0));
        const formData = new FormData();
        formData.append("watched_seconds", String(Math.round(watchedSeconds)));
        formData.append("video_duration", String(Math.round(duration)));
        formData.append("csrfmiddlewaretoken", csrfToken);
        return formData;
    };

    const hasProgress = (video) => {
        const maxWatchedSeconds = Number(video.dataset.maxWatchedSeconds || 0);
        const duration = Number.isFinite(video.duration) ? video.duration : 0;
        return maxWatchedSeconds > 0 && duration > 0;
    };

    const sendProgress = (video, { beacon = false } = {}) => {
        const url = video.dataset.trackWatchUrl;

        if (!url || !hasProgress(video)) {
            if (url) {
                setDebugStatus(video, "waiting for valid duration/progress");
            }
            return;
        }

        const now = Date.now();
        const lastSentAt = Number(video.dataset.watchLastSentAt || 0);
        const lastSentSecond = Number(video.dataset.watchLastSentSecond || -1);
        const currentSecond = Math.round(Number(video.dataset.maxWatchedSeconds || 0));

        if (!beacon && now - lastSentAt < 1200 && currentSecond === lastSentSecond) {
            return;
        }

        video.dataset.watchLastSentAt = String(now);
        video.dataset.watchLastSentSecond = String(currentSecond);

        const payload = buildPayload(video);
        setDebugStatus(video, beacon ? "sending with beacon" : "sending");

        if (beacon && navigator.sendBeacon) {
            const queued = navigator.sendBeacon(url, payload);
            setDebugStatus(video, queued ? "beacon queued" : "beacon failed");
            setDebugValue(video, "[data-watch-debug-status-code]", queued ? "beacon queued" : "beacon failed");
            return;
        }

        fetch(url, {
            method: "POST",
            body: payload,
            headers: {
                "X-CSRFToken": csrfToken,
                "X-Requested-With": "XMLHttpRequest",
            },
            credentials: "same-origin",
            keepalive: beacon,
            })
            .then((response) => {
                setDebugStatus(video, response.ok ? "saved" : "request failed");
                setDebugValue(video, "[data-watch-debug-status-code]", response.status);
                return response.text().then((body) => {
                    setDebugValue(video, "[data-watch-debug-response-body]", body || "(empty)");
                });
            })
            .catch((error) => {
                setDebugStatus(video, "request failed");
                setDebugValue(video, "[data-watch-debug-status-code]", "request failed");
                setDebugValue(video, "[data-watch-debug-response-body]", error?.message || "request failed");
            });
    };

    const updateMaxWatchedSeconds = (video) => {
        const currentTime = Number.isFinite(video.currentTime) ? video.currentTime : 0;
        const previousMax = Number(video.dataset.maxWatchedSeconds || 0);
        const nextMax = Math.max(previousMax, currentTime);
        video.dataset.maxWatchedSeconds = String(nextMax);
        if (nextMax > 0) {
            setDebugStatus(video, "tracking");
        }
        setDebugValue(video, "[data-watch-debug-current]", Math.round(currentTime));
        setDebugValue(video, "[data-watch-debug-max]", Math.round(nextMax));
    };

    videos.forEach((video) => {
        video.dataset.watchTrackingReady = "true";
        video.dataset.maxWatchedSeconds = video.dataset.maxWatchedSeconds || "0";
        setDebugStatus(video, "initialized");
        setDebugValue(video, "[data-watch-debug-url]", video.dataset.trackWatchUrl || "missing");
        video.addEventListener("loadedmetadata", () => {
            setDebugStatus(video, Number.isFinite(video.duration) ? "metadata loaded" : "duration unavailable");
            updateMaxWatchedSeconds(video);
            sendProgress(video);
        });
        video.addEventListener("play", () => updateMaxWatchedSeconds(video));
        video.addEventListener("timeupdate", () => {
            updateMaxWatchedSeconds(video);
            const now = Date.now();
            const lastPeriodicSentAt = Number(video.dataset.watchLastPeriodicSentAt || 0);
            if (now - lastPeriodicSentAt >= 5000) {
                video.dataset.watchLastPeriodicSentAt = String(now);
                sendProgress(video);
            }
        });
        video.addEventListener("seeking", () => {
            updateMaxWatchedSeconds(video);
            sendProgress(video);
        });
        video.addEventListener("pause", () => {
            updateMaxWatchedSeconds(video);
            sendProgress(video);
        });
        video.addEventListener("ended", () => {
            updateMaxWatchedSeconds(video);
            sendProgress(video);
        });
    });

    if (!setupVideoWatchTracking.pageExitListenersReady) {
        setupVideoWatchTracking.pageExitListenersReady = true;
        const sendAllProgress = () => {
            document
                .querySelectorAll("video[data-track-watch-url]")
                .forEach((video) => sendProgress(video, { beacon: true }));
        };

        document.addEventListener("visibilitychange", () => {
            if (document.visibilityState === "hidden") {
                sendAllProgress();
            }
        });

        window.addEventListener("pagehide", sendAllProgress);
    }
}

function setupSettingsPage() {
    const settingsShell = document.querySelector("[data-settings-update-url]");
    const autosaveFields = document.querySelectorAll("[data-settings-field]");

    autosaveFields.forEach((field) => {
        if (field.dataset.settingsField === "enable_push_notifications") {
            return;
        }
        field.addEventListener("change", async () => {
            if (!settingsShell) {
                return;
            }

            const fieldName = field.dataset.settingsField;
            const value = field.type === "checkbox" ? String(field.checked) : field.value;
            const body = new URLSearchParams();
            body.set("field", fieldName);
            body.set("value", value);

            try {
                const response = await fetch(settingsShell.dataset.settingsUpdateUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-CSRFToken": getCookieValue("csrftoken"),
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    body,
                });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    throw new Error(data.error || "Settings update failed.");
                }
                if (fieldName === "language") {
                    window.location.reload();
                }
            } catch (error) {
                console.warn(error.message || "Settings update failed.");
            }
        });
    });

    const pushToggle = document.querySelector('[data-settings-field="enable_push_notifications"]');
    const pushSupported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
    const updatePushPreference = async (enabled) => {
        const body = new URLSearchParams({ field: "enable_push_notifications", value: String(enabled) });
        const response = await fetch(settingsShell.dataset.settingsUpdateUrl, {
            method: "POST",
            headers: {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-CSRFToken": getCookieValue("csrftoken"),
                "X-Requested-With": "XMLHttpRequest",
            },
            credentials: "same-origin",
            body,
        });
        if (!response.ok) throw new Error("Could not update push preference.");
    };
    const base64ToUint8Array = (value) => {
        const padding = "=".repeat((4 - value.length % 4) % 4);
        const decoded = atob((value + padding).replace(/-/g, "+").replace(/_/g, "/"));
        return Uint8Array.from(decoded, (character) => character.charCodeAt(0));
    };

    if (pushToggle && settingsShell) {
        if (!pushSupported) {
            pushToggle.checked = false;
            pushToggle.disabled = true;
        } else {
            registerCreanaServiceWorker()
                .catch(() => {
                    pushToggle.checked = false;
                    pushToggle.disabled = true;
                });

            pushToggle.addEventListener("change", async () => {
                pushToggle.disabled = true;
                try {
                    const registration = await navigator.serviceWorker.ready;
                    if (pushToggle.checked) {
                        const permission = await Notification.requestPermission();
                        if (permission !== "granted") throw new Error("Notification permission was not granted.");
                        const keyResponse = await fetch(settingsShell.dataset.pushKeyUrl, { credentials: "same-origin" });
                        const keyData = await keyResponse.json();
                        if (!keyResponse.ok) throw new Error(keyData.error || "Push is not configured.");
                        let subscription = await registration.pushManager.getSubscription();
                        if (!subscription) {
                            subscription = await registration.pushManager.subscribe({
                                userVisibleOnly: true,
                                applicationServerKey: base64ToUint8Array(keyData.public_key),
                            });
                        }
                        const response = await fetch(settingsShell.dataset.pushSubscriptionUrl, {
                            method: "POST",
                            headers: { "Content-Type": "application/json", "X-CSRFToken": getCookieValue("csrftoken") },
                            credentials: "same-origin",
                            body: JSON.stringify(subscription.toJSON()),
                        });
                        if (!response.ok) throw new Error("Could not save this device.");
                        await updatePushPreference(true);
                    } else {
                        const subscription = await registration.pushManager.getSubscription();
                        if (subscription) {
                            await fetch(settingsShell.dataset.pushSubscriptionUrl, {
                                method: "DELETE",
                                headers: { "Content-Type": "application/json", "X-CSRFToken": getCookieValue("csrftoken") },
                                credentials: "same-origin",
                                body: JSON.stringify({ endpoint: subscription.endpoint }),
                            });
                            await subscription.unsubscribe();
                        }
                        await updatePushPreference(false);
                    }
                } catch (error) {
                    pushToggle.checked = false;
                    updatePushPreference(false).catch(() => {});
                    console.warn(error.message);
                } finally {
                    pushToggle.disabled = false;
                }
            });
        }
    }

    const modal = document.querySelector("[data-delete-account-modal]");
    const openButton = document.querySelector("[data-delete-account-open]");
    const closeButtons = document.querySelectorAll("[data-delete-account-close]");
    const confirmationInput = document.querySelector("[data-delete-confirmation]");
    const submitButton = document.querySelector("[data-delete-submit]");

    if (!modal || !openButton) {
        return;
    }

    const setModalOpen = (isOpen) => {
        modal.hidden = !isOpen;
        document.body.classList.toggle("settings-modal-open", isOpen);
        if (isOpen) {
            modal.querySelector("input")?.focus();
        }
    };

    openButton.addEventListener("click", () => setModalOpen(true));
    closeButtons.forEach((button) => {
        button.addEventListener("click", () => setModalOpen(false));
    });
    modal.addEventListener("click", (event) => {
        if (event.target === modal) {
            setModalOpen(false);
        }
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !modal.hidden) {
            setModalOpen(false);
        }
    });
    confirmationInput?.addEventListener("input", () => {
        submitButton.disabled = confirmationInput.value !== "DELETE";
    });
}

function setupInstallApp() {
    const component = document.querySelector("[data-install-app]");
    const installButton = component?.querySelector("[data-install-app-button]");
    const modal = document.querySelector("[data-install-instructions-modal]");
    const closeButtons = modal?.querySelectorAll("[data-install-instructions-close]") || [];
    const safariInstructions = modal?.querySelector("[data-install-ios-safari]");
    const otherBrowserInstructions = modal?.querySelector("[data-install-ios-other]");
    if (!component || !installButton) return;

    const userAgent = navigator.userAgent || "";
    const isIOS = /iPad|iPhone|iPod/i.test(userAgent)
        || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    const isIOSAlternativeBrowser = /CriOS|EdgiOS|FxiOS|OPiOS/i.test(userAgent);
    const isStandalone = () => window.matchMedia("(display-mode: standalone)").matches
        || navigator.standalone === true;

    const refreshVisibility = () => {
        component.hidden = isStandalone() || (!isIOS && !deferredInstallPrompt);
    };

    let previouslyFocused = null;
    const setModalOpen = (isOpen) => {
        if (!modal) return;
        modal.hidden = !isOpen;
        document.body.classList.toggle("settings-modal-open", isOpen);
        if (isOpen) {
            previouslyFocused = document.activeElement;
            safariInstructions.hidden = isIOSAlternativeBrowser;
            otherBrowserInstructions.hidden = !isIOSAlternativeBrowser;
            modal.querySelector("[data-install-instructions-close]")?.focus();
        } else if (previouslyFocused instanceof HTMLElement) {
            previouslyFocused.focus();
        }
    };

    installButton.addEventListener("click", async () => {
        if (isIOS) {
            setModalOpen(true);
            return;
        }
        if (!deferredInstallPrompt) return;
        const promptEvent = deferredInstallPrompt;
        deferredInstallPrompt = null;
        await promptEvent.prompt();
        await promptEvent.userChoice;
        refreshVisibility();
    });

    closeButtons.forEach((button) => button.addEventListener("click", () => setModalOpen(false)));
    modal?.addEventListener("click", (event) => {
        if (event.target === modal) setModalOpen(false);
    });
    modal?.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            event.preventDefault();
            setModalOpen(false);
            return;
        }
        if (event.key !== "Tab") return;
        const focusable = [...modal.querySelectorAll("button:not([disabled]), [href], input, select, textarea")]
            .filter((element) => !element.hidden);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });

    window.addEventListener("creana:installavailable", refreshVisibility);
    window.addEventListener("creana:appinstalled", refreshVisibility);
    window.matchMedia("(display-mode: standalone)").addEventListener?.("change", refreshVisibility);
    refreshVisibility();
}

function initializeCreana() {
    registerCreanaServiceWorker().catch(() => {});
    setupAiMembersOnlyModal();
    setupAiMembershipFormGuards();
    setupAIInsightToggles();
    setupResponsiveSidebar();
    setupScrollTargets();
    setupFlashNotifications();
    setupFeedEngagementActions();
    setupMediaLightbox();
    setupVideoWatchTracking();
    setupInstallApp();
    setupSettingsPage();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeCreana, { once: true });
} else {
    initializeCreana();
}

function setupCampaignPlatformInput() {
    if (!document.body.classList.contains("campaign-form-page")) {
        return;
    }

    const sourceInput = document.getElementById("id_platform_focus");
    const form = sourceInput?.closest("form");
    const root = document.querySelector("[data-platform-multiselect]");
    const toggle = root?.querySelector("[data-platform-toggle]");
    const menu = root?.querySelector("[data-platform-menu]");
    const selectedInline = root?.querySelector("[data-platform-selected-inline]");
    const placeholder = root?.querySelector("[data-platform-placeholder]");
    const optionButtons = [...(root?.querySelectorAll("[data-platform-option]") || [])];

    if (!sourceInput || !form || !root || !toggle || !menu || !selectedInline) {
        return;
    }

    const optionLabels = optionButtons.map((button) => button.dataset.platformOption);
    const optionLookup = new Map(
        optionLabels.map((label) => [
            label.toLowerCase().replace(/\s+/g, "").replace(/\//g, ""),
            label,
        ]),
    );
    optionLookup.set("twitter", "X / Twitter");
    optionLookup.set("x", "X / Twitter");

    const selected = [];

    const normalizePlatform = (value) => {
        const cleaned = String(value || "").trim().replace(/^['"\[]+|['"\]]+$/g, "");
        return optionLookup.get(cleaned.toLowerCase().replace(/\s+/g, "").replace(/\//g, "")) || "";
    };

    const parseInitialPlatforms = (value) => {
        if (!value) {
            return [];
        }

        try {
            const parsed = JSON.parse(value);
            return Array.isArray(parsed) ? parsed : [];
        } catch (error) {
            return String(value)
                .replace(/^\[|\]$/g, "")
                .split(",");
        }
    };

    const syncSourceInput = () => {
        sourceInput.value = JSON.stringify(selected);
    };

    const renderSelected = () => {
        selectedInline.replaceChildren();

        if (!selected.length) {
            const emptyPlaceholder = document.createElement("span");
            emptyPlaceholder.className = "platform-placeholder";
            emptyPlaceholder.dataset.platformPlaceholder = "";
            emptyPlaceholder.textContent = "Select platforms";
            selectedInline.append(emptyPlaceholder);
        } else {
            selected.forEach((platform) => {
                const chip = document.createElement("span");
                chip.className = "hashtag-chip";

                const label = document.createElement("span");
                label.textContent = platform;

                const removeButton = document.createElement("button");
                removeButton.className = "hashtag-chip-remove";
                removeButton.type = "button";
                removeButton.setAttribute("aria-label", `Remove ${platform}`);
                removeButton.innerHTML =
                    '<span class="material-symbols-outlined" aria-hidden="true">close</span>';

                removeButton.addEventListener("click", (event) => {
                    event.stopPropagation();
                    const index = selected.indexOf(platform);
                    if (index >= 0) {
                        selected.splice(index, 1);
                        renderSelected();
                    }
                });

                chip.append(label, removeButton);
                selectedInline.append(chip);
            });
        }

        optionButtons.forEach((button) => {
            const platform = button.dataset.platformOption;
            const isSelected = selected.includes(platform);
            button.disabled = isSelected;
            button.classList.toggle("is-selected", isSelected);
        });

        syncSourceInput();
    };

    const addPlatform = (value) => {
        const platform = normalizePlatform(value);
        if (!platform || selected.includes(platform)) {
            return;
        }

        selected.push(platform);
        renderSelected();
    };

    parseInitialPlatforms(sourceInput.value).forEach(addPlatform);
    renderSelected();

    const closeMenu = () => {
        menu.hidden = true;
        toggle.setAttribute("aria-expanded", "false");
    };

    const openMenu = () => {
        menu.hidden = false;
        toggle.setAttribute("aria-expanded", "true");
    };

    const toggleMenu = () => {
        if (menu.hidden) {
            openMenu();
        } else {
            closeMenu();
        }
    };

    toggle.addEventListener("click", (event) => {
        event.stopPropagation();
        toggleMenu();
    });

    optionButtons.forEach((button) => {
        button.addEventListener("click", (event) => {
            event.stopPropagation();
            addPlatform(button.dataset.platformOption);
            openMenu();
        });
    });

    document.addEventListener("click", (event) => {
        if (!root.contains(event.target)) {
            closeMenu();
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closeMenu();
        }
    });

    form.addEventListener("submit", () => {
        syncSourceInput();
    });
}

function setupCampaignPostInput() {
    if (!document.body.classList.contains("campaign-form-page")) {
        return;
    }

    const sourceInput = document.getElementById("id_campaign_posts");
    const form = sourceInput?.closest("form");
    const root = document.querySelector("[data-campaign-posts-multiselect]");
    const toggle = root?.querySelector("[data-campaign-posts-toggle]");
    const menu = root?.querySelector("[data-campaign-posts-menu]");
    const selectedInline = root?.querySelector("[data-campaign-posts-selected-inline]");
    const optionButtons = [...(root?.querySelectorAll("[data-campaign-posts-option]") || [])];

    if (!sourceInput || !form || !root || !toggle || !menu || !selectedInline) {
        return;
    }

    const postsById = new Map(
        optionButtons
            .filter((button) => button.dataset.campaignPostsOption)
            .map((button) => [
                String(button.dataset.campaignPostsOption),
                button.dataset.campaignPostsTitle || button.textContent.trim(),
            ]),
    );
    const selected = [];

    const parseInitialPosts = (value) => {
        if (!value) {
            return [];
        }

        try {
            const parsed = JSON.parse(value);
            return Array.isArray(parsed) ? parsed : [];
        } catch (error) {
            return String(value)
                .replace(/^\[|\]$/g, "")
                .split(",");
        }
    };

    const syncSourceInput = () => {
        sourceInput.value = JSON.stringify(selected);
    };

    const renderSelected = () => {
        selectedInline.replaceChildren();

        if (!selected.length) {
            const emptyPlaceholder = document.createElement("span");
            emptyPlaceholder.className = "post-placeholder";
            emptyPlaceholder.dataset.postPlaceholder = "";
            emptyPlaceholder.textContent = "Select posts";
            selectedInline.append(emptyPlaceholder);
        } else {
            selected.forEach((postId) => {
                const title = postsById.get(postId) || "Untitled post";
                const chip = document.createElement("span");
                chip.className = "hashtag-chip";

                const label = document.createElement("span");
                label.textContent = title;

                const removeButton = document.createElement("button");
                removeButton.className = "hashtag-chip-remove";
                removeButton.type = "button";
                removeButton.setAttribute("aria-label", `Remove ${title}`);
                removeButton.innerHTML =
                    '<span class="material-symbols-outlined" aria-hidden="true">close</span>';

                removeButton.addEventListener("click", (event) => {
                    event.stopPropagation();
                    const index = selected.indexOf(postId);
                    if (index >= 0) {
                        selected.splice(index, 1);
                        renderSelected();
                    }
                });

                chip.append(label, removeButton);
                selectedInline.append(chip);
            });
        }

        optionButtons.forEach((button) => {
            const postId = String(button.dataset.campaignPostsOption || "");
            const isSelected = selected.includes(postId);
            if (postId) {
                button.disabled = isSelected;
                button.classList.toggle("is-selected", isSelected);
            }
        });

        syncSourceInput();
    };

    const addPost = (value) => {
        const postId = String(value || "").trim();
        if (!postsById.has(postId) || selected.includes(postId)) {
            return;
        }

        selected.push(postId);
        renderSelected();
    };

    parseInitialPosts(sourceInput.value).forEach(addPost);
    renderSelected();

    const closeMenu = () => {
        menu.hidden = true;
        toggle.setAttribute("aria-expanded", "false");
    };

    const openMenu = () => {
        menu.hidden = false;
        toggle.setAttribute("aria-expanded", "true");
    };

    toggle.addEventListener("click", (event) => {
        event.stopPropagation();
        if (menu.hidden) {
            openMenu();
        } else {
            closeMenu();
        }
    });

    optionButtons.forEach((button) => {
        button.addEventListener("click", (event) => {
            event.stopPropagation();
            addPost(button.dataset.campaignPostsOption);
            openMenu();
        });
    });

    document.addEventListener("click", (event) => {
        if (!root.contains(event.target)) {
            closeMenu();
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closeMenu();
        }
    });

    form.addEventListener("submit", () => {
        syncSourceInput();
    });
}

function setupCampaignStrategyModal() {
    if (!document.body.classList.contains("campaign-list-page")) {
        return;
    }

    const modal = document.querySelector("[data-strategy-modal]");
    const closeButton = modal?.querySelector("[data-strategy-modal-close]");
    const titleNode = modal?.querySelector("[data-strategy-modal-title]");
    const copyNode = modal?.querySelector("[data-strategy-modal-copy]");
    const frame = modal?.querySelector("[data-strategy-modal-frame]");
    const strategyCopies = document.querySelectorAll("[data-campaign-strategy-copy]");
    let lastFocusedElement = null;

    if (!modal || !closeButton || !titleNode || !copyNode || !frame) {
        return;
    }

    strategyCopies.forEach((copy) => {
        const trigger = copy.parentElement?.querySelector("[data-strategy-trigger]");
        if (!trigger) {
            return;
        }

        if (copy.scrollHeight > copy.clientHeight + 1) {
            trigger.hidden = false;
        }
    });

    const closeModal = () => {
        if (modal.hidden) {
            return;
        }

        modal.hidden = true;
        document.body.classList.remove("media-lightbox-open");

        if (lastFocusedElement && typeof lastFocusedElement.focus === "function") {
            lastFocusedElement.focus();
        }
        lastFocusedElement = null;
    };

    const openModal = (trigger) => {
        lastFocusedElement = document.activeElement;
        titleNode.textContent =
            trigger.dataset.strategyTitle ||
            modal.dataset.strategyTitleLabel ||
            "";
        copyNode.textContent =
            trigger.dataset.strategyCopy ||
            modal.dataset.emptyObjectiveLabel ||
            "";
        modal.hidden = false;
        document.body.classList.add("media-lightbox-open");
        closeButton.focus();
    };

    document.addEventListener("click", (event) => {
        const trigger = event.target.closest("[data-strategy-trigger]");
        if (!trigger) {
            return;
        }

        event.preventDefault();
        openModal(trigger);
    });

    closeButton.addEventListener("click", closeModal);
    modal.addEventListener("click", (event) => {
        if (event.target === modal || event.target === frame) {
            closeModal();
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closeModal();
        }
    });
}

function setupCampaignCardMenus() {
    if (!document.body.classList.contains("campaign-page")) {
        return;
    }

    const menuButtons = document.querySelectorAll("[data-campaign-menu-button]");

    if (!menuButtons.length) {
        return;
    }

    const closeMenus = () => {
        menuButtons.forEach((button) => {
            const dropdown = button
                .closest(".campaign-card-menu")
                ?.querySelector("[data-campaign-menu-dropdown]");
            if (dropdown) {
                dropdown.hidden = true;
            }
            button.setAttribute("aria-expanded", "false");
        });
    };

    menuButtons.forEach((button) => {
        button.addEventListener("click", (event) => {
            event.stopPropagation();
            const dropdown = button
                .closest(".campaign-card-menu")
                ?.querySelector("[data-campaign-menu-dropdown]");
            if (!dropdown) {
                return;
            }

            const shouldOpen = dropdown.hidden;
            closeMenus();
            dropdown.hidden = !shouldOpen;
            button.setAttribute("aria-expanded", String(shouldOpen));
        });
    });

    document.addEventListener("click", closeMenus);
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closeMenus();
        }
    });
}

function initializeCampaign() {
    setupCampaignPlatformInput();
    setupCampaignPostInput();
    setupCampaignStrategyModal();
    setupCampaignCardMenus();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeCampaign, { once: true });
} else {
    initializeCampaign();
}

function setupProfilePostFilters() {
    const postTabs = Array.from(document.querySelectorAll("[data-post-tab]"));
    const panels = Array.from(document.querySelectorAll("[data-post-panel]"));
    const statusTabsRow = document.querySelector(".profile-status-tabs");

    if (!postTabs.length || !panels.length) {
        return;
    }

    const showPanel = (target) => {
        const activeTarget = panels.some((panel) => panel.dataset.postPanel === target)
            ? target
            : "all";

        if (statusTabsRow) {
            statusTabsRow.hidden = activeTarget === "shared";
        }

        postTabs.forEach((button) => {
            const isActive = button.dataset.postTab === activeTarget;
            button.classList.toggle("active", isActive);
            button.classList.toggle("is-active", isActive);
            button.setAttribute("aria-selected", isActive ? "true" : "false");
        });

        panels.forEach((panel) => {
            const isActive = panel.dataset.postPanel === activeTarget;
            panel.hidden = !isActive;
            panel.classList.toggle("is-active", isActive);
        });
    };

    postTabs.forEach((button) => {
        button.addEventListener("click", () => {
            showPanel(button.dataset.postTab || "all");
        });
    });

    showPanel("all");
}

function setupProfileInlineEditor() {
    if (!document.body.classList.contains("profile-page")) {
        return;
    }

    const editButton = document.getElementById("profile-edit-toggle");
    const profileForm = document.getElementById("profile-inline-form");
    const usernameCopy = document.getElementById("profile-username-copy");
    const usernameInput = document.getElementById("profile-username-input");
    const bioCopy = document.getElementById("profile-bio-copy");
    const bioInput = document.getElementById("profile-bio-input");
    const linksInput = document.getElementById("profile-links-input");
    const linkGrid = document.getElementById("profile-link-chip-grid");
    const linkEntry = document.getElementById("profile-link-entry");
    const bioHelper = document.getElementById("profile-bio-helper");
    const linksHelper = document.getElementById("profile-links-helper");
    const emailInput = document.getElementById("profile-email-input");
    const avatarInput = document.getElementById("profile-avatar-input");
    const avatarPreview = document.getElementById("profile-avatar-preview");
    const avatarFallback = document.getElementById("profile-avatar-preview-fallback");
    const avatarUpload = document.getElementById("profile-avatar-upload");

    if (
        !editButton ||
        !usernameCopy ||
        !usernameInput ||
        !bioCopy ||
        !bioInput ||
        !linksInput ||
        !linkGrid ||
        !linkEntry
    ) {
        return;
    }

    let isEditing = false;
    let editingChip = null;
    let isEditingExistingChip = false;
    const usernameMaxLength = Number(usernameCopy.dataset.maxLength || usernameInput.getAttribute("maxlength") || 20);
    const bioMaxLength = Number(bioCopy.dataset.maxLength || bioInput.getAttribute("maxlength") || 250);
    const maxProfileLinks = 5;
    const bioPlaceholderText = bioCopy.dataset.placeholder || "Please insert your bio here (up to 250 characters)";
    const linkPlaceholderText = "Please add your link here";

    const normalizeLineEndings = (value) => (value || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");

    const getChipValue = (chip) => (chip.dataset.value || chip.textContent || "").trim();

    const getChips = () =>
        Array.from(linkGrid.querySelectorAll(".profile-link-chip")).map(getChipValue);

    const getContactLabel = (value) => {
        const rawValue = value.trim();

        if (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(rawValue)) {
            return rawValue.split("@")[0] || rawValue;
        }

        if (/^\+?[\d\s().-]{7,}$/.test(rawValue)) {
            return rawValue;
        }

        if (!rawValue.includes(".") && !rawValue.includes("/")) {
            return rawValue;
        }

        let url;
        try {
            url = new URL(/^https?:\/\//i.test(rawValue) ? rawValue : `https://${rawValue}`);
        } catch (error) {
            return rawValue;
        }

        const domain = url.hostname.toLowerCase().replace(/^www\./, "");
        const platformLabels = [
            ["instagram.com", "Instagram"],
            ["tiktok.com", "TikTok"],
            ["youtube.com", "YouTube"],
            ["youtu.be", "YouTube"],
            ["linkedin.com", "LinkedIn"],
            ["github.com", "GitHub"],
        ];
        const platform = platformLabels.find(([host]) => domain === host || domain.endsWith(`.${host}`));

        return platform ? platform[1] : domain;
    };

    const stripBioPlaceholder = (value) =>
        normalizeLineEndings(value).replaceAll(bioPlaceholderText, "");
    const cleanBioValue = (value) => stripBioPlaceholder(value).trim();

    const getBioText = () => cleanBioValue(bioCopy.textContent);

    const setBioEditorValue = (value) => {
        const cleanedValue = cleanBioValue(value);
        bioCopy.textContent = cleanedValue || bioPlaceholderText;
        bioCopy.classList.toggle("is-placeholder", !cleanedValue);
        bioInput.value = cleanedValue;
    };

    const scrubBioEditor = () => {
        const rawValue = bioCopy.textContent || "";
        const scrubbedValue = stripBioPlaceholder(rawValue);
        if (rawValue !== scrubbedValue) {
            bioCopy.textContent = scrubbedValue;
        }
        bioCopy.classList.toggle("is-placeholder", !cleanBioValue(scrubbedValue) && !isEditing);
        if (!isEditing && !cleanBioValue(scrubbedValue)) {
            bioCopy.textContent = bioPlaceholderText;
        }
    };

    const clearBioPlaceholderForEditing = () => {
        if (bioCopy.classList.contains("is-placeholder") || getBioText() === "") {
            bioCopy.textContent = "";
            bioCopy.classList.remove("is-placeholder");
        }
    };

    const getBioValue = () => getBioText();

    const getCounterStatus = (count, maxLength) => {
        if (count >= Math.ceil(maxLength * 0.96)) {
            return "danger";
        }
        if (count >= Math.ceil(maxLength * 0.88)) {
            return "warning";
        }
        return "";
    };

    const enforceEditableLimit = (element) => {
        const maxLength = Number(element?.dataset.maxLength || 0);
        if (!maxLength) {
            return;
        }
        const value = element === bioCopy ? getBioValue() : normalizeLineEndings(element.textContent).trim();
        if (value.length <= maxLength) {
            return;
        }
        const truncatedValue = value.slice(0, maxLength);
        if (element === bioCopy) {
            setBioEditorValue(truncatedValue);
        } else {
            element.textContent = truncatedValue;
        }
        syncHiddenFields();
    };

    const updateCharacterCounter = (element) => {
        const counterId = element?.dataset.characterCounterSource;
        const counter = counterId ? document.getElementById(counterId) : null;
        const maxLength = Number(element?.dataset.maxLength || 0);
        if (!counter || !maxLength) {
            return;
        }
        enforceEditableLimit(element);
        const value = element === bioCopy ? getBioValue() : normalizeLineEndings(element.textContent).trim();
        const count = Math.min(value.length, maxLength);
        const status = getCounterStatus(count, maxLength);
        counter.textContent = `${count} / ${maxLength}`;
        counter.hidden = !isEditing;
        counter.classList.toggle("is-warning", status === "warning");
        counter.classList.toggle("is-danger", status === "danger");
    };

    const updateCounters = () => {
        updateCharacterCounter(usernameCopy);
        updateCharacterCounter(bioCopy);
    };

    const renderLinksPlaceholder = () => {
        const existingPlaceholder = linkGrid.querySelector("[data-links-placeholder]");
        const values = getChips();

        if (!isEditing || values.length) {
            existingPlaceholder?.remove();
            return;
        }

        if (!existingPlaceholder) {
            const placeholder = document.createElement("p");
            placeholder.className = "profile-links-placeholder";
            placeholder.dataset.linksPlaceholder = "";
            placeholder.textContent = linkPlaceholderText;
            linkGrid.append(placeholder);
        }
    };

    const syncHiddenFields = () => {
        usernameInput.value = normalizeLineEndings(usernameCopy.textContent).trim();
        bioInput.value = getBioValue();
        const values = getChips();
        linksInput.value = values.join("|");

        const emailChip = values.find((chip) => chip.includes("@"));
        if (emailInput && emailChip) {
            emailInput.value = emailChip;
        }
    };

    const renderChip = (value) => {
        const storedValue = value.trim();
        const chip = document.createElement("span");
        chip.className = "link-chip profile-link-chip";
        chip.dataset.value = storedValue;

        const chipLabel = document.createElement("span");
        chipLabel.className = "profile-link-chip-label";
        chipLabel.textContent = getContactLabel(storedValue);
        chip.append(chipLabel);

        if (isEditing) {
            chip.tabIndex = 0;
            chip.setAttribute("role", "button");
            chip.setAttribute("aria-label", `Edit ${storedValue}`);
            chip.addEventListener("click", () => {
                editingChip = chip;
                isEditingExistingChip = true;
                linkEntry.value = getChipValue(chip);
                linkEntry.focus();
            });

            const removeButton = document.createElement("button");
            removeButton.className = "profile-link-remove";
            removeButton.type = "button";
            removeButton.setAttribute("aria-label", `Remove ${getContactLabel(storedValue)}`);
            removeButton.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">close</span>';
            removeButton.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                if (editingChip === chip) {
                    editingChip = null;
                    isEditingExistingChip = false;
                    linkEntry.value = "";
                }
                chip.remove();
                renderLinksPlaceholder();
                syncHiddenFields();
            });
            chip.append(removeButton);
        }

        return chip;
    };

    const refreshChips = () => {
        const values = getChips();
        linkGrid.replaceChildren(...values.map((value) => renderChip(value)));
        renderLinksPlaceholder();
    };

    const setEditing = (nextValue) => {
        isEditing = nextValue;
        profileForm?.classList.toggle("is-editing", nextValue);
        usernameCopy.contentEditable = String(nextValue);
        usernameCopy.classList.toggle("is-editing", nextValue);
        bioCopy.contentEditable = String(nextValue);
        bioCopy.classList.toggle("is-editing", nextValue);
        if (avatarInput) {
            avatarInput.disabled = !nextValue;
        }
        linkEntry.hidden = !nextValue;
        bioHelper && (bioHelper.hidden = !nextValue);
        linksHelper && (linksHelper.hidden = !nextValue);
        updateCounters();
        editButton.textContent = nextValue ? "Save profile" : "Edit profile";
        editButton.type = nextValue ? "submit" : "button";
        editButton.classList.remove("is-disabled");
        editButton.classList.toggle("is-editing-mode", nextValue);
        avatarUpload?.classList.toggle("is-editing", nextValue);
        refreshChips();

        if (nextValue) {
            clearBioPlaceholderForEditing();
            scrubBioEditor();
            usernameCopy.focus();
        } else {
            scrubBioEditor();
        }
    };

    editButton.addEventListener("click", (event) => {
        if (isEditing) {
            syncHiddenFields();
            return;
        }

        event.preventDefault();
        setEditing(true);
    });

    linkEntry.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") {
            return;
        }

        event.preventDefault();
        const value = linkEntry.value.trim();

        if (!value) {
            return;
        }

        if (editingChip && isEditingExistingChip) {
            editingChip.dataset.value = value;
            editingChip = null;
            isEditingExistingChip = false;
            refreshChips();
        } else {
            if (getChips().length >= maxProfileLinks) {
                showValidationToast(`Add no more than ${maxProfileLinks} profile links.`);
                return;
            }
            linkGrid.append(renderChip(value));
        }
        linkEntry.value = "";
        renderLinksPlaceholder();
        syncHiddenFields();
    });

    linkEntry.addEventListener("input", () => {
        if (!isEditingExistingChip) {
            editingChip = null;
        }
    });

    linkEntry.addEventListener("focus", () => {
        if (!linkEntry.value.trim()) {
            editingChip = null;
            isEditingExistingChip = false;
        }
    });

    avatarInput?.addEventListener("change", () => {
        const [file] = avatarInput.files || [];
        if (!file || !avatarPreview) {
            return;
        }

        const objectUrl = URL.createObjectURL(file);
        avatarPreview.src = objectUrl;
        avatarPreview.hidden = false;
        avatarFallback?.setAttribute("hidden", "hidden");
    });

    avatarUpload?.addEventListener("click", (event) => {
        if (!isEditing) {
            event.preventDefault();
        }
    });

    bioCopy.addEventListener("focus", () => {
        if (isEditing) {
            clearBioPlaceholderForEditing();
        }
    });
    bioCopy.addEventListener("input", () => {
        scrubBioEditor();
        enforceEditableLimit(bioCopy);
        syncHiddenFields();
        updateCharacterCounter(bioCopy);
    });
    bioCopy.addEventListener("paste", () => {
        window.setTimeout(() => {
            scrubBioEditor();
            enforceEditableLimit(bioCopy);
            syncHiddenFields();
            updateCharacterCounter(bioCopy);
        }, 0);
    });
    bioCopy.addEventListener("blur", () => {
        scrubBioEditor();
        enforceEditableLimit(bioCopy);
        syncHiddenFields();
        updateCharacterCounter(bioCopy);
    });

    [usernameCopy].forEach((element) => {
        element.addEventListener("input", () => {
            enforceEditableLimit(element);
            syncHiddenFields();
            updateCharacterCounter(element);
        });
        element.addEventListener("paste", () => {
            window.setTimeout(() => {
                enforceEditableLimit(element);
                syncHiddenFields();
                updateCharacterCounter(element);
            }, 0);
        });
        element.addEventListener("blur", () => {
            enforceEditableLimit(element);
            syncHiddenFields();
            updateCharacterCounter(element);
        });
    });

    profileForm?.addEventListener("submit", (event) => {
        clearBioPlaceholderForEditing();
        scrubBioEditor();
        syncHiddenFields();
        if ((usernameInput.value || "").length > usernameMaxLength) {
            event.preventDefault();
            showValidationToast(`Username must be ${usernameMaxLength} characters or fewer.`);
            return;
        }
        if ((bioInput.value || "").length > bioMaxLength) {
            event.preventDefault();
            showValidationToast(`Bio must be ${bioMaxLength} characters or fewer.`);
            return;
        }
        if (getChips().length > maxProfileLinks) {
            event.preventDefault();
            showValidationToast(`Add no more than ${maxProfileLinks} profile links.`);
        }
    });

    setBioEditorValue(bioInput.value || bioCopy.textContent);
    renderLinksPlaceholder();
    updateCounters();
}

function setupProfileMobileDisclosure() {
    if (!document.body.classList.contains("profile-page")) {
        return;
    }

    const mobileQuery = window.matchMedia("(max-width: 767.98px)");
    const targets = [
        ...Array.from(document.querySelectorAll(".profile-bio-copy")).map(
            (element) => ({ element, label: "Bio" }),
        ),
        ...Array.from(document.querySelectorAll(".profile-link-chip-grid")).map(
            (element) => ({ element, label: "Links" }),
        ),
    ];

    const refreshTarget = ({ element, label }) => {
        const card = element.closest(".profile-meta-card");
        if (!card) {
            return;
        }

        let toggle = card.querySelector(
            `.profile-more-toggle[data-profile-toggle="${label.toLowerCase()}"]`,
        );

        element.classList.remove("is-expanded");

        if (!mobileQuery.matches) {
            toggle?.remove();
            return;
        }

        const hasOverflow = element.scrollHeight > element.clientHeight + 1;

        if (!hasOverflow) {
            toggle?.remove();
            return;
        }

        if (!toggle) {
            toggle = document.createElement("button");
            toggle.className = "profile-more-toggle";
            toggle.type = "button";
            toggle.dataset.profileToggle = label.toLowerCase();
            card.append(toggle);
        }

        toggle.textContent = `Show more ${label.toLowerCase()}`;
        toggle.setAttribute("aria-expanded", "false");

        toggle.onclick = () => {
            const isExpanded = element.classList.toggle("is-expanded");
            toggle.textContent = `${isExpanded ? "Show less" : "Show more"} ${label.toLowerCase()}`;
            toggle.setAttribute("aria-expanded", String(isExpanded));
        };
    };

    const refresh = () => {
        targets.forEach(refreshTarget);
    };

    refresh();
    mobileQuery.addEventListener?.("change", refresh);
    window.addEventListener("resize", refresh);
}

function initializeProfile() {
    setupProfilePostFilters();
    setupProfileInlineEditor();
    setupProfileMobileDisclosure();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeProfile, { once: true });
} else {
    initializeProfile();
}

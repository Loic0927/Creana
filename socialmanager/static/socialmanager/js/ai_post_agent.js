(function () {
    "use strict";

    const root = document.querySelector("[data-ai-post-agent]");
    const form = document.querySelector(".create-post-page form");
    if (!root || !form) return;

    const panel = root.querySelector("[data-agent-panel]");
    const launcher = root.querySelector("[data-agent-open]");
    const closeButton = root.querySelector("[data-agent-close]");
    const nextButton = root.querySelector("[data-agent-next]");
    const backButton = root.querySelector("[data-agent-back]");
    const modal = root.querySelector("[data-agent-modal]");
    const pageBackdrop = root.querySelector("[data-agent-page-backdrop]");
    const appShell = document.querySelector(".app-shell");
    document.body.append(root);
    const state = {
        currentStep: 1,
        detectedMedia: {},
        context: "",
        contentGoal: "",
        customContentGoal: "",
        generateFields: ["title", "caption", "hashtags"],
        generatedContent: {},
        isGenerating: false,
        loadingFields: new Set(),
        lastRequestedFields: [],
        informationEmptyConfirmed: false,
        mediaSignature: "",
        requestVersion: 0,
        abortController: null,
        appShellAriaHidden: null,
    };

    const copy = (name) => root.dataset[name] || "";
    function resolvePostField(fieldName) {
        const format = document.getElementById("id_content_format")?.value || "article";
        const prefix = format === "video" ? "video" : (format === "image" || format === "carousel") ? "illustration" : "article";
        const suffix = fieldName === "caption" && prefix === "article" ? "article_caption" : fieldName;
        const activeField = document.getElementById(`${prefix}_${suffix}_input`);
        if (activeField) return activeField;

        const shellId = fieldName === "caption" && prefix === "article"
            ? "article-caption-field-shell"
            : `${fieldName}-field-shell`;
        const visibleField = document.getElementById(shellId)?.querySelector("input:not([type='hidden']), textarea:not([hidden])");
        if (visibleField) return visibleField;
        return document.getElementById(`id_${fieldName}`);
    }

    const fieldMap = () => ({
        title: resolvePostField("title"),
        caption: resolvePostField("caption"),
        hashtags: resolvePostField("hashtags"),
    });

    function detectMedia() {
        const format = document.getElementById("id_content_format")?.value || "article";
        const articleInput = document.getElementById("article_body_input") || document.getElementById("id_caption");
        const imageInput = document.getElementById("id_illustration_images");
        const coverInput = document.querySelector("#article-media-panel input[type='file']");
        const videoInput = document.querySelector("#video-media-panel input[type='file']");
        const articlePreview = document.getElementById("article-preview-wrap");
        const illustrationPreview = document.getElementById("illustration-preview-wrap");
        const videoPreview = document.getElementById("video-preview-wrap");
        const existingImage = Boolean((!articlePreview?.hidden && articlePreview?.dataset.existingSrc) || (!illustrationPreview?.hidden && illustrationPreview?.dataset.existingSrc));
        const existingVideo = Boolean((!videoPreview?.hidden && videoPreview?.dataset.existingSrc) || document.getElementById("uploaded-video-object-name")?.value);
        const imageCount = (imageInput?.files?.length || 0) + (coverInput?.files?.length || 0);
        state.detectedMedia = {
            article: format === "article" && Boolean(articleInput?.value?.trim()),
            image: imageCount === 1 || existingImage,
            carousel: imageCount > 1,
            video: (videoInput?.files?.length || 0) > 0 || existingVideo,
        };
        return Object.values(state.detectedMedia).some(Boolean);
    }

    function hasAnalyzableVisualMedia() {
        detectMedia();
        return Boolean(state.detectedMedia.image || state.detectedMedia.carousel || state.detectedMedia.video);
    }

    function fileIdentity(file) {
        return [file.name || "", file.size || 0, file.type || "", file.lastModified || 0].join(":");
    }

    function mediaSignature() {
        const fileInputs = [...form.querySelectorAll("#article-media-panel input[type='file'], #illustration-media-panel input[type='file'], #video-media-panel input[type='file']")];
        const files = fileInputs.flatMap((input) => [...(input.files || [])].map((file) => `${input.id}:${fileIdentity(file)}`));
        const previews = ["article-preview-wrap", "illustration-preview-wrap", "video-preview-wrap"].map((id) => {
            const element = document.getElementById(id);
            return `${id}:${element?.hidden ? "hidden" : "visible"}:${element?.dataset.existingSrc || ""}`;
        });
        const storedMarkers = [...form.querySelectorAll("[data-existing-image-id], input[name*='deleted'], input[name*='image_order']")].map((element) =>
            `${element.dataset.existingImageId || element.name || ""}:${element.value || ""}`
        );
        return JSON.stringify({
            format: document.getElementById("id_content_format")?.value || "",
            files,
            previews,
            storedMarkers,
            uploadedVideo: document.getElementById("uploaded-video-object-name")?.value || "",
        });
    }

    function renderStep() {
        root.querySelectorAll("[data-agent-step]").forEach((step) => { step.hidden = Number(step.dataset.agentStep) !== state.currentStep; });
        root.querySelectorAll("[data-progress-step]").forEach((item) => {
            const step = Number(item.dataset.progressStep);
            item.classList.toggle("is-active", step === state.currentStep);
            item.classList.toggle("is-complete", step < state.currentStep);
        });
        backButton.hidden = state.currentStep === 1;
        nextButton.hidden = state.currentStep === 3;
        if (state.currentStep === 3) updateExistingWarning();
        panel.querySelector("[data-agent-step]:not([hidden]) h3")?.focus?.();
    }

    function openPanel() {
        if (!detectMedia()) {
            showModal(copy("mediaModalTitle"), copy("mediaModalMessage"), [
                { label: copy("ok"), primary: true, callback: () => launcher.focus() },
            ]);
            return;
        }
        state.informationEmptyConfirmed = false;
        state.mediaSignature = mediaSignature();
        state.requestVersion += 1;
        pageBackdrop.hidden = false;
        panel.hidden = false;
        launcher.hidden = true;
        launcher.setAttribute("aria-expanded", "true");
        document.documentElement.classList.add("ai-agent-modal-open");
        document.body.classList.add("ai-agent-modal-open");
        if (appShell) {
            state.appShellAriaHidden = appShell.getAttribute("aria-hidden");
            appShell.inert = true;
            appShell.setAttribute("aria-hidden", "true");
        }
        renderStep();
        closeButton.focus();
    }
    function closePanel() {
        state.requestVersion += 1;
        state.abortController?.abort();
        state.abortController = null;
        state.isGenerating = false;
        state.loadingFields.clear();
        backButton.disabled = false;
        nextButton.disabled = false;
        const generateButton = root.querySelector("[data-agent-generate]");
        generateButton.disabled = false;
        generateButton.textContent = copy("generate");
        panel.setAttribute("aria-busy", "false");
        state.informationEmptyConfirmed = false;
        hideModal(false);
        panel.hidden = true;
        pageBackdrop.hidden = true;
        launcher.hidden = false;
        launcher.setAttribute("aria-expanded", "false");
        document.documentElement.classList.remove("ai-agent-modal-open");
        document.body.classList.remove("ai-agent-modal-open");
        if (appShell) {
            appShell.inert = false;
            if (state.appShellAriaHidden === null) appShell.removeAttribute("aria-hidden");
            else appShell.setAttribute("aria-hidden", state.appShellAriaHidden);
        }
        launcher.focus();
    }

    function hideModal(restoreFocus = true) {
        if (modal.hidden) return;
        modal.hidden = true;
        modal.querySelector("[data-modal-actions]").replaceChildren();
        if (restoreFocus) {
            if (!panel.hidden) nextButton.focus();
            else launcher.focus();
        }
    }

    function showModal(title, message, actions) {
        modal.querySelector("[data-modal-title]").textContent = title;
        modal.querySelector("[data-modal-message]").textContent = message;
        const actionRoot = modal.querySelector("[data-modal-actions]");
        actionRoot.replaceChildren();
        actions.forEach(({ label, primary, callback }) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = `btn ${primary ? "btn-primary" : "btn-secondary"}`;
            button.textContent = label;
            button.addEventListener("click", () => { hideModal(false); callback?.(); });
            actionRoot.append(button);
        });
        modal.hidden = false;
        actionRoot.querySelector("button")?.focus();
    }

    function advance() {
        if (state.currentStep === 1) {
            state.context = root.querySelector("[data-agent-context]").value.trim();
            if (!state.context && !state.informationEmptyConfirmed) {
                if (!hasAnalyzableVisualMedia()) {
                    showModal(copy("informationTitle"), copy("noMediaInformation"), [
                        { label: copy("goBack"), primary: true, callback: () => nextButton.focus() },
                    ]);
                    return;
                }
                showModal(copy("informationTitle"), copy("emptyInformation"), [
                    { label: copy("goBack"), callback: () => nextButton.focus() },
                    { label: copy("continue"), primary: true, callback: () => {
                        state.informationEmptyConfirmed = true;
                        state.currentStep = 2;
                        renderStep();
                    } },
                ]);
                return;
            }
        }
        if (state.currentStep === 2) {
            const selectedGoal = root.querySelector("[data-content-goal]");
            const goalError = root.querySelector("[data-content-goal-error]");
            if (!selectedGoal.value) {
                goalError.hidden = false;
                goalError.focus();
                return;
            }
            state.contentGoal = selectedGoal.value;
            goalError.hidden = true;
            if (state.contentGoal === "other") {
                const customGoal = root.querySelector("[data-custom-goal]");
                const customGoalError = root.querySelector("[data-custom-goal-error]");
                state.customContentGoal = customGoal.value.trim();
                if (!state.customContentGoal || state.customContentGoal.length > 150) {
                    customGoalError.hidden = false;
                    customGoal.setAttribute("aria-invalid", "true");
                    customGoalError.focus();
                    return;
                }
                customGoalError.hidden = true;
                customGoal.setAttribute("aria-invalid", "false");
                customGoal.value = state.customContentGoal;
            }
        }
        state.currentStep = Math.min(3, state.currentStep + 1);
        renderStep();
    }

    function updateExistingWarning() {
        const fields = fieldMap();
        root.querySelector("[data-existing-content-warning]").hidden = !Object.values(fields).some((field) => field?.value?.trim());
    }

    function replaceFieldValue(target, value) {
        target.value = value;
        if (target.id === "id_hashtags" || target.dataset.hashtagDraft === "true") {
            target.dispatchEvent(new CustomEvent("create-post:replace-tags", {
                detail: { value },
                bubbles: true,
            }));
        }
        target.dispatchEvent(new Event("input", { bubbles: true }));
        target.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function applyValue(name, value, button = null) {
        const target = resolvePostField(name);
        if (!target) return;
        const commit = () => {
            replaceFieldValue(target, value);
            if (button) {
                button.textContent = copy("applied") || "Applied";
                button.disabled = true;
            }
        };
        if (target.value.trim() && target.value.trim() !== value.trim()) {
            showModal(copy("replaceTitle"), copy("replaceMessage"), [
                { label: copy("replace"), primary: true, callback: commit },
                { label: copy("cancel") },
            ]);
        } else commit();
    }

    function generatedValue(name, value) {
        return name === "hashtags" ? (Array.isArray(value) ? value.join(" ") : String(value || "")) : String(value || "");
    }

    function syncGeneratedInputsToState() {
        root.querySelectorAll("[data-result-field]").forEach((input) => {
            state.generatedContent[input.dataset.resultField] = input.value;
        });
    }

    function mergeGeneratedContent(existing, incoming, requestedFields) {
        const merged = { ...existing };
        requestedFields.forEach((name) => {
            if (Object.prototype.hasOwnProperty.call(incoming || {}, name) && incoming[name] !== null) {
                merged[name] = generatedValue(name, incoming[name]);
            }
        });
        return merged;
    }

    function renderGeneratedContent() {
        const results = root.querySelector("[data-agent-results]");
        const previousScroll = root.querySelector(".ai-agent-body")?.scrollTop || 0;
        results.replaceChildren();
        state.generateFields.forEach((name) => {
            const card = document.createElement("article");
            card.className = "ai-agent-result";
            const heading = document.createElement("h4"); heading.textContent = copy(`result${name[0].toUpperCase()}${name.slice(1)}`);
            const editor = document.createElement(name === "title" ? "input" : "textarea");
            if (name === "title") editor.type = "text";
            editor.className = `ai-agent-result-field ai-agent-result-field-${name}`;
            editor.value = state.generatedContent[name] || "";
            editor.dataset.resultField = name;
            editor.addEventListener("input", () => { state.generatedContent[name] = editor.value; });
            const actions = document.createElement("div"); actions.className = "ai-agent-result-actions";
            const applyButton = document.createElement("button");
            applyButton.type = "button"; applyButton.className = "btn btn-primary"; applyButton.textContent = copy("apply");
            applyButton.addEventListener("click", () => applyValue(name, editor.value, applyButton));
            const regenerateButton = document.createElement("button");
            regenerateButton.type = "button"; regenerateButton.className = "btn btn-secondary";
            regenerateButton.disabled = state.loadingFields.has(name);
            regenerateButton.textContent = state.loadingFields.has(name) ? copy("generating") : copy("regenerate");
            regenerateButton.addEventListener("click", () => generateContent([name]));
            actions.append(applyButton, regenerateButton);
            card.append(heading, editor, actions); results.append(card);
        });
        if (state.generateFields.length > 1) {
            const applyAll = document.createElement("button"); applyAll.type = "button"; applyAll.className = "btn btn-primary"; applyAll.textContent = copy("applyAll");
            applyAll.addEventListener("click", () => {
                syncGeneratedInputsToState();
                state.generateFields.forEach((name) => applyValue(name, state.generatedContent[name] || ""));
                applyAll.textContent = copy("applied") || "Applied";
                applyAll.disabled = true;
            });
            results.append(applyAll);
        }
        results.hidden = false;
        const body = root.querySelector(".ai-agent-body");
        if (body) body.scrollTop = previousScroll;
    }

    function csrfToken() {
        return form.querySelector("input[name='csrfmiddlewaretoken']")?.value || "";
    }

    function requestPayload(requestedFields) {
        detectMedia();
        const fields = fieldMap();
        const data = new FormData();
        data.set("context", state.context);
        data.set("skipped_context", String(!state.context));
        data.set("content_goal", state.contentGoal);
        data.set("custom_content_goal", state.contentGoal === "other" ? state.customContentGoal : "");
        data.set("requested_fields", JSON.stringify(requestedFields));
        data.set("detected_media_types", JSON.stringify(Object.entries(state.detectedMedia).filter(([, found]) => found).map(([name]) => name)));
        data.set("post_id", form.dataset.postId || "");
        data.set("article_text", document.getElementById("article_body_input")?.value || "");
        data.set("current_title", fields.title?.value || "");
        data.set("current_caption", fields.caption?.value || "");
        data.set("current_hashtags", fields.hashtags?.value || "");
        const imageInputs = [
            document.getElementById("id_illustration_images"),
            document.querySelector("#article-media-panel input[type='file']"),
        ];
        const seenFiles = new Set();
        imageInputs.flatMap((input) => [...(input?.files || [])]).forEach((file) => {
            const identity = fileIdentity(file);
            if (seenFiles.has(identity)) return;
            seenFiles.add(identity);
            data.append("image_files", file, file.name);
        });
        return data;
    }

    function showApiError(message, requestedFields, retryable = true) {
        const errorBox = root.querySelector("[data-agent-api-error]");
        errorBox.replaceChildren(document.createTextNode(message || copy("genericError")));
        if (retryable) {
            const retry = document.createElement("button");
            retry.type = "button";
            retry.className = "btn btn-secondary";
            retry.textContent = copy("retry");
            retry.addEventListener("click", () => generateContent(requestedFields));
            errorBox.append(document.createElement("br"), retry);
        }
        errorBox.hidden = false;
    }

    async function generateContent(onlyFields = null) {
        if (state.isGenerating) return;
        if (mediaSignature() !== state.mediaSignature) {
            state.informationEmptyConfirmed = false;
            showApiError(copy("mediaChanged"), [], false);
            return;
        }
        const selected = onlyFields || [...root.querySelectorAll("[data-generate-field]:checked")].map((input) => input.value);
        if (!selected.length) {
            showApiError(copy("noFieldsError"), selected);
            return;
        }
        const button = root.querySelector("[data-agent-generate]");
        syncGeneratedInputsToState();
        state.isGenerating = true;
        panel.setAttribute("aria-busy", "true");
        backButton.disabled = true;
        nextButton.disabled = true;
        selected.forEach((name) => state.loadingFields.add(name));
        state.lastRequestedFields = [...selected];
        button.disabled = true;
        if (!onlyFields) button.textContent = copy("generating");
        if (onlyFields && state.generateFields.length) renderGeneratedContent();
        root.querySelector("[data-agent-api-error]").hidden = true;
        root.querySelector("[data-agent-api-warning]").hidden = true;
        let renderedResponse = false;
        const requestVersion = ++state.requestVersion;
        state.abortController = new AbortController();
        try {
            const response = await fetch(root.dataset.generateUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: { "X-CSRFToken": csrfToken() },
                body: requestPayload(selected),
                signal: state.abortController.signal,
            });
            if (requestVersion !== state.requestVersion || panel.hidden) return;
            let payload = null;
            try { payload = await response.json(); } catch (_error) { payload = null; }
            if (requestVersion !== state.requestVersion || panel.hidden) return;
            if (!response.ok || !payload?.success) {
                const code = payload?.error_code || "provider_error";
                if (code === "membership_required") {
                    window.openAiMembershipModal?.();
                    return;
                }
                const fallback = code === "provider_timeout" ? copy("timeoutError") : code === "provider_rate_limited" ? copy("rateLimitError") : copy("genericError");
                showApiError(payload?.message || fallback, selected);
                return;
            }
            state.generateFields = onlyFields
                ? [...new Set([...state.generateFields, ...selected])]
                : [...selected];
            state.generatedContent = mergeGeneratedContent(state.generatedContent, payload.data, selected);
            selected.forEach((name) => state.loadingFields.delete(name));
            root.classList.add("has-generated-results");
            renderGeneratedContent();
            renderedResponse = true;
            const warnings = Array.isArray(payload.data.warnings) ? payload.data.warnings : [];
            if (warnings.length) {
                const warningBox = root.querySelector("[data-agent-api-warning]");
                warningBox.textContent = warnings.join(" ");
                warningBox.hidden = false;
            }
        } catch (requestError) {
            if (requestError.name === "AbortError" || requestVersion !== state.requestVersion) return;
            showApiError(copy("genericError"), selected);
        } finally {
            if (requestVersion === state.requestVersion) {
                state.isGenerating = false;
                state.abortController = null;
                selected.forEach((name) => state.loadingFields.delete(name));
                button.disabled = false;
                backButton.disabled = false;
                nextButton.disabled = false;
                button.textContent = copy("generate");
                panel.setAttribute("aria-busy", "false");
                if (onlyFields && state.generateFields.length && !renderedResponse) renderGeneratedContent();
            }
        }
    }

    launcher.addEventListener("click", openPanel);
    closeButton.addEventListener("click", closePanel);
    nextButton.addEventListener("click", advance);
    backButton.addEventListener("click", () => { state.currentStep = Math.max(1, state.currentStep - 1); renderStep(); });
    root.querySelector("[data-agent-context]").addEventListener("input", (event) => {
        state.context = event.target.value;
        state.informationEmptyConfirmed = false;
        root.querySelector("[data-context-count]").textContent = event.target.value.length;
    });
    root.querySelector("[data-content-goal]").addEventListener("change", (event) => {
        state.contentGoal = event.target.value;
        root.querySelector("[data-content-goal-error]").hidden = true;
        const isOther = state.contentGoal === "other";
        const customWrap = root.querySelector("[data-custom-goal-wrap]");
        customWrap.hidden = !isOther;
        if (isOther) {
            root.querySelector("[data-custom-goal]").focus();
        } else {
            state.customContentGoal = "";
            root.querySelector("[data-custom-goal]").value = "";
            root.querySelector("[data-custom-goal]").setAttribute("aria-invalid", "false");
            root.querySelector("[data-custom-goal-count]").textContent = "0";
            root.querySelector("[data-custom-goal-error]").hidden = true;
        }
    });
    root.querySelector("[data-custom-goal]").addEventListener("input", (event) => {
        state.customContentGoal = event.target.value;
        event.target.setAttribute("aria-invalid", "false");
        root.querySelector("[data-custom-goal-count]").textContent = String(event.target.value.length);
        root.querySelector("[data-custom-goal-error]").hidden = true;
    });
    root.querySelector("[data-agent-generate]").addEventListener("click", () => generateContent());
    form.querySelectorAll("#article-media-panel input, #illustration-media-panel input, #video-media-panel input, #uploaded-video-object-name").forEach((input) => {
        input.addEventListener("change", () => { state.informationEmptyConfirmed = false; });
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !modal.hidden) {
            event.preventDefault();
            hideModal();
            return;
        } else if (event.key === "Escape" && !panel.hidden) {
            event.preventDefault();
            closePanel();
            return;
        }
        if (event.key === "Tab" && (!modal.hidden || !panel.hidden)) {
            const focusRoot = !modal.hidden ? modal : panel;
            const focusable = [...focusRoot.querySelectorAll("button:not([hidden]), input:not([hidden]), textarea:not([hidden]), select:not([hidden]), [href]")].filter((item) => !item.disabled && item.offsetParent);
            if (!focusable.length) return;
            if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable.at(-1).focus(); }
            else if (!event.shiftKey && document.activeElement === focusable.at(-1)) { event.preventDefault(); focusable[0].focus(); }
        }
    });
    document.addEventListener("focusin", (event) => {
        if (!modal.hidden && !modal.contains(event.target)) {
            modal.querySelector("button")?.focus();
            return;
        }
        if (panel.hidden || root.contains(event.target)) return;
        closeButton.focus();
    });

    renderStep();
})();

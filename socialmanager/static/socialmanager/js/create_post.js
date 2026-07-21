function setupCreatePostPage() {
    if (!document.body.classList.contains("create-post-page")) {
        return;
    }

    const typeButtons = Array.from(document.querySelectorAll(".post-type-tab"));
    const typePanels = Array.from(document.querySelectorAll(".post-type-panel"));
    const sharedEditorSections = Array.from(
        document.querySelectorAll(".shared-editor-section"),
    );
    const postForm =
        document.querySelector(".create-post-page form[data-ai-feedback-url]") ||
        document.querySelector(".create-post-page form");
    const isEditingPost = postForm?.dataset.isEditing === "true";
    const uiLabels = {
        title: postForm?.dataset.labelTitle || "",
        caption: postForm?.dataset.labelCaption || "",
        captionBody: postForm?.dataset.labelCaptionBody || "",
        article: postForm?.dataset.labelArticle || "Article",
        body: postForm?.dataset.labelBody || "Body",
        size: postForm?.dataset.labelSize || "Size",
        writeArticle: postForm?.dataset.labelWriteArticle || "Write your article...",
    };
    const contentFormatInput = document.getElementById("id_content_format");
    const statusInput = document.getElementById("id_status");
    const scheduledForInput = document.getElementById("id_scheduled_for");
    const scheduleButton = document.getElementById("schedule-btn");
    const publishButton = document.getElementById("publish-btn");
    const draftButton = document.getElementById("draft-btn");
    const schedulePanel = document.getElementById("schedule-panel");
    const scheduleDate = document.getElementById("schedule-date");
    const scheduleTime = document.getElementById("schedule-time");
    const confirmScheduleButton = document.getElementById(
        "confirm-schedule-btn",
    );
    const scheduleMessage = document.getElementById("schedule-message");
    const imageInput = document.getElementById("id_image");
    const videoInput = document.getElementById("id_video_file");
    const videoThumbnailInput = document.getElementById("id_video_thumbnail");
    const uploadedVideoObjectNameInput = document.getElementById("uploaded-video-object-name");
    const uploadedVideoDurationInput = document.getElementById("uploaded-video-duration-seconds");
    const directVideoUploadEnabled = postForm?.dataset.directVideoUploadEnabled === "true";
    const geminiVideoMaxBytes = Number(postForm?.dataset.geminiVideoMaxBytes || 50 * 1024 * 1024);
    const geminiVideoMaxSeconds = Number(postForm?.dataset.geminiVideoMaxSeconds || 60);
    const videoMaxDurationSeconds = Number(postForm?.dataset.videoMaxDurationSeconds || 60);
    const illustrationImagesInput = document.getElementById("id_illustration_images");
    const mediaUploadCard = document.getElementById("media-upload-card");
    const articleMediaPanel = document.getElementById("article-media-panel");
    const illustrationMediaPanel = document.getElementById("illustration-media-panel");
    const videoMediaPanel = document.getElementById("video-media-panel");
    const articleUploadState = document.getElementById("article-upload-state");
    const articlePreviewWrap = document.getElementById("article-preview-wrap");
    const articlePreview = document.getElementById("article-preview");
    const articlePreviewNote = document.getElementById("article-preview-note");
    const previewWrapper = document.getElementById("illustration-preview-wrap");
    const previewGrid = document.getElementById("illustration-preview-grid");
    const previewNote = document.getElementById("illustration-preview-note");
    const videoUploadState = document.getElementById("video-upload-state");
    const videoPreviewWrap = document.getElementById("video-preview-wrap");
    const previewVideo = document.getElementById("video-preview");
    const videoPreviewNote = document.getElementById("video-preview-note");
    const uploadError = document.getElementById("illustration-upload-error");
    const uploadState = document.getElementById("illustration-upload-state");
    const illustrationUploadControl = document.getElementById("illustration-upload-control");
    const videoUploadControl = document.getElementById("video-upload-control");
    const replaceArticleButton = document.getElementById("replace-article-media-btn");
    const removeArticleButton = document.getElementById("remove-article-media-btn");
    const replaceButton = document.getElementById("replace-illustration-btn");
    const removeButton = document.getElementById("remove-illustration-btn");
    const replaceVideoButton = document.getElementById("replace-video-btn");
    const removeVideoButton = document.getElementById("remove-video-btn");
    const articleTitleSlot = document.getElementById("article-title-slot");
    const articleBodySlot = document.getElementById("article-body-slot");
    const sharedTitleSlot = document.getElementById("shared-title-slot");
    const sharedCaptionSlot = document.getElementById("shared-caption-slot");
    const hiddenFieldsSlot = document.getElementById("create-post-hidden-fields");
    const titleFieldShell = document.getElementById("title-field-shell");
    const captionFieldShell = document.getElementById("caption-field-shell");
    const articleCaptionFieldShell = document.getElementById(
        "article-caption-field-shell",
    );
    const titleInput = document.getElementById("id_title");
    const captionInput = document.getElementById("id_caption");
    const articleCaptionInput = document.getElementById("id_article_caption");
    const hashtagsInput = document.getElementById("id_hashtags");
    const hashtagsFieldShell = hashtagsInput?.closest("[data-ai-field]");
    const visibilityInput = document.getElementById("id_visibility");
    const campaignInput = document.getElementById("id_campaign");
    const titleLabel = titleFieldShell?.querySelector("label");
    const captionLabel = captionFieldShell?.querySelector("label");
    const aiFeedbackButtons = Array.from(
        document.querySelectorAll(".create-post-ai-link[data-ai-target]"),
    );

    if (!typeButtons.length || !typePanels.length || !contentFormatInput) {
        return;
    }

    const typeToFormat = {
        article: "article",
        illustration: "image",
        video: "video",
    };

    const maxIllustrationImages = 10;
    let selectedArticleImageFile = null;
    let articleObjectUrl = null;
    let videoObjectUrl = null;
    let videoUploadInProgress = false;
    let activeVideoUploadController = null;
    let videoThumbnailObjectUrl = null;
    let videoThumbnailGenerationPromise = null;
    let videoThumbnailGenerationId = 0;
    let selectedVideoDurationSeconds = null;
    let selectedVideoDurationReady = false;
    let selectedVideoDurationError = "";
    let selectedIllustrationFiles = [];
    let illustrationObjectUrls = [];
    let currentPostType = "article";
    const defaultVisibilityValue = visibilityInput?.value || "";
    const defaultTitlePlaceholder = titleInput?.getAttribute("placeholder") || "Optional article title";
    const defaultCaptionPlaceholder =
        captionInput?.getAttribute("placeholder") ||
        "Write a clear, concise caption for your audience. (up to 250 characters)";
    const titleMaxLength = Number(titleInput?.dataset.characterCounterMax || titleInput?.maxLength || 50);
    const captionMaxLength = Number(captionInput?.dataset.characterCounterMax || captionInput?.maxLength || 250);
    const maxHashtags = Number(hashtagsInput?.dataset.maxTags || 5);
    const videoUploadChunkSize = 8 * 1024 * 1024;
    const videoUploadMaxRetries = 4;
    const videoUploadRetryStatuses = new Set([408, 429, 500, 502, 503, 504]);
    const videoDurationToleranceSeconds = 0.05;
    const videoTooLongMessage = "Please provide a video that is 60 seconds or shorter.";
    const videoDurationUnreadableMessage = "The video duration could not be read. Please choose a supported video file.";
    const videoTextFallbackMessage = "Video analysis is unavailable. Please add a title, caption, or hashtags so AI can generate a suggestion from your text.";

    const createDraftInput = (id, sourceInput) => {
        const input = document.createElement("input");
        input.type = "text";
        input.id = id;
        input.className = sourceInput?.className || "field-input";
        input.placeholder = sourceInput?.getAttribute("placeholder") || "";
        if (sourceInput?.maxLength && sourceInput.maxLength > 0) {
            input.maxLength = sourceInput.maxLength;
        }
        input.autocomplete = "off";
        input.dataset.lpignore = "true";
        input.dataset.formType = "other";
        input.dataset.draftControl = "true";
        if (sourceInput?.dataset.maxTags) {
            input.dataset.maxTags = sourceInput.dataset.maxTags;
        }
        if (sourceInput?.dataset.characterCounter) {
            input.dataset.characterCounter = sourceInput.dataset.characterCounter;
        }
        if (sourceInput?.dataset.characterCounterMax) {
            input.dataset.characterCounterMax = sourceInput.dataset.characterCounterMax;
        }
        return input;
    };

    const createDraftTextarea = (id, sourceInput) => {
        const textarea = document.createElement("textarea");
        textarea.id = id;
        textarea.className = sourceInput?.className || "field-input";
        textarea.placeholder = sourceInput?.getAttribute("placeholder") || "";
        if (sourceInput?.maxLength && sourceInput.maxLength > 0) {
            textarea.maxLength = sourceInput.maxLength;
        }
        textarea.autocomplete = "off";
        textarea.dataset.lpignore = "true";
        textarea.dataset.formType = "other";
        textarea.rows = sourceInput?.rows || 4;
        textarea.dataset.draftControl = "true";
        if (sourceInput?.dataset.characterCounter) {
            textarea.dataset.characterCounter = sourceInput.dataset.characterCounter;
        }
        if (sourceInput?.dataset.characterCounterMax) {
            textarea.dataset.characterCounterMax = sourceInput.dataset.characterCounterMax;
        }
        return textarea;
    };

    const applyCharacterLimit = (field, maxLength) => {
        if (!field || !maxLength) {
            return;
        }
        field.maxLength = maxLength;
        field.dataset.characterCounter = "true";
        field.dataset.characterCounterMax = String(maxLength);
    };

    const applyCaptionLimit = (field) => {
        applyCharacterLimit(field, captionMaxLength);
    };

    const getCounterStatus = (count, maxLength) => {
        if (count >= Math.ceil(maxLength * 0.96)) {
            return "danger";
        }
        if (count >= Math.ceil(maxLength * 0.88)) {
            return "warning";
        }
        return "";
    };

    const normalizeLineEndings = (value) => (value || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");

    const enforceCharacterLimit = (field) => {
        if (!field || field.dataset.characterCounter !== "true") {
            return;
        }
        const maxLength = Number(field.dataset.characterCounterMax || field.maxLength || 0);
        const normalizedValue = normalizeLineEndings(field.value);
        const limitedValue = maxLength ? normalizedValue.slice(0, maxLength) : normalizedValue;
        if (field.value !== limitedValue) {
            field.value = limitedValue;
        }
    };

    const getCharacterCounter = (field) => {
        const shell = field?.closest(".field-shell, .field");
        return shell?.querySelector(`[data-character-counter-for="${field.id}"]`) ||
            shell?.querySelector("[data-character-counter-for]");
    };

    const placeCharacterCounterAfterField = (field) => {
        const counter = getCharacterCounter(field);
        if (field && counter && counter.previousElementSibling !== field) {
            field.insertAdjacentElement("afterend", counter);
        }
        return counter;
    };

    const updateCharacterCounter = (field) => {
        if (!field || field.dataset.characterCounter !== "true") {
            return;
        }
        const maxLength = Number(field.dataset.characterCounterMax || field.maxLength || 0);
        if (!maxLength) {
            return;
        }
        enforceCharacterLimit(field);
        const counter = placeCharacterCounterAfterField(field);
        if (counter) {
            const count = Math.min(field.value.length, maxLength);
            const status = getCounterStatus(count, maxLength);
            counter.textContent = `${count} / ${maxLength}`;
            counter.hidden = false;
            counter.classList.toggle("is-warning", status === "warning");
            counter.classList.toggle("is-danger", status === "danger");
        }
    };

    const bindCharacterCounter = (field) => {
        if (!field || field.dataset.characterCounterBound === "true") {
            return;
        }
        field.dataset.characterCounterBound = "true";
        field.addEventListener("input", () => updateCharacterCounter(field));
        field.addEventListener("paste", () => {
            window.setTimeout(() => updateCharacterCounter(field), 0);
        });
        updateCharacterCounter(field);
    };

    const createArticleRichEditor = (sourceInput) => {
        const shell = document.createElement("div");
        shell.className = "article-rich-editor";
        shell.dataset.draftControl = "true";

        const toolbar = document.createElement("div");
        toolbar.className = "article-rich-toolbar";
        toolbar.setAttribute("aria-label", "Article formatting controls");

        const blockSelect = document.createElement("select");
        blockSelect.className = "article-rich-select";
        blockSelect.setAttribute("aria-label", "Text style");
        [
            ["p", uiLabels.body],
            ["h1", "H1"],
            ["h2", "H2"],
            ["h3", "H3"],
            ["h4", "H4"],
            ["h5", "H5"],
        ].forEach(([value, label]) => {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = label;
            blockSelect.append(option);
        });

        const sizeSelect = document.createElement("select");
        sizeSelect.className = "article-rich-select";
        sizeSelect.setAttribute("aria-label", "Font size");
        [
            ["", uiLabels.size],
            ["14px", "14"],
            ["16px", "16"],
            ["18px", "18"],
            ["20px", "20"],
            ["24px", "24"],
            ["28px", "28"],
        ].forEach(([value, label]) => {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = label;
            sizeSelect.append(option);
        });

        const createToolbarButton = (command, label, icon, title) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "article-rich-button";
            button.dataset.command = command;
            button.setAttribute("aria-label", label);
            button.title = title || label;
            button.innerHTML = `<span class="material-symbols-outlined" aria-hidden="true">${icon}</span>`;
            return button;
        };

        const orderedListButton = createToolbarButton("insertOrderedList", "Numbered list", "format_list_numbered");
        const bulletListButton = createToolbarButton("insertUnorderedList", "Bullet list", "format_list_bulleted");
        const boldButton = createToolbarButton("bold", "Bold", "format_bold");
        const italicButton = createToolbarButton("italic", "Italic", "format_italic");

        toolbar.append(blockSelect, sizeSelect, orderedListButton, bulletListButton, boldButton, italicButton);

        const editor = document.createElement("div");
        editor.id = `${sourceInput.id}_rich_surface`;
        editor.className = "article-rich-surface";
        editor.contentEditable = "true";
        editor.role = "textbox";
        editor.setAttribute("aria-multiline", "true");
        editor.dataset.placeholder = uiLabels.writeArticle;

        sourceInput.classList.add("article-rich-source");
        sourceInput.hidden = true;
        sourceInput.style.display = "none";
        sourceInput.tabIndex = -1;

        shell.append(toolbar, editor);

        const allowedTags = new Set(["P", "BR", "STRONG", "B", "EM", "I", "OL", "UL", "LI", "H1", "H2", "H3", "H4", "H5", "SPAN"]);
        const blockedContentTags = new Set(["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED"]);
        const styledTags = new Set(["P", "LI", "H1", "H2", "H3", "H4", "H5", "SPAN"]);
        const allowedTextAlign = new Set(["left", "right", "center", "justify"]);
        const allowedFontStyle = new Set(["normal", "italic", "oblique"]);
        const allowedFontWeight = new Set(["normal", "bold", "bolder", "lighter"]);
        const allowedColorNames = new Set(["black", "blue", "gray", "green", "grey", "navy", "purple", "red", "teal", "white"]);
        const fontSizePattern = /^(?:1[0-9]|[2-4][0-9]|5[0-6])px$/;
        const colorPattern = /^(?:#[0-9a-f]{3}(?:[0-9a-f]{3})?|rgb\(\s*(?:\d{1,3}\s*,\s*){2}\d{1,3}\s*\)|rgba\(\s*(?:\d{1,3}\s*,\s*){3}(?:0|1|0?\.\d+)\s*\))$/;
        let savedRange = null;

        const saveSelection = () => {
            const selection = window.getSelection();
            if (!selection || selection.rangeCount === 0) {
                return;
            }

            const range = selection.getRangeAt(0);
            if (editor.contains(range.commonAncestorContainer)) {
                savedRange = range.cloneRange();
            }
        };

        const restoreSelection = () => {
            editor.focus();
            if (!savedRange) {
                return false;
            }

            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(savedRange);
            return true;
        };

        const normalizeFontSize = (value) => {
            const normalized = (value || "").trim().toLowerCase();
            const pointMatch = normalized.match(/^(\d+(?:\.\d+)?)pt$/);
            if (pointMatch) {
                return `${Math.round(Number(pointMatch[1]) * 1.333)}px`;
            }
            return normalized;
        };

        const cleanStyle = (source, target) => {
            if (!source.style || !styledTags.has(target.tagName)) {
                return;
            }

            const declarations = [];
            const fontSize = normalizeFontSize(source.style.fontSize);
            const fontWeight = (source.style.fontWeight || "").trim().toLowerCase();
            const fontStyle = (source.style.fontStyle || "").trim().toLowerCase();
            const textAlign = (source.style.textAlign || "").trim().toLowerCase();
            const color = (source.style.color || "").trim().toLowerCase();

            if (fontSizePattern.test(fontSize)) {
                declarations.push(`font-size: ${fontSize}`);
            }
            if (
                allowedFontWeight.has(fontWeight) ||
                (/^\d+$/.test(fontWeight) && Number(fontWeight) >= 100 && Number(fontWeight) <= 900)
            ) {
                declarations.push(`font-weight: ${fontWeight}`);
            }
            if (allowedFontStyle.has(fontStyle)) {
                declarations.push(`font-style: ${fontStyle}`);
            }
            if (allowedTextAlign.has(textAlign)) {
                declarations.push(`text-align: ${textAlign}`);
            }
            if (allowedColorNames.has(color) || colorPattern.test(color)) {
                declarations.push(`color: ${color}`);
            }

            if (declarations.length) {
                target.setAttribute("style", declarations.join("; "));
            }
        };

        const cleanNode = (node) => {
            if (node.nodeType === Node.TEXT_NODE) {
                return document.createTextNode(node.textContent || "");
            }

            if (node.nodeType !== Node.ELEMENT_NODE) {
                return document.createTextNode("");
            }

            if (blockedContentTags.has(node.tagName)) {
                return document.createTextNode("");
            }

            let tagName = node.tagName;
            if (tagName === "DIV") {
                tagName = "P";
            }

            if (!allowedTags.has(tagName)) {
                const fragment = document.createDocumentFragment();
                Array.from(node.childNodes).forEach((child) => {
                    fragment.append(cleanNode(child));
                });
                return fragment;
            }

            const normalizedTagName = tagName === "B" ? "strong" : tagName === "I" ? "em" : tagName.toLowerCase();
            const cleanElement = document.createElement(normalizedTagName);
            cleanStyle(node, cleanElement);

            Array.from(node.childNodes).forEach((child) => {
                cleanElement.append(cleanNode(child));
            });

            if (normalizedTagName === "br") {
                return cleanElement;
            }

            return cleanElement;
        };

        const sanitizeHtml = (html) => {
            const template = document.createElement("template");
            template.innerHTML = html || "";
            const fragment = document.createDocumentFragment();
            Array.from(template.content.childNodes).forEach((node) => {
                fragment.append(cleanNode(node));
            });
            const container = document.createElement("div");
            container.append(fragment);
            return container.innerHTML.trim();
        };

        const syncSource = () => {
            sourceInput.value = sanitizeHtml(editor.innerHTML);
        };

        const updateFromSource = () => {
            editor.innerHTML = sanitizeHtml(sourceInput.value || "");
        };

        const focusEditor = () => {
            editor.focus();
        };

        const runCommand = (command, value = null) => {
            restoreSelection() || focusEditor();
            document.execCommand(command, false, value);
            syncSource();
            saveSelection();
        };

        const applyFontSize = (size) => {
            if (!fontSizePattern.test(size)) {
                return;
            }

            restoreSelection();
            const selection = window.getSelection();
            if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
                return;
            }

            const range = selection.getRangeAt(0);
            if (!editor.contains(range.commonAncestorContainer)) {
                return;
            }

            const span = document.createElement("span");
            span.style.fontSize = size;
            try {
                range.surroundContents(span);
            } catch (error) {
                span.append(range.extractContents());
                range.insertNode(span);
            }
            selection.removeAllRanges();
            selection.addRange(range);
            syncSource();
            saveSelection();
        };

        toolbar.addEventListener("mousedown", (event) => {
            saveSelection();
            if (event.target.closest(".article-rich-button")) {
                event.preventDefault();
            }
        });

        blockSelect.addEventListener("change", () => {
            runCommand("formatBlock", blockSelect.value);
        });

        sizeSelect.addEventListener("change", () => {
            applyFontSize(sizeSelect.value);
            sizeSelect.value = "";
        });

        [orderedListButton, bulletListButton, boldButton, italicButton].forEach((button) => {
            button.addEventListener("click", () => {
                runCommand(button.dataset.command);
            });
        });

        editor.addEventListener("input", syncSource);
        editor.addEventListener("keyup", saveSelection);
        editor.addEventListener("mouseup", saveSelection);
        editor.addEventListener("focus", saveSelection);
        editor.addEventListener("blur", () => {
            saveSelection();
            syncSource();
        });
        editor.addEventListener("paste", (event) => {
            event.preventDefault();
            restoreSelection() || focusEditor();
            const html = event.clipboardData?.getData("text/html") || "";
            const text = event.clipboardData?.getData("text/plain") || "";
            if (html) {
                document.execCommand("insertHTML", false, sanitizeHtml(html));
            } else if (text) {
                const escapedHtml = text
                    .split(/\n{2,}/)
                    .map((paragraph) => {
                        const lines = paragraph
                            .split(/\n/)
                            .map((line) =>
                                line
                                    .replace(/&/g, "&amp;")
                                    .replace(/</g, "&lt;")
                                    .replace(/>/g, "&gt;"),
                            )
                            .join("<br>");
                        return lines ? `<p>${lines}</p>` : "";
                    })
                    .join("");
                document.execCommand("insertHTML", false, escapedHtml);
            }
            syncSource();
            saveSelection();
        });

        return {
            shell,
            editor,
            syncSource,
            updateFromSource,
            getText: () => editor.textContent || "",
        };
    };

    const draftFields = {
        article: {
            title: createDraftInput("article_title_input", titleInput),
            caption: createDraftTextarea("article_body_input", captionInput),
            article_caption: createDraftTextarea("article_caption_input", articleCaptionInput),
            hashtags: createDraftInput("article_hashtags_input", hashtagsInput),
        },
        illustration: {
            title: createDraftInput("illustration_title_input", titleInput),
            caption: createDraftTextarea("illustration_caption_input", captionInput),
            article_caption: null,
            hashtags: createDraftInput("illustration_hashtags_input", hashtagsInput),
        },
        video: {
            title: createDraftInput("video_title_input", titleInput),
            caption: createDraftTextarea("video_caption_input", captionInput),
            article_caption: null,
            hashtags: createDraftInput("video_hashtags_input", hashtagsInput),
        },
    };
    const draftHashtagControls = {};
    const draftHashtagShells = {};
    const articleRichEditor = createArticleRichEditor(draftFields.article.caption);

    [draftFields.article.title, draftFields.illustration.title, draftFields.video.title]
        .forEach((field) => applyCharacterLimit(field, titleMaxLength));
    applyCaptionLimit(draftFields.article.article_caption);
    applyCaptionLimit(draftFields.illustration.caption);
    applyCaptionLimit(draftFields.video.caption);
    [
        titleInput,
        captionInput,
        articleCaptionInput,
        draftFields.article.title,
        draftFields.article.article_caption,
        draftFields.illustration.title,
        draftFields.illustration.caption,
        draftFields.video.title,
        draftFields.video.caption,
    ]
        .forEach(bindCharacterCounter);

    const hideMasterField = (input) => {
        if (!input || !hiddenFieldsSlot) {
            return;
        }

        input.autocomplete = "off";
        if (input.tagName === "INPUT") {
            input.type = "hidden";
        } else {
            input.hidden = true;
        }
        hiddenFieldsSlot.append(input);
    };

    const initializeDraftHashtags = () => {
        Object.entries(draftFields).forEach(([type, fields]) => {
            fields.hashtags.dataset.hashtagDraft = "true";
            hiddenFieldsSlot?.append(fields.hashtags);
            draftHashtagControls[type] = createHashtagInput(fields.hashtags, {
                initialValue: "",
                maxTags: maxHashtags,
                placeholder:
                    hashtagsInput?.getAttribute("placeholder") ||
                    document.body.dataset.labelHashtagPlaceholder ||
                    "",
            });
            draftHashtagShells[type] = fields.hashtags.nextElementSibling?.classList.contains("hashtag-input-shell")
                ? fields.hashtags.nextElementSibling
                : null;
        });
    };

    const getActiveDraftFields = () => draftFields[currentPostType] || draftFields.article;

    const syncActiveDraftHashtags = () => {
        const controller = draftHashtagControls[currentPostType];

        if (!controller) {
            return;
        }

        if (controller.entryInput.value.trim()) {
            controller.addTagsFromValue(controller.entryInput.value);
        }

        controller.syncSourceInput();
    };

    const setLabelTarget = (fieldShell, input) => {
        const label = fieldShell?.querySelector("label");
        if (label && input?.id) {
            label.setAttribute("for", input.id);
        }
    };

    const clearDraftMount = (fieldShell) => {
        fieldShell
            ?.querySelectorAll(":scope > [data-draft-control], :scope > .hashtag-input-shell")
            .forEach((node) => hiddenFieldsSlot?.append(node));
    };

    const mountDraftControl = (fieldShell, input, companion = null) => {
        if (!fieldShell || !input) {
            return;
        }

        clearDraftMount(fieldShell);
        fieldShell.append(input);
        if (companion) {
            fieldShell.append(companion);
        }
        placeCharacterCounterAfterField(input);
        setLabelTarget(fieldShell, input);
        bindCharacterCounter(input);
        updateCharacterCounter(input);
    };

    const mountArticleBodyControl = () => {
        if (!captionFieldShell || !draftFields.article.caption) {
            return;
        }

        clearDraftMount(captionFieldShell);
        captionFieldShell.append(articleRichEditor.shell);
        captionFieldShell.append(draftFields.article.caption);
        const counter = getCharacterCounter(draftFields.article.caption);
        if (counter) {
            counter.hidden = true;
        }
        setLabelTarget(captionFieldShell, articleRichEditor.editor);
    };

    const mountActiveDraftControls = (type) => {
        const fields = draftFields[type];

        if (!fields) {
            return;
        }

        mountDraftControl(titleFieldShell, fields.title);
        if (type === "article") {
            mountArticleBodyControl();
        } else {
            mountDraftControl(captionFieldShell, fields.caption);
        }
        if (fields.article_caption) {
            mountDraftControl(articleCaptionFieldShell, fields.article_caption);
        }
        mountDraftControl(hashtagsFieldShell, fields.hashtags, draftHashtagShells[type]);
    };

    const loadMasterValuesIntoDraft = (type) => {
        const fields = draftFields[type];

        if (!fields) {
            return;
        }

        fields.title.value = titleInput?.value || "";
        fields.caption.value = captionInput?.value || "";
        if (fields.article_caption) {
            fields.article_caption.value = articleCaptionInput?.value || "";
        }
        fields.hashtags.value = hashtagsInput?.value || "";
        fields.hashtags.dispatchEvent(
            new CustomEvent("create-post:replace-tags", {
                detail: { value: fields.hashtags.value },
            }),
        );
        if (type === "article") {
            articleRichEditor.updateFromSource();
        }
    };

    const clearAllDraftControls = () => {
        Object.values(draftFields).forEach((fields) => {
            if (fields.title) {
                fields.title.value = "";
            }
            if (fields.caption) {
                fields.caption.value = "";
            }
            if (fields.article_caption) {
                fields.article_caption.value = "";
            }
            if (fields.hashtags) {
                fields.hashtags.value = "";
                fields.hashtags.dispatchEvent(
                    new CustomEvent("create-post:replace-tags", {
                        detail: { value: "" },
                    }),
                );
            }
        });

        if (titleInput) {
            titleInput.value = "";
        }
        if (captionInput) {
            captionInput.value = "";
        }
        if (articleCaptionInput) {
            articleCaptionInput.value = "";
        }
        if (hashtagsInput) {
            hashtagsInput.value = "";
        }
        articleRichEditor.updateFromSource();
    };

    const syncActiveDraftToMaster = () => {
        const fields = getActiveDraftFields();
        if (currentPostType === "article") {
            articleRichEditor.syncSource();
        }
        syncActiveDraftHashtags();

        enforceCharacterLimit(fields.title);
        if (currentPostType !== "article") {
            enforceCharacterLimit(fields.caption);
        }
        enforceCharacterLimit(fields.article_caption);

        if (titleInput) {
            titleInput.value = fields.title?.value || "";
            enforceCharacterLimit(titleInput);
        }
        if (captionInput) {
            captionInput.value = fields.caption?.value || "";
            if (currentPostType !== "article") {
                enforceCharacterLimit(captionInput);
            }
        }
        if (articleCaptionInput) {
            articleCaptionInput.value = currentPostType === "article"
                ? fields.article_caption?.value || ""
                : "";
            enforceCharacterLimit(articleCaptionInput);
        }
        if (hashtagsInput) {
            hashtagsInput.value = fields.hashtags?.value || "";
        }
        if (contentFormatInput && typeToFormat[currentPostType]) {
            contentFormatInput.value = typeToFormat[currentPostType];
        }
    };

    hideMasterField(titleInput);
    hideMasterField(captionInput);
    hideMasterField(articleCaptionInput);
    if (hashtagsInput) {
        hashtagsInput.dataset.skipTagInput = "true";
    }
    hideMasterField(hashtagsInput);
    initializeDraftHashtags();
    Object.values(draftFields).forEach((fields) => {
        [fields.title, fields.caption, fields.article_caption].forEach((field) => {
            if (field) {
                hiddenFieldsSlot?.append(field);
            }
        });
    });

    const getSelectedOptionText = (select) =>
        select?.selectedOptions?.[0]?.textContent?.trim() || "";

    const getFileMetadata = (input) =>
        Array.from(input?.files || []).map((file) => ({
            name: file.name,
            size: file.size,
            type: file.type,
        }));

    const hasSelectedFiles = (input) => Boolean(input?.files?.length);

    const hasVisibleExistingPreview = (preview) =>
        Boolean(preview?.dataset.existingSrc && !preview.hidden);

    const normalizeAiValue = (value) =>
        String(value || "").replace(/\r\n?/g, "\n").trim();

    const hasExistingStoredVideo = () =>
        Boolean(postForm?.dataset.postId && videoPreviewWrap?.dataset.existingSrc);

    const getVideoAiSourceState = () => ({
        hasSelectedVideo: Boolean(videoInput?.files?.[0]),
        hasUploadedVideo: Boolean(normalizeAiValue(uploadedVideoObjectNameInput?.value)),
        hasExistingVideo: hasExistingStoredVideo(),
    });

    const getCurrentMediaState = () => {
        if (currentPostType === "video") {
            const fileNames = getFileMetadata(videoInput).map((file) => file.name);
            const videoState = getVideoAiSourceState();
            return {
                has_image: false,
                has_video:
                    videoState.hasSelectedVideo ||
                    videoState.hasUploadedVideo ||
                    videoState.hasExistingVideo,
                file_names: fileNames,
            };
        }

        if (currentPostType === "illustration") {
            const fileNames = getFileMetadata(illustrationImagesInput).map((file) => file.name);
            return {
                has_image: hasSelectedFiles(illustrationImagesInput) || hasVisibleExistingPreview(previewWrapper),
                has_video: false,
                file_names: fileNames,
            };
        }

        if (currentPostType === "article") {
            const fileNames = getFileMetadata(imageInput).map((file) => file.name);
            return {
                has_image: hasSelectedFiles(imageInput) || hasVisibleExistingPreview(articlePreviewWrap),
                has_video: false,
                file_names: fileNames,
            };
        }

        return {
            has_image: false,
            has_video: false,
            file_names: [],
        };
    };

    const getArticleText = () =>
        currentPostType === "article"
            ? normalizeAiValue(articleRichEditor.getText() || draftFields.article.caption?.value)
            : "";

    const hasAiSourceContent = () => {
        const fields = getActiveDraftFields();
        if (currentPostType === "video") {
            const videoState = getVideoAiSourceState();
            return Boolean(
                normalizeAiValue(fields.title?.value) ||
                normalizeAiValue(fields.caption?.value) ||
                normalizeAiValue(fields.hashtags?.value) ||
                videoState.hasSelectedVideo ||
                videoState.hasUploadedVideo ||
                videoState.hasExistingVideo
            );
        }
        const mediaState = getCurrentMediaState();
        return Boolean(
                normalizeAiValue(fields.title?.value) ||
                normalizeAiValue(fields.caption?.value) ||
                normalizeAiValue(fields.article_caption?.value) ||
                normalizeAiValue(fields.hashtags?.value) ||
                getFirstAiImageFile() ||
                mediaState.has_video,
        );
    };

    const hasTextAiSourceContent = () => {
        const fields = getActiveDraftFields();
        return Boolean(
            normalizeAiValue(fields.title?.value) ||
            normalizeAiValue(fields.caption?.value) ||
            normalizeAiValue(fields.article_caption?.value) ||
            normalizeAiValue(fields.hashtags?.value) ||
            normalizeAiValue(getArticleText())
        );
    };

    const getAiValidationMessage = () => {
        return "Please add a short description, title, or caption before using AI feedback.";
    };

    const getAiFieldInput = (fieldShell, feedbackType) => {
        const fields = getActiveDraftFields();

        if (feedbackType === "title") {
            return fields.title;
        }

        if (feedbackType === "hashtags") {
            return fields.hashtags;
        }

        if (currentPostType === "article" && fieldShell === articleCaptionFieldShell) {
            return fields.article_caption;
        }

        return fields.caption;
    };

    const getAiResultAnchor = (input) => {
        if (!input) {
            return null;
        }

        if (
            (input.id === "id_hashtags" || input.dataset.hashtagDraft === "true") &&
            input.nextElementSibling?.classList.contains("hashtag-input-shell")
        ) {
            return input.nextElementSibling;
        }

        return input;
    };

    const removeAiError = (fieldShell) => {
        fieldShell?.querySelector(".create-post-ai-error[data-field-error]")?.remove();
    };

    const clearAiFieldState = (fieldShell) => {
        removeAiError(fieldShell);
        fieldShell?.querySelector(".create-post-ai-result")?.remove();
    };

    const clearAllAiFieldState = () => {
        document
            .querySelectorAll("[data-ai-field]")
            .forEach((fieldShell) => clearAiFieldState(fieldShell));
    };

    const renderAiFieldError = (fieldShell, message) => {
        clearAiFieldState(fieldShell);
        const error = document.createElement("p");
        error.className = "create-post-ai-error";
        error.dataset.fieldError = "true";
        error.textContent = message;
        const input = getAiFieldInput(fieldShell, fieldShell?.dataset.aiField);
        const anchor = getAiResultAnchor(input);
        if (anchor) {
            anchor.insertAdjacentElement("afterend", error);
        } else {
            fieldShell?.append(error);
        }
    };

    const renderAiResult = (fieldShell, feedbackType, state) => {
        const input = getAiFieldInput(fieldShell, feedbackType);
        const anchor = getAiResultAnchor(input);
        if (!fieldShell || !anchor) {
            return null;
        }

        fieldShell.querySelector(".create-post-ai-result")?.remove();

        const resultBox = document.createElement("div");
        resultBox.className = "create-post-ai-result";
        resultBox.dataset.feedbackType = feedbackType;
        if (state.loading) {
            resultBox.classList.add("is-loading");
        }

        const badge = document.createElement("span");
        badge.className = "create-post-ai-badge";
        badge.textContent = "AI Feedback";

        const suggestion = document.createElement("p");
        suggestion.className = "create-post-ai-suggestion";
        suggestion.textContent = state.loading ? "Generating suggestion..." : state.suggestion;

        const explanation = document.createElement("p");
        explanation.className = "create-post-ai-explanation";
        explanation.textContent = state.loading ? "Reviewing your post context." : state.explanation;

        resultBox.append(badge, suggestion, explanation);
        if (!state.loading && state.mediaNotice) {
            const mediaNotice = document.createElement("p");
            mediaNotice.className = "create-post-ai-explanation create-post-ai-media-notice";
            mediaNotice.textContent = state.mediaNotice;
            resultBox.append(mediaNotice);
        }

        if (!state.loading) {
            const actions = document.createElement("div");
            actions.className = "create-post-ai-actions";

            const applyButton = document.createElement("button");
            applyButton.className = "btn btn-primary";
            applyButton.type = "button";
            applyButton.textContent = "Apply";
            applyButton.addEventListener("click", () => {
                input.value = state.suggestion;
                if (input.id === "id_hashtags" || input.dataset.hashtagDraft === "true") {
                    input.dispatchEvent(
                        new CustomEvent("create-post:replace-tags", {
                            detail: { value: state.suggestion },
                        }),
                    );
                }
                input.dispatchEvent(new Event("input", { bubbles: true }));
                input.focus();
            });

            const regenerateButton = document.createElement("button");
            regenerateButton.className = "btn btn-secondary";
            regenerateButton.type = "button";
            regenerateButton.textContent = "Regenerate";
            regenerateButton.addEventListener("click", () => {
                requestAiFeedback(fieldShell, feedbackType);
            });

            actions.append(applyButton, regenerateButton);
            resultBox.append(actions);
        }

        anchor.insertAdjacentElement("afterend", resultBox);
        return resultBox;
    };

    const setAiButtonLoading = (button, isLoading) => {
        if (!button) {
            return;
        }

        button.disabled = isLoading;
        button.textContent = isLoading ? "Generating..." : "AI Feedback";
    };

    const buildAiPayload = (fieldShell, feedbackType) => {
        const input = getAiFieldInput(fieldShell, feedbackType);
        const fields = getActiveDraftFields();
        const selectedVideo = currentPostType === "video" ? videoInput?.files?.[0] : null;
        const useTextOnlyVideoFallback = Boolean(selectedVideo && selectedVideo.size > geminiVideoMaxBytes);
        const aiPostType =
            currentPostType === "illustration"
                ? "photo_post"
                : currentPostType === "article"
                    ? "article"
                    : currentPostType === "video"
                        ? "video"
                        : currentPostType;
        return {
            feedback_type: feedbackType,
            post_id: postForm?.dataset.postId || "",
            current_value: normalizeAiValue(input?.value),
            post_type: aiPostType,
            article_text: getArticleText(),
            campaign: campaignInput?.value ? getSelectedOptionText(campaignInput) : "",
            title: normalizeAiValue(fields.title?.value),
            caption: normalizeAiValue(fields.caption?.value),
            article_caption: normalizeAiValue(fields.article_caption?.value),
            hashtags: normalizeAiValue(fields.hashtags?.value),
            uploaded_video_object_name: normalizeAiValue(uploadedVideoObjectNameInput?.value),
            video_analysis_requested: currentPostType === "video" && !useTextOnlyVideoFallback,
            video_fallback_reason: useTextOnlyVideoFallback ? "video_too_large" : "",
            use_text_only_fallback: useTextOnlyVideoFallback,
            video_duration_seconds: Number.isFinite(selectedVideoDurationSeconds)
                ? selectedVideoDurationSeconds
                : Number.isFinite(previewVideo?.duration)
                    ? previewVideo.duration
                : null,
        };
    };

    const getFirstAiImageFile = () => {
        if (currentPostType === "illustration" && selectedIllustrationFiles.length) {
            return selectedIllustrationFiles[0];
        }
        if (currentPostType === "article" && selectedArticleImageFile) {
            return selectedArticleImageFile;
        }
        return null;
    };

    const createAiImageThumbnail = async (file) => {
        if (!file?.type?.startsWith("image/")) {
            return null;
        }

        try {
            const bitmap = await createImageBitmap(file);
            const maxDimension = 1280;
            const scale = Math.min(1, maxDimension / Math.max(bitmap.width, bitmap.height));
            const canvas = document.createElement("canvas");
            canvas.width = Math.max(1, Math.round(bitmap.width * scale));
            canvas.height = Math.max(1, Math.round(bitmap.height * scale));
            canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
            bitmap.close();
            return await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.75));
        } catch (error) {
            console.warn("AI image thumbnail could not be prepared; using text context.", error);
            return null;
        }
    };

    const isSupportedAiImageFile = (file) =>
        ["image/jpeg", "image/png", "image/webp"].includes(file?.type || "");

    const aiImageErrorMessages = {
            image_input_unavailable: "AI did not receive a valid image. Please reselect the image and try again.",
            unsupported_image: "This image format is not supported. Please use JPG, PNG, or WebP.",
            invalid_image: "The image file is invalid or corrupted.",
            empty_image: "The image file is empty. Please choose another image.",
            image_too_large: "The image file is too large. Please choose a smaller image.",
            image_analysis_failed: "AI could not analyze the image, so no suggestion was generated.",
    };

    const getAiImageUnavailableMessage = (code) => {
        return aiImageErrorMessages[code] || "The image could not be provided to AI. Please reselect the image and try again.";
    };

    const getAiImageErrorMessage = (code) => {
        return aiImageErrorMessages[code] || "";
    };

    const buildAiRequestBody = async (fieldShell, feedbackType) => {
        const payload = buildAiPayload(fieldShell, feedbackType);
        const imageFile = getFirstAiImageFile();
        if (imageFile) {
            const thumbnail = await createAiImageThumbnail(imageFile);
            if (thumbnail) {
                const body = new FormData();
                body.append("payload", JSON.stringify(payload));
                body.append("image", thumbnail, "ai-feedback-preview.jpg");
                return {
                    body,
                    headers: {},
                };
            }
            if (!isSupportedAiImageFile(imageFile)) {
                throw new Error(getAiImageUnavailableMessage("unsupported_image"));
            }
            if (!imageFile.size) {
                throw new Error(getAiImageUnavailableMessage("empty_image"));
            }
            if (imageFile.size > 2 * 1024 * 1024) {
                throw new Error(getAiImageUnavailableMessage("image_too_large"));
            }
            const body = new FormData();
            body.append("payload", JSON.stringify(payload));
            body.append("image", imageFile, imageFile.name || "ai-feedback-image");
            return {
                body,
                headers: {},
            };
        }
        const selectedVideo = currentPostType === "video" ? videoInput?.files?.[0] : null;
        const useTextOnlyVideoFallback = Boolean(selectedVideo && selectedVideo.size > geminiVideoMaxBytes);
        if (selectedVideo && !directVideoUploadEnabled && !useTextOnlyVideoFallback) {
            const body = new FormData();
            body.append("payload", JSON.stringify(payload));
            body.append("video", selectedVideo, selectedVideo.name);
            return { body, headers: {} };
        }
        return {
            body: JSON.stringify(payload),
            headers: { "Content-Type": "application/json" },
        };
    };

    async function requestAiFeedback(fieldShell, feedbackType, triggerButton = null) {
        if (
            typeof window.requireAiMembershipBeforeAction === "function" &&
            !window.requireAiMembershipBeforeAction()
        ) {
            return;
        }

        syncActiveDraftToMaster();
        clearAiFieldState(fieldShell);

        if (currentPostType === "video") {
            const selectedVideo = videoInput?.files?.[0];
            const videoState = getVideoAiSourceState();
            const hasStoredVideo = videoState.hasUploadedVideo || videoState.hasExistingVideo;
            if (!selectedVideo && !hasStoredVideo) {
                renderAiFieldError(fieldShell, "Please finish uploading a video before using AI feedback.");
                return;
            }
            const durationError = getSelectedVideoDurationError();
            if (durationError) {
                renderAiFieldError(fieldShell, durationError);
                return;
            }
            const useTextOnlyVideoFallback = Boolean(selectedVideo && selectedVideo.size > geminiVideoMaxBytes);
            if (useTextOnlyVideoFallback && !hasTextAiSourceContent()) {
                renderAiFieldError(fieldShell, videoTextFallbackMessage);
                return;
            }
            if (selectedVideo && directVideoUploadEnabled && !uploadedVideoObjectNameInput?.value && !useTextOnlyVideoFallback) {
                if (videoUploadInProgress) {
                    renderAiFieldError(fieldShell, "Please wait for the video upload to finish.");
                    return;
                }
                videoUploadInProgress = true;
                setAiButtonLoading(triggerButton, true);
                renderAiResult(fieldShell, feedbackType, { loading: true });
                if (videoPreviewNote) {
                    videoPreviewNote.hidden = false;
                    videoPreviewNote.textContent = "Uploading video securely before AI analysis...";
                }
                try {
                    uploadedVideoObjectNameInput.value = await uploadVideoDirectly(selectedVideo);
                    videoInput.value = "";
                    if (videoPreviewNote) {
                        videoPreviewNote.textContent = "Video uploaded securely. Starting AI analysis...";
                    }
                } catch (error) {
                    renderAiFieldError(fieldShell, getVideoUploadErrorMessage(error));
                    setAiButtonLoading(triggerButton, false);
                    videoUploadInProgress = false;
                    activeVideoUploadController = null;
                    return;
                }
                videoUploadInProgress = false;
                activeVideoUploadController = null;
            }
        }

        if (!hasAiSourceContent()) {
            renderAiFieldError(fieldShell, getAiValidationMessage());
            return;
        }

        const endpoint = postForm?.dataset.aiFeedbackUrl;
        if (!endpoint) {
            renderAiFieldError(fieldShell, "AI feedback is unavailable on this page.");
            console.warn("Create Post AI feedback endpoint is missing.");
            return;
        }

        setAiButtonLoading(triggerButton, true);
        renderAiResult(fieldShell, feedbackType, { loading: true });

        try {
            const requestBody = await buildAiRequestBody(fieldShell, feedbackType);
            const response = await fetch(endpoint, {
                method: "POST",
                headers: {
                    "X-CSRFToken": getCookieValue("csrftoken"),
                    ...requestBody.headers,
                },
                body: requestBody.body,
            });
            const data = await response.json().catch(() => ({}));

            if (response.status === 403) {
                clearAiFieldState(fieldShell);
                if (typeof window.openAiMembershipModal === "function") {
                    window.openAiMembershipModal();
                } else {
                    alert("AI features are available for members only. Would you like to upgrade your plan?");
                }
                return;
            }

            if (!response.ok) {
                throw new Error(data.message || getAiImageErrorMessage(data.error) || data.error || "AI feedback could not be generated.");
            }

            if (currentPostType === "illustration" && data.used_image_input !== true) {
                throw new Error(getAiImageUnavailableMessage(data.error || "image_input_unavailable"));
            }

            renderAiResult(fieldShell, feedbackType, {
                suggestion: data.suggestion || "",
                explanation: data.explanation || "",
                mediaNotice: data.media_notice || "",
            });
        } catch (error) {
            fieldShell?.querySelector(".create-post-ai-result")?.remove();
            renderAiFieldError(fieldShell, error.message || "AI feedback could not be generated.");
        } finally {
            setAiButtonLoading(triggerButton, false);
        }
    }

    const clearScheduleValidation = () => {
        [scheduleDate, scheduleTime].forEach((input) =>
            input?.classList.remove("input-error"),
        );
    };

    const setScheduleMessage = (message, state = "") => {
        if (!scheduleMessage) {
            return;
        }

        scheduleMessage.textContent = message;
        scheduleMessage.className = "schedule-message";

        if (state) {
            scheduleMessage.classList.add(`is-${state}`);
        }
    };

    const setScheduledForValue = () => {
        if (!scheduledForInput) {
            return;
        }

        if (scheduleDate?.value && scheduleTime?.value) {
            scheduledForInput.value = `${scheduleDate.value}T${scheduleTime.value}`;
        } else {
            scheduledForInput.value = "";
        }
    };

    const hideSchedulePanel = () => {
        if (!schedulePanel || !scheduleButton) {
            return;
        }

        schedulePanel.hidden = true;
        scheduleButton.setAttribute("aria-expanded", "false");
    };

    const showSchedulePanel = () => {
        if (!schedulePanel || !scheduleButton) {
            return;
        }

        schedulePanel.hidden = false;
        scheduleButton.setAttribute("aria-expanded", "true");
    };

    const setArticlePreview = (src, message = "") => {
        if (!articlePreviewWrap || !articlePreview) {
            return;
        }

        articlePreviewWrap.hidden = false;
        articlePreview.src = src;
        if (articleUploadState) {
            articleUploadState.hidden = true;
        }
        if (articlePreviewNote) {
            articlePreviewNote.hidden = !message;
            articlePreviewNote.textContent = message;
        }
    };

    const setVideoPreview = (src, message = "") => {
        if (!videoPreviewWrap || !previewVideo) {
            return;
        }

        videoPreviewWrap.hidden = false;
        previewVideo.closest(".video-upload-preview-frame")?.classList.add("video-upload-preview-frame");
        previewVideo.classList.add("video-upload-preview-media");
        previewVideo.src = src;
        if (videoUploadState) {
            videoUploadState.hidden = true;
        }
        if (videoPreviewNote) {
            videoPreviewNote.hidden = !message;
            videoPreviewNote.textContent = message;
        }
    };

    const setUploadError = (message = "") => {
        if (!uploadError) {
            return;
        }

        uploadError.hidden = !message;
        uploadError.textContent = message;
    };

    const setSubmitButtonsDisabled = (isDisabled) => {
        postForm?.querySelectorAll('button[type="submit"]').forEach((button) => {
            button.disabled = isDisabled;
        });
    };

    const resetSelectedVideoDuration = () => {
        selectedVideoDurationSeconds = null;
        selectedVideoDurationReady = false;
        selectedVideoDurationError = "";
        if (uploadedVideoDurationInput) {
            uploadedVideoDurationInput.value = "";
        }
    };

    const clearVideoThumbnailInput = () => {
        videoThumbnailGenerationId += 1;
        if (videoThumbnailObjectUrl) {
            URL.revokeObjectURL(videoThumbnailObjectUrl);
            videoThumbnailObjectUrl = null;
        }
        videoThumbnailGenerationPromise = null;
        if (videoThumbnailInput) {
            videoThumbnailInput.value = "";
        }
    };

    const waitForVideoEvent = (video, eventName, timeoutMs = 5000) =>
        new Promise((resolve, reject) => {
            const timeout = window.setTimeout(() => {
                cleanup();
                reject(new Error(`${eventName} timed out`));
            }, timeoutMs);
            const cleanup = () => {
                window.clearTimeout(timeout);
                video.removeEventListener(eventName, handleEvent);
                video.removeEventListener("error", handleError);
            };
            const handleEvent = () => {
                cleanup();
                resolve();
            };
            const handleError = () => {
                cleanup();
                reject(new Error("Video thumbnail source could not be read."));
            };
            video.addEventListener(eventName, handleEvent, { once: true });
            video.addEventListener("error", handleError, { once: true });
        });

    const canvasToBlob = (canvas, type, quality) =>
        new Promise((resolve) => canvas.toBlob(resolve, type, quality));

    const setVideoThumbnailFile = (blob, extension) => {
        if (!videoThumbnailInput || !blob) {
            return false;
        }
        const transfer = new DataTransfer();
        const file = new File([blob], `video-thumbnail.${extension}`, {
            type: blob.type || (extension === "webp" ? "image/webp" : "image/jpeg"),
            lastModified: Date.now(),
        });
        transfer.items.add(file);
        videoThumbnailInput.files = transfer.files;
        return true;
    };

    const generateVideoThumbnail = async (file, generationId) => {
        if (!file?.type?.startsWith("video/") || !videoThumbnailInput) {
            return false;
        }

        const video = document.createElement("video");
        video.muted = true;
        video.playsInline = true;
        video.preload = "metadata";
        video.crossOrigin = "anonymous";
        const objectUrl = URL.createObjectURL(file);
        videoThumbnailObjectUrl = objectUrl;
        video.src = objectUrl;

        try {
            await waitForVideoEvent(video, "loadedmetadata", 7000);
            const duration = Number(video.duration);
            const seekTime = Number.isFinite(duration) && duration > 2
                ? 1
                : Math.max(0, duration * 0.25 || 0);
            if (seekTime > 0) {
                video.currentTime = Math.min(seekTime, Math.max(duration - 0.05, 0));
                await waitForVideoEvent(video, "seeked", 7000);
            } else if (video.readyState < 2) {
                await waitForVideoEvent(video, "loadeddata", 7000);
            }

            const sourceWidth = video.videoWidth;
            const sourceHeight = video.videoHeight;
            if (!sourceWidth || !sourceHeight) {
                throw new Error("Video dimensions are unavailable.");
            }

            const maxDimension = 720;
            const scale = Math.min(1, maxDimension / Math.max(sourceWidth, sourceHeight));
            const canvas = document.createElement("canvas");
            canvas.width = Math.max(1, Math.round(sourceWidth * scale));
            canvas.height = Math.max(1, Math.round(sourceHeight * scale));
            canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);

            if (generationId !== videoThumbnailGenerationId) {
                return false;
            }

            const webpBlob = await canvasToBlob(canvas, "image/webp", 0.8);
            if (generationId === videoThumbnailGenerationId && webpBlob && setVideoThumbnailFile(webpBlob, "webp")) {
                return true;
            }
            const jpegBlob = await canvasToBlob(canvas, "image/jpeg", 0.82);
            if (generationId === videoThumbnailGenerationId && jpegBlob && setVideoThumbnailFile(jpegBlob, "jpg")) {
                return true;
            }
            throw new Error("Canvas thumbnail export failed.");
        } catch (error) {
            if (generationId === videoThumbnailGenerationId && videoThumbnailInput) {
                videoThumbnailInput.value = "";
            }
            console.warn("Video thumbnail could not be created; placeholder will be used.", error);
            if (videoPreviewNote) {
                videoPreviewNote.hidden = false;
                videoPreviewNote.textContent = "Thumbnail could not be created; placeholder will be used.";
            }
            return false;
        } finally {
            video.removeAttribute("src");
            video.load();
            if (videoThumbnailObjectUrl === objectUrl) {
                URL.revokeObjectURL(objectUrl);
                videoThumbnailObjectUrl = null;
            }
        }
    };

    const beginVideoThumbnailGeneration = (file) => {
        if (!file) {
            clearVideoThumbnailInput();
            return null;
        }
        clearVideoThumbnailInput();
        const generationId = videoThumbnailGenerationId;
        const thumbnailPromise = generateVideoThumbnail(file, generationId).finally(() => {
            if (videoThumbnailGenerationPromise === thumbnailPromise) {
                videoThumbnailGenerationPromise = null;
            }
        });
        videoThumbnailGenerationPromise = thumbnailPromise;
        return videoThumbnailGenerationPromise;
    };

    const validateDurationNumber = (duration) => {
        if (!Number.isFinite(duration) || duration <= 0) {
            return videoDurationUnreadableMessage;
        }
        if (duration > videoMaxDurationSeconds + videoDurationToleranceSeconds) {
            return videoTooLongMessage;
        }
        return "";
    };

    const rejectSelectedVideo = (message) => {
        if (videoObjectUrl) {
            URL.revokeObjectURL(videoObjectUrl);
            videoObjectUrl = null;
        }
        clearVideoPreview();
        setUploadError(message);
        showValidationToast(message);
        setSubmitButtonsDisabled(false);
    };

    const readSelectedVideoDuration = (file) => {
        resetSelectedVideoDuration();
        selectedVideoDurationError = videoDurationUnreadableMessage;
        setSubmitButtonsDisabled(true);
        if (videoPreviewNote) {
            videoPreviewNote.hidden = false;
            videoPreviewNote.textContent = "Reading video duration...";
        }

        const complete = () => {
            const duration = Number(previewVideo?.duration);
            const errorMessage = validateDurationNumber(duration);
            if (errorMessage) {
                selectedVideoDurationError = errorMessage;
                rejectSelectedVideo(errorMessage);
                return;
            }
            selectedVideoDurationSeconds = duration;
            selectedVideoDurationReady = true;
            selectedVideoDurationError = "";
            if (uploadedVideoDurationInput) {
                uploadedVideoDurationInput.value = String(duration);
            }
            if (videoPreviewNote) {
                videoPreviewNote.hidden = false;
                videoPreviewNote.textContent = "1 video selected";
            }
            setSubmitButtonsDisabled(false);
        };

        const fail = () => {
            selectedVideoDurationError = videoDurationUnreadableMessage;
            rejectSelectedVideo(videoDurationUnreadableMessage);
        };

        if (!file || !previewVideo) {
            fail();
            return;
        }
        if (previewVideo.readyState >= 1) {
            complete();
            return;
        }
        previewVideo.addEventListener("loadedmetadata", complete, { once: true });
        previewVideo.addEventListener("error", fail, { once: true });
    };

    const revokeIllustrationObjectUrls = () => {
        illustrationObjectUrls.forEach((url) => URL.revokeObjectURL(url));
        illustrationObjectUrls = [];
    };

    const syncIllustrationInput = () => {
        if (!illustrationImagesInput) {
            return;
        }

        const transfer = new DataTransfer();
        selectedIllustrationFiles.forEach((file) => transfer.items.add(file));
        illustrationImagesInput.files = transfer.files;
    };

    const getIllustrationFileKey = (file) => `${file.name}-${file.size}-${file.lastModified}`;

    const openIllustrationFilePicker = () => {
        if (!illustrationImagesInput) {
            return;
        }

        illustrationImagesInput.value = "";
        illustrationImagesInput.click();
    };

    const renderIllustrationPreviews = () => {
        if (!previewWrapper || !previewGrid) {
            return;
        }

        revokeIllustrationObjectUrls();
        previewWrapper.hidden = selectedIllustrationFiles.length === 0;
        previewGrid.hidden = selectedIllustrationFiles.length === 0;
        previewGrid.replaceChildren();

        if (uploadState) {
            uploadState.hidden = selectedIllustrationFiles.length > 0;
        }

        if (previewNote) {
            previewNote.hidden = selectedIllustrationFiles.length === 0;
            previewNote.textContent = selectedIllustrationFiles.length
                ? `${selectedIllustrationFiles.length} image${selectedIllustrationFiles.length === 1 ? "" : "s"} selected.`
                : "";
        }

        selectedIllustrationFiles.forEach((file, index) => {
            const item = document.createElement("div");
            item.className = "multi-image-preview-item";

            const image = document.createElement("img");
            image.alt = file.name || `Selected image ${index + 1}`;
            const objectUrl = URL.createObjectURL(file);
            illustrationObjectUrls.push(objectUrl);
            image.src = objectUrl;

            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "multi-image-remove";
            remove.setAttribute("aria-label", `Remove ${file.name || `image ${index + 1}`}`);
            remove.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">close</span>';
            remove.addEventListener("click", () => {
                selectedIllustrationFiles.splice(index, 1);
                syncIllustrationInput();
                renderIllustrationPreviews();
                setUploadError("");
            });

            item.append(image, remove);
            previewGrid.append(item);
        });
    };

    const clearArticlePreview = () => {
        if (articlePreviewWrap) {
            articlePreviewWrap.hidden = true;
        }

        if (articleUploadState) {
            articleUploadState.hidden = false;
        }

        if (articlePreview) {
            articlePreview.removeAttribute("src");
        }

        if (articlePreviewNote) {
            articlePreviewNote.hidden = true;
            articlePreviewNote.textContent = "";
        }

        if (imageInput) {
            imageInput.value = "";
        }
        selectedArticleImageFile = null;
    };

    const clearVideoPreview = () => {
        if (videoUploadInProgress && activeVideoUploadController) {
            activeVideoUploadController.abort();
        }

        if (videoPreviewWrap) {
            videoPreviewWrap.hidden = true;
        }

        if (videoUploadState) {
            videoUploadState.hidden = false;
        }

        if (previewVideo) {
            previewVideo.removeAttribute("src");
        }

        if (videoPreviewNote) {
            videoPreviewNote.hidden = true;
            videoPreviewNote.textContent = "";
        }

        if (videoInput) {
            videoInput.value = "";
        }
        if (uploadedVideoObjectNameInput) {
            uploadedVideoObjectNameInput.value = "";
        }
        resetSelectedVideoDuration();
        clearVideoThumbnailInput();
    };

    const clearIllustrationPreview = () => {
        if (previewWrapper) {
            previewWrapper.hidden = true;
        }
        if (uploadState) {
            uploadState.hidden = false;
        }
        if (previewGrid) {
            previewGrid.hidden = true;
            previewGrid.replaceChildren();
        }
        if (previewNote) {
            previewNote.hidden = true;
            previewNote.textContent = "";
        }
        if (illustrationImagesInput) {
            selectedIllustrationFiles = [];
            syncIllustrationInput();
        }
        revokeIllustrationObjectUrls();
        setUploadError("");
    };

    const restoreFieldCopy = () => {
        if (titleLabel) {
            titleLabel.textContent = uiLabels.title;
        }

        if (titleInput) {
            titleInput.placeholder = defaultTitlePlaceholder;
        }

        if (captionLabel) {
            captionLabel.textContent = uiLabels.captionBody;
        }

        if (captionInput) {
            captionInput.placeholder = defaultCaptionPlaceholder;
        }
    };

    const setFieldAiLinkVisible = (fieldShell, isVisible) => {
        const link = fieldShell?.querySelector(".create-post-ai-link");
        if (link) {
            link.hidden = !isVisible;
        }
    };

    const placeFieldShell = (shell, target) => {
        if (shell && target && shell.parentElement !== target) {
            target.append(shell);
        }
    };

    const placeFieldShellBefore = (shell, sibling) => {
        if (shell && sibling?.parentElement && shell.nextElementSibling !== sibling) {
            sibling.parentElement.insertBefore(shell, sibling);
        }
    };

    const syncEditorLayout = (type) => {
        restoreFieldCopy();

        if (!titleFieldShell || !captionFieldShell || !hiddenFieldsSlot) {
            return;
        }

        if (mediaUploadCard) {
            mediaUploadCard.hidden = type === "draft" || type === "article";
        }

        if (articleMediaPanel) {
            articleMediaPanel.hidden = type !== "article";
        }

        if (illustrationMediaPanel) {
            illustrationMediaPanel.hidden = type !== "illustration";
        }

        if (videoMediaPanel) {
            videoMediaPanel.hidden = type !== "video";
        }

        if (imageInput) {
            imageInput.disabled = type !== "article";
            imageInput.accept = "image/*";
        }

        if (videoInput) {
            videoInput.disabled = type !== "video";
            videoInput.hidden = type !== "video";
            videoInput.accept = "video/mp4,video/webm,video/quicktime,video/*";
        }

        if (illustrationImagesInput) {
            illustrationImagesInput.disabled = type !== "illustration";
            illustrationImagesInput.hidden = type !== "illustration";
        }

        if (type === "article") {
            if (captionLabel) {
                captionLabel.textContent = uiLabels.article;
            }

            if (draftFields.article.caption) {
                draftFields.article.caption.placeholder = uiLabels.writeArticle;
            }

            placeFieldShell(captionFieldShell, articleBodySlot);
            placeFieldShellBefore(titleFieldShell, articleCaptionFieldShell);
            setFieldAiLinkVisible(titleFieldShell, true);
            setFieldAiLinkVisible(captionFieldShell, false);
            if (articleCaptionFieldShell) {
                articleCaptionFieldShell.hidden = false;
                setFieldAiLinkVisible(articleCaptionFieldShell, true);
            }
            mountActiveDraftControls(type);
            setLabelTarget(captionFieldShell, articleRichEditor.editor);
            return;
        }

        if (type === "draft") {
            clearDraftMount(titleFieldShell);
            clearDraftMount(captionFieldShell);
            clearDraftMount(articleCaptionFieldShell);
            clearDraftMount(hashtagsFieldShell);
            placeFieldShell(titleFieldShell, hiddenFieldsSlot);
            placeFieldShell(captionFieldShell, hiddenFieldsSlot);
            setFieldAiLinkVisible(captionFieldShell, false);
            if (articleCaptionFieldShell) {
                articleCaptionFieldShell.hidden = true;
                setFieldAiLinkVisible(articleCaptionFieldShell, false);
            }
            return;
        }

        if (captionLabel) {
            captionLabel.textContent = uiLabels.caption;
        }

        placeFieldShell(titleFieldShell, hiddenFieldsSlot);
        placeFieldShell(captionFieldShell, sharedCaptionSlot);
        placeFieldShell(titleFieldShell, sharedTitleSlot);
        setFieldAiLinkVisible(captionFieldShell, true);
        if (articleCaptionFieldShell) {
            articleCaptionFieldShell.hidden = true;
            setFieldAiLinkVisible(articleCaptionFieldShell, false);
        }
        mountActiveDraftControls(type);
    };

    const validPostTypes = new Set(["article", "illustration", "video", "draft"]);

    const updatePostTypeUrl = (type) => {
        if (!validPostTypes.has(type) || !window.history?.replaceState) {
            return;
        }

        const url = new URL(window.location.href);
        url.searchParams.set("type", type);
        window.history.replaceState({}, "", url);
    };

    const getRequestedPostType = () => {
        const requestedType = new URLSearchParams(window.location.search).get("type");
        return validPostTypes.has(requestedType) ? requestedType : "";
    };

    const getActivePostType = () => {
        const activeButton = typeButtons.find((button) => button.classList.contains("active"));
        return validPostTypes.has(activeButton?.dataset.type) ? activeButton.dataset.type : "";
    };

    const switchPostType = (type, options = {}) => {
        const { updateUrl = true } = options;
        const selectedType = validPostTypes.has(type) ? type : "article";
        const isDraftView = selectedType === "draft";
        const formatValue = typeToFormat[selectedType] || contentFormatInput.value;

        currentPostType = selectedType;
        clearAllAiFieldState();

        document.body.classList.toggle("is-article-post-type", selectedType === "article");
        document.body.classList.toggle(
            "is-illustration-post-type",
            selectedType === "illustration",
        );
        document.body.classList.toggle("is-video-post-type", selectedType === "video");
        document.body.classList.toggle("is-draft-post-type", isDraftView);

        typeButtons.forEach((button) => {
            const isActive = button.dataset.type === selectedType;
            button.classList.toggle("active", isActive);
            button.setAttribute("aria-selected", String(isActive));
        });

        typePanels.forEach((panel) => {
            const isActive = panel.id === `${selectedType}-section`;
            panel.classList.toggle("active-panel", isActive);
            panel.hidden = !isActive;
        });

        sharedEditorSections.forEach((section) => {
            section.hidden = isDraftView;
        });

        syncEditorLayout(selectedType);

        if (!isDraftView) {
            contentFormatInput.value = formatValue;
        } else {
            hideSchedulePanel();
        }

        if (updateUrl) {
            updatePostTypeUrl(selectedType);
        }
    };

    typeButtons.forEach((button) => {
        button.addEventListener("click", () => {
            switchPostType(button.dataset.type || "article");
        });
    });

    aiFeedbackButtons.forEach((button) => {
        button.addEventListener("click", (event) => {
            if (
                typeof window.requireAiMembershipBeforeAction === "function" &&
                !window.requireAiMembershipBeforeAction(null, event)
            ) {
                return;
            }

            const fieldShell = button.closest("[data-ai-field]");
            requestAiFeedback(fieldShell, button.dataset.aiTarget, button);
        });
    });

    illustrationUploadControl?.addEventListener("click", (event) => {
        if (event.target === illustrationImagesInput) {
            return;
        }

        event.preventDefault();
        openIllustrationFilePicker();
    });

    videoUploadControl?.addEventListener("click", (event) => {
        if (event.target === videoInput) {
            return;
        }

        event.preventDefault();
        videoInput?.click();
    });

    scheduleButton?.addEventListener("click", () => {
        clearScheduleValidation();
        setScheduleMessage("");

        if (schedulePanel?.hidden) {
            showSchedulePanel();
        } else {
            hideSchedulePanel();
        }
    });

    scheduleDate?.addEventListener("input", setScheduledForValue);
    scheduleTime?.addEventListener("input", setScheduledForValue);

    publishButton?.addEventListener("click", () => {
        if (statusInput) {
            statusInput.value = "published";
        }

        clearScheduleValidation();
        setScheduleMessage("");
        hideSchedulePanel();
        setScheduledForValue();
    });

    draftButton?.addEventListener("click", () => {
        if (statusInput) {
            statusInput.value = "draft";
        }

        clearScheduleValidation();
        setScheduleMessage("");
        hideSchedulePanel();
        scheduledForInput.value = "";
    });

    confirmScheduleButton?.addEventListener("click", (event) => {
        clearScheduleValidation();

        if (!scheduleDate?.value || !scheduleTime?.value) {
            event.preventDefault();

            if (!scheduleDate?.value) {
                scheduleDate?.classList.add("input-error");
            }

            if (!scheduleTime?.value) {
                scheduleTime?.classList.add("input-error");
            }

            setScheduleMessage(
                "Choose both a date and time to schedule this post.",
                "error",
            );

            if (statusInput) {
                statusInput.value = "draft";
            }

            showSchedulePanel();
            return;
        }

        const scheduledAt = new Date(`${scheduleDate.value}T${scheduleTime.value}`);
        const now = new Date();

        if (Number.isNaN(scheduledAt.getTime()) || scheduledAt <= now) {
            event.preventDefault();
            scheduleDate.classList.add("input-error");
            scheduleTime.classList.add("input-error");
            setScheduleMessage(
                "Select a future date and time for scheduling.",
                "error",
            );

            if (statusInput) {
                statusInput.value = "draft";
            }

            showSchedulePanel();
            return;
        }

        if (statusInput) {
            statusInput.value = "scheduled";
        }

        setScheduledForValue();
        setScheduleMessage(
            `Post scheduled for ${scheduledAt.toLocaleDateString()} at ${scheduledAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.`,
            "success",
        );
    });

    imageInput?.addEventListener("change", () => {
        const [file] = imageInput.files || [];

        if (articleObjectUrl) {
            URL.revokeObjectURL(articleObjectUrl);
            articleObjectUrl = null;
        }

        if (!file) {
            clearArticlePreview();
            return;
        }

        selectedArticleImageFile = file;
        articleObjectUrl = URL.createObjectURL(file);
        setArticlePreview(articleObjectUrl);
    });

    videoInput?.addEventListener("change", () => {
        const [file] = videoInput.files || [];
        const validVideoExtensions = [".mp4", ".webm", ".mov"];
        const fileName = (file?.name || "").toLowerCase();
        const isValidVideo =
            file &&
            file.type.startsWith("video/") &&
            validVideoExtensions.some((extension) => fileName.endsWith(extension));

        if (videoUploadInProgress && activeVideoUploadController) {
            activeVideoUploadController.abort();
        }

        if (videoObjectUrl) {
            URL.revokeObjectURL(videoObjectUrl);
            videoObjectUrl = null;
        }

        if (!file) {
            clearVideoPreview();
            return;
        }

        if (uploadedVideoObjectNameInput) {
            uploadedVideoObjectNameInput.value = "";
        }
        resetSelectedVideoDuration();

        if (!isValidVideo) {
            videoInput.value = "";
            clearVideoThumbnailInput();
            setUploadError("Upload a supported video file: MP4, WebM, or MOV.");
            return;
        }

        const directUploadMaxBytes = Number(
            postForm?.dataset.directVideoUploadMaxBytes ||
                videoInput?.dataset.directUploadMaxBytes ||
                500 * 1024 * 1024,
        );
        const fallbackMaxBytes = Number(
            videoInput?.dataset.fallbackMaxBytes ||
                videoInput?.dataset.maxBytes ||
                20 * 1024 * 1024,
        );
        const maxBytes = directVideoUploadEnabled
            ? directUploadMaxBytes
            : fallbackMaxBytes;
        if (maxBytes > 0 && file.size > maxBytes) {
            videoInput.value = "";
            clearVideoThumbnailInput();
            setUploadError(`This video is too large. Choose a video smaller than ${Math.floor(maxBytes / 1024 / 1024)} MB.`);
            return;
        }

        videoObjectUrl = URL.createObjectURL(file);
        setVideoPreview(videoObjectUrl, "1 video selected");
        setUploadError("");
        beginVideoThumbnailGeneration(file);
        readSelectedVideoDuration(file);
    });

    const setVideoUploadSubmittingState = (isUploading) => {
        postForm?.querySelectorAll('button[type="submit"]').forEach((button) => {
            button.disabled = isUploading;
        });
        if (videoPreviewNote) {
            videoPreviewNote.hidden = false;
            videoPreviewNote.textContent = isUploading
                ? "Uploading video directly to secure storage..."
                : "Video uploaded. Saving post...";
        }
    };

    const setVideoUploadProgress = (uploadedBytes, totalBytes, message = "") => {
        if (!videoPreviewNote) {
            return;
        }
        const total = Math.max(Number(totalBytes) || 0, 1);
        const percent = Math.min(100, Math.max(0, Math.floor((uploadedBytes / total) * 100)));
        videoPreviewNote.hidden = false;
        videoPreviewNote.textContent = message || `Uploading video: ${percent}%`;
    };

    const sleep = (milliseconds) =>
        new Promise((resolve) => {
            window.setTimeout(resolve, milliseconds);
        });

    const getRetryDelay = (attempt) => {
        const baseDelay = Math.min(16000, 1000 * (2 ** Math.max(attempt - 1, 0)));
        return baseDelay + Math.floor(Math.random() * 500);
    };

    const parseUploadedBytesFromRange = (rangeHeader) => {
        const match = String(rangeHeader || "").match(/bytes=0-(\d+)$/i);
        return match ? Number(match[1]) + 1 : 0;
    };

    const isRetryableUploadError = (error) =>
        error?.name !== "AbortError" &&
        (error?.retryable === true || error instanceof TypeError);

    const getVideoUploadErrorMessage = (error) => {
        if (error?.name === "AbortError") {
            return "Video upload was cancelled.";
        }
        return error?.message || "Video upload failed. Please try again.";
    };

    const getSelectedVideoDurationError = () => {
        if (!videoInput?.files?.[0]) {
            return "";
        }
        if (selectedVideoDurationError) {
            return selectedVideoDurationError;
        }
        if (!selectedVideoDurationReady) {
            return videoDurationUnreadableMessage;
        }
        return validateDurationNumber(selectedVideoDurationSeconds);
    };

    const createUploadError = (message, { retryable = false } = {}) => {
        const error = new Error(message);
        error.retryable = retryable;
        return error;
    };

    const queryUploadStatus = async (uploadUrl, totalBytes, signal) => {
        const response = await fetch(uploadUrl, {
            method: "PUT",
            headers: {
                "Content-Range": `bytes */${totalBytes}`,
            },
            signal,
        });
        if (response.status === 308) {
            return parseUploadedBytesFromRange(response.headers.get("Range"));
        }
        if (response.status === 200 || response.status === 201) {
            return totalBytes;
        }
        if ([404, 410].includes(response.status)) {
            throw createUploadError("Video upload session expired. Please select the video and try again.");
        }
        throw createUploadError("Video upload failed. Check your connection and try again.", {
            retryable: videoUploadRetryStatuses.has(response.status),
        });
    };

    const uploadVideoChunk = async (uploadUrl, file, startByte, signal) => {
        const endByte = Math.min(startByte + videoUploadChunkSize, file.size) - 1;
        const response = await fetch(uploadUrl, {
            method: "PUT",
            headers: {
                "Content-Type": file.type,
                "Content-Range": `bytes ${startByte}-${endByte}/${file.size}`,
            },
            body: file.slice(startByte, endByte + 1),
            signal,
        });
        if (response.status === 308) {
            const uploadedBytes = parseUploadedBytesFromRange(response.headers.get("Range"));
            return Math.max(uploadedBytes, endByte + 1);
        }
        if (response.status === 200 || response.status === 201) {
            return file.size;
        }
        if ([404, 410].includes(response.status)) {
            throw createUploadError("Video upload session expired. Please select the video and try again.");
        }
        throw createUploadError("Video upload failed. Check your connection and try again.", {
            retryable: videoUploadRetryStatuses.has(response.status),
        });
    };

    const uploadVideoChunkWithRetry = async (uploadUrl, file, startByte, signal) => {
        let confirmedBytes = startByte;
        for (let attempt = 0; attempt <= videoUploadMaxRetries; attempt += 1) {
            try {
                return await uploadVideoChunk(uploadUrl, file, confirmedBytes, signal);
            } catch (error) {
                if (error?.name === "AbortError") {
                    throw createUploadError("Video upload was cancelled.");
                }
                if (!isRetryableUploadError(error) || attempt === videoUploadMaxRetries) {
                    throw error;
                }
                setVideoUploadProgress(
                    confirmedBytes,
                    file.size,
                    `Connection interrupted. Retrying upload (${attempt + 1}/${videoUploadMaxRetries})...`,
                );
                await sleep(getRetryDelay(attempt + 1));
                confirmedBytes = Math.max(
                    confirmedBytes,
                    await queryUploadStatus(uploadUrl, file.size, signal),
                );
                if (confirmedBytes >= file.size) {
                    return file.size;
                }
            }
        }
        return confirmedBytes;
    };

    const uploadVideoDirectly = async (file) => {
        const endpoint = postForm?.dataset.videoUploadStartUrl;
        if (!endpoint) {
            throw new Error("Direct video upload is unavailable on this page.");
        }

        activeVideoUploadController = new AbortController();
        const { signal } = activeVideoUploadController;

        const startResponse = await fetch(endpoint, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCookieValue("csrftoken"),
            },
            body: JSON.stringify({
                filename: file.name,
                content_type: file.type,
                size: file.size,
                duration_seconds: selectedVideoDurationSeconds,
            }),
            signal,
        });
        const startData = await startResponse.json().catch(() => ({}));
        if (!startResponse.ok || !startData.upload_url || !startData.object_name) {
            throw new Error(startData.error || "Video upload could not be started.");
        }

        let confirmedBytes = await queryUploadStatus(startData.upload_url, file.size, signal);
        while (confirmedBytes < file.size) {
            confirmedBytes = await uploadVideoChunkWithRetry(
                startData.upload_url,
                file,
                confirmedBytes,
                signal,
            );
            setVideoUploadProgress(confirmedBytes, file.size);
        }
        setVideoUploadProgress(file.size, file.size, "Video upload complete");
        return startData.object_name;
    };

    illustrationImagesInput?.addEventListener("change", () => {
        const incomingFiles = Array.from(illustrationImagesInput.files || []);
        const invalidFiles = incomingFiles.filter((file) => !file.type.startsWith("image/"));

        if (invalidFiles.length) {
            illustrationImagesInput.value = "";
            syncIllustrationInput();
            setUploadError("Only image files can be uploaded for image posts.");
            return;
        }

        const selectedKeys = new Set(selectedIllustrationFiles.map(getIllustrationFileKey));
        const uniqueIncomingFiles = incomingFiles.filter((file) => {
            const key = getIllustrationFileKey(file);
            if (selectedKeys.has(key)) {
                return false;
            }
            selectedKeys.add(key);
            return true;
        });

        if (selectedIllustrationFiles.length + uniqueIncomingFiles.length > maxIllustrationImages) {
            illustrationImagesInput.value = "";
            syncIllustrationInput();
            setUploadError(`Select up to ${maxIllustrationImages} images for one post.`);
            return;
        }

        selectedIllustrationFiles = selectedIllustrationFiles.concat(uniqueIncomingFiles);
        syncIllustrationInput();
        renderIllustrationPreviews();
        setUploadError(uniqueIncomingFiles.length ? "" : "That image has already been selected.");
    });

    replaceArticleButton?.addEventListener("click", () => {
        imageInput?.click();
    });

    removeArticleButton?.addEventListener("click", () => {
        if (!articleObjectUrl && articlePreviewWrap?.dataset.existingSrc) {
            if (articlePreviewNote) {
                articlePreviewNote.hidden = false;
                articlePreviewNote.textContent = "Upload a replacement image to change the saved asset.";
            }
            return;
        }

        if (articleObjectUrl) {
            URL.revokeObjectURL(articleObjectUrl);
            articleObjectUrl = null;
        }

        clearArticlePreview();
    });

    replaceButton?.addEventListener("click", () => {
        openIllustrationFilePicker();
    });

    removeButton?.addEventListener("click", () => {
        clearIllustrationPreview();
    });

    replaceVideoButton?.addEventListener("click", () => {
        videoInput?.click();
    });

    removeVideoButton?.addEventListener("click", () => {
        if (!videoObjectUrl && videoPreviewWrap?.dataset.existingSrc) {
            if (videoPreviewNote) {
                videoPreviewNote.hidden = false;
                videoPreviewNote.textContent = "Upload a replacement video to change the saved asset.";
            }
            return;
        }

        if (videoObjectUrl) {
            URL.revokeObjectURL(videoObjectUrl);
            videoObjectUrl = null;
        }

        clearVideoPreview();
    });

    postForm?.addEventListener("submit", (event) => {
        syncActiveDraftToMaster();

        const selectedVideo = videoInput?.files?.[0];
        if (currentPostType === "video" && selectedVideo) {
            const durationError = getSelectedVideoDurationError();
            if (durationError) {
                event.preventDefault();
                setUploadError(durationError);
                showValidationToast(durationError);
                mediaUploadCard?.scrollIntoView({ behavior: "smooth", block: "center" });
                return;
            }
            if (videoThumbnailGenerationPromise) {
                event.preventDefault();
                setSubmitButtonsDisabled(true);
                if (videoPreviewNote) {
                    videoPreviewNote.hidden = false;
                    videoPreviewNote.textContent = "Preparing video thumbnail...";
                }
                videoThumbnailGenerationPromise
                    .catch(() => false)
                    .finally(() => {
                        setSubmitButtonsDisabled(false);
                        postForm.requestSubmit();
                    });
                return;
            }
        }
        if (
            currentPostType === "video" &&
            directVideoUploadEnabled &&
            selectedVideo &&
            !uploadedVideoObjectNameInput?.value
        ) {
            event.preventDefault();
            if (videoUploadInProgress) {
                return;
            }
            videoUploadInProgress = true;
            setUploadError("");
            setVideoUploadSubmittingState(true);
            uploadVideoDirectly(selectedVideo)
                .then((objectName) => {
                    uploadedVideoObjectNameInput.value = objectName;
                    videoInput.value = "";
                    setVideoUploadSubmittingState(false);
                    postForm.requestSubmit();
                })
                .catch((error) => {
                    setVideoUploadSubmittingState(false);
                    const message = getVideoUploadErrorMessage(error);
                    setUploadError(message);
                    showValidationToast(message);
                    mediaUploadCard?.scrollIntoView({ behavior: "smooth", block: "center" });
                })
                .finally(() => {
                    videoUploadInProgress = false;
                    activeVideoUploadController = null;
                });
            return;
        }

        if (currentPostType === "illustration") {
            syncIllustrationInput();
            if (imageInput) {
                imageInput.disabled = true;
            }
            if (videoInput) {
                videoInput.disabled = true;
            }
        } else if (currentPostType === "video") {
            if (imageInput) {
                imageInput.disabled = true;
            }
            if (illustrationImagesInput) {
                illustrationImagesInput.disabled = true;
            }
        } else if (currentPostType === "article") {
            if (videoInput) {
                videoInput.disabled = true;
            }
            if (illustrationImagesInput) {
                illustrationImagesInput.disabled = true;
            }
        }

        const hasExistingImage = Boolean(previewWrapper?.dataset.existingSrc);
        const hasSelectedImages = selectedIllustrationFiles.length > 0;

        if (currentPostType !== "illustration" || hasExistingImage || hasSelectedImages) {
            return;
        }

        event.preventDefault();
        setUploadError("At least one image is required.");
        showValidationToast("At least one image is required.");
        mediaUploadCard?.scrollIntoView({ behavior: "smooth", block: "center" });
    });

    if (articlePreviewWrap?.dataset.existingSrc) {
        setArticlePreview(articlePreviewWrap.dataset.existingSrc, "Current uploaded image");
    }

    if (previewWrapper?.dataset.existingSrc) {
        previewWrapper.hidden = false;
        if (uploadState) {
            uploadState.hidden = true;
        }
        if (previewNote) {
            previewNote.hidden = false;
            previewNote.textContent = "Current uploaded image";
        }
    }

    if (videoPreviewWrap?.dataset.existingSrc) {
        setVideoPreview(videoPreviewWrap.dataset.existingSrc, "Current uploaded video");
    }

    if (scheduledForInput?.value) {
        showSchedulePanel();
    } else {
        hideSchedulePanel();
    }

    const shouldFocusSchedule =
        window.location.hash === "#schedule-section" ||
        new URLSearchParams(window.location.search).get("focus") === "schedule";
    if (shouldFocusSchedule) {
        showSchedulePanel();
        const scheduleSection = document.getElementById("schedule-section");
        scheduleSection?.classList.add("is-focused-section");
        window.setTimeout(() => {
            scheduleSection?.scrollIntoView({ behavior: "smooth", block: "center" });
        }, 100);
    }

    const requestedInitialType = getRequestedPostType();
    const initialType = requestedInitialType || getActivePostType() || "article";
    currentPostType = initialType;
    if (isEditingPost) {
        loadMasterValuesIntoDraft(initialType);
    } else {
        clearAllDraftControls();
        Object.values(draftFields).forEach((fields) => {
            Object.values(fields).forEach((field) => {
                if (field) {
                    field.value = "";
                }
            });
        });
        articleRichEditor.updateFromSource();
    }
    switchPostType(initialType, {
        updateUrl: Boolean(requestedInitialType),
    });
    setScheduledForValue();
}


function setupHashtagInput() {
    if (!document.body.classList.contains("create-post-page")) {
        return;
    }

    const sourceInput = document.getElementById("id_hashtags");
    const form = sourceInput?.closest("form");

    if (!sourceInput || sourceInput.dataset.skipTagInput === "true") {
        return;
    }

    const tagInput = createHashtagInput(sourceInput);

    if (!form || !tagInput) {
        return;
    }

    form.addEventListener("submit", () => {
        if (tagInput.entryInput.value.trim()) {
            tagInput.addTagsFromValue(tagInput.entryInput.value);
        }

        tagInput.syncSourceInput();
    });
}
function initializeCreatePost() {
    setupCreatePostPage();
    setupHashtagInput();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeCreatePost, { once: true });
} else {
    initializeCreatePost();
}

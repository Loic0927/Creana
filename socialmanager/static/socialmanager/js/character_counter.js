function initializeCharacterCounters(root = document) {
    const fields = Array.from(root.querySelectorAll("[data-character-counter='true']"));

    const getMaxLength = (field) =>
        Number(field.dataset.characterCounterMax || field.getAttribute("maxlength") || 0);

    const normalizeLineEndings = (value) => (value || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");

    const getCounterStatus = (count, maxLength) => {
        if (count >= Math.ceil(maxLength * 0.96)) {
            return "danger";
        }
        if (count >= Math.ceil(maxLength * 0.88)) {
            return "warning";
        }
        return "";
    };

    const findOrCreateCounter = (field) => {
        const counterId = field.id || "";
        let counter = counterId
            ? document.querySelector(`[data-character-counter-for="${counterId}"]`)
            : null;

        if (!counter) {
            counter = document.createElement("p");
            counter.className = "helper-text character-counter";
            counter.setAttribute("aria-live", "polite");
            if (counterId) {
                counter.dataset.characterCounterFor = counterId;
            }
        }

        counter.classList.add("character-counter");
        counter.setAttribute("aria-live", counter.getAttribute("aria-live") || "polite");
        if (counter.previousElementSibling !== field) {
            field.insertAdjacentElement("afterend", counter);
        }
        return counter;
    };

    const enforceLimit = (field, maxLength) => {
        const normalizedValue = normalizeLineEndings(field.value);
        const limitedValue = maxLength ? normalizedValue.slice(0, maxLength) : normalizedValue;
        if (field.value !== limitedValue) {
            field.value = limitedValue;
        }
    };

    fields.forEach((field) => {
        const maxLength = getMaxLength(field);
        if (!maxLength || field.dataset.characterCounterReady === "true") {
            return;
        }

        field.dataset.characterCounterReady = "true";
        field.maxLength = maxLength;
        const counter = findOrCreateCounter(field);

        const updateCounter = () => {
            enforceLimit(field, maxLength);
            const count = Math.min((field.value || "").length, maxLength);
            const status = getCounterStatus(count, maxLength);
            counter.textContent = `${count} / ${maxLength}`;
            counter.hidden = false;
            counter.classList.toggle("is-warning", status === "warning");
            counter.classList.toggle("is-danger", status === "danger");
        };

        field.addEventListener("input", updateCounter);
        field.addEventListener("paste", () => {
            window.setTimeout(updateCounter, 0);
        });
        updateCounter();
    });

    root.querySelectorAll("form").forEach((form) => {
        if (form.dataset.characterCounterSubmitReady === "true") {
            return;
        }
        form.dataset.characterCounterSubmitReady = "true";
        form.addEventListener("submit", () => {
            form.querySelectorAll("[data-character-counter='true']").forEach((field) => {
                const maxLength = getMaxLength(field);
                if (!maxLength) {
                    return;
                }
                enforceLimit(field, maxLength);
                field.dispatchEvent(new Event("input", { bubbles: true }));
            });
        });
    });
}

window.initializeCharacterCounters = initializeCharacterCounters;

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => initializeCharacterCounters(), { once: true });
} else {
    initializeCharacterCounters();
}

(function () {
    function initPublicMegaMenu() {
        const header = document.querySelector("[data-public-header]");
        if (!header) return;

        const panel = header.querySelector("[data-mega-panel]");
        const nav = header.querySelector(".landing-nav");
        const triggers = Array.from(header.querySelectorAll("[data-mega-trigger]"));
        const sections = Array.from(header.querySelectorAll("[data-mega-section]"));
        if (!panel || !triggers.length) return;

        let closeTimer = null;
        let activeKey = null;

        function cancelClose() {
            if (closeTimer) window.clearTimeout(closeTimer);
            closeTimer = null;
        }

        function closeMenu() {
            cancelClose();
            panel.hidden = true;
            activeKey = null;
            triggers.forEach((trigger) => trigger.setAttribute("aria-expanded", "false"));
            sections.forEach((section) => { section.hidden = true; });
        }

        function openMenu(key) {
            cancelClose();
            panel.hidden = false;
            activeKey = key;
            triggers.forEach((trigger) => {
                trigger.setAttribute("aria-expanded", String(trigger.dataset.megaTrigger === key));
            });
            sections.forEach((section) => {
                section.hidden = section.dataset.megaSection !== key;
            });
            positionMenu(key);
        }

        function positionMenu(key) {
            const trigger = triggers.find((item) => item.dataset.megaTrigger === key);
            if (!trigger || !nav || panel.hidden) return;
            const triggerRect = trigger.getBoundingClientRect();
            const navRect = nav.getBoundingClientRect();
            const desiredLeft = triggerRect.left - navRect.left;
            const maximumLeft = Math.max(0, navRect.width - panel.offsetWidth);
            panel.style.left = `${Math.min(Math.max(0, desiredLeft), maximumLeft)}px`;
        }

        function scheduleClose() {
            cancelClose();
            closeTimer = window.setTimeout(closeMenu, 140);
        }

        triggers.forEach((trigger) => {
            const key = trigger.dataset.megaTrigger;
            trigger.addEventListener("mouseenter", () => openMenu(key));
            trigger.addEventListener("focus", () => openMenu(key));
            trigger.addEventListener("click", () => {
                if (trigger.getAttribute("aria-expanded") === "true") closeMenu();
                else openMenu(key);
            });
            trigger.addEventListener("keydown", (event) => {
                if (event.key === "ArrowDown") {
                    event.preventDefault();
                    openMenu(key);
                    panel.querySelector(`[data-mega-section="${key}"] a`)?.focus();
                }
            });
        });

        header.addEventListener("mouseenter", cancelClose);
        header.addEventListener("mouseleave", scheduleClose);
        panel.addEventListener("mouseenter", cancelClose);
        panel.addEventListener("mouseleave", scheduleClose);
        header.addEventListener("focusout", (event) => {
            if (!header.contains(event.relatedTarget)) scheduleClose();
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !panel.hidden) {
                const activeTrigger = triggers.find((trigger) => trigger.getAttribute("aria-expanded") === "true");
                closeMenu();
                activeTrigger?.focus();
            }
        });
        document.addEventListener("click", (event) => {
            if (!header.contains(event.target)) closeMenu();
        });
        window.addEventListener("resize", () => {
            if (activeKey) positionMenu(activeKey);
        });

        const mobileNav = header.querySelector(".public-mobile-nav");
        const mobileTriggers = Array.from(header.querySelectorAll("[data-mobile-group-trigger]"));
        const mobilePanels = Array.from(header.querySelectorAll("[data-mobile-group-panel]"));

        function closeMobileGroups() {
            mobileTriggers.forEach((trigger) => trigger.setAttribute("aria-expanded", "false"));
            mobilePanels.forEach((groupPanel) => { groupPanel.hidden = true; });
        }

        mobileTriggers.forEach((trigger) => {
            trigger.addEventListener("click", () => {
                const key = trigger.dataset.mobileGroupTrigger;
                const willOpen = trigger.getAttribute("aria-expanded") !== "true";
                closeMobileGroups();
                if (willOpen) {
                    trigger.setAttribute("aria-expanded", "true");
                    const groupPanel = mobilePanels.find((item) => item.dataset.mobileGroupPanel === key);
                    if (groupPanel) groupPanel.hidden = false;
                }
            });
        });
        mobileNav?.addEventListener("toggle", () => {
            if (!mobileNav.open) closeMobileGroups();
        });
    }

    document.addEventListener("DOMContentLoaded", initPublicMegaMenu);
})();

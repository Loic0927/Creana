(function () {
    const tourSteps = [
        {
            step: "Step 01",
            label: "Sign in",
            progress: 0.003,
            title: "Sign in and create an account",
            slides: [
                {
                    image: "signin.PNG",
                    title: "Sign in to Creana",
                    description:
                        "Sign in with email, password, or Google. Each email can only be linked to one account.",
                },
                {
                    image: "create_account.PNG",
                    title: "Create your account",
                    description:
                        "Enter your username, email, and password to create a new account and start using Creana.",
                },
            ],
        },
        {
            step: "Step 02",
            label: "Feed",
            progress: 0.239,
            title: "Explore the feed",
            slides: [
                {
                    image: "feed.PNG",
                    title: "Browse the feed",
                    description:
                        "Browse posts from other users and filter content by hashtag or keyword.",
                },
                {
                    image: "sidebar.PNG",
                    title: "Use the sidebar",
                    description:
                        "Use the sidebar to open Profile, Feed, Create Post, Campaigns, Dashboard, or log out.",
                },
            ],
        },
        {
            step: "Step 03",
            label: "Profile",
            progress: 0.42,
            title: "Manage your profile",
            slides: [
                {
                    image: "profile.PNG",
                    title: "View your profile",
                    description:
                        "View your avatar, username, bio, links, posts, followers, and following count.",
                },
                {
                    image: "profile_edit.PNG",
                    title: "Edit your profile",
                    description:
                        "Update your username, bio, and links, then save your changes.",
                },
            ],
        },
        {
            step: "Step 04",
            label: "Create Post",
            progress: 0.547,
            title: "Create and publish posts",
            slides: [
                {
                    image: "createpost1.PNG",
                    title: "Create a post",
                    description:
                        "Create articles, image posts, or video posts. Save unfinished work as drafts.",
                },
                {
                    image: "createpost2.PNG",
                    title: "Set post details",
                    description:
                        "Add content details, choose visibility, select a platform, assign a campaign, then publish, save, or schedule.",
                },
                {
                    image: "createpost3.PNG",
                    title: "Schedule publishing",
                    description:
                        "Choose a date and time for automatic publishing.",
                },
            ],
        },
        {
            step: "Step 05",
            label: "Campaigns",
            progress: 0.735,
            title: "Organise campaigns",
            slides: [
                {
                    image: "campaign1.PNG",
                    title: "Manage campaigns",
                    description:
                        "Group related posts under the same campaign goal.",
                },
                {
                    image: "campaign2.PNG",
                    title: "Create a campaign",
                    description:
                        "Set the campaign name, objective, platforms, posts, and schedule.",
                },
            ],
        },
        {
            step: "Step 06",
            label: "Dashboard",
            progress: 0.991,
            title: "Review dashboard performance",
            slides: [
                {
                    image: "dashboard.PNG",
                    title: "Check your dashboard",
                    description:
                        "Review weekly views, likes, comments, shares, trends, and recent post metrics.",
                },
            ],
        },
    ];

    function initPolicyModal() {
        const modalRoot = document.querySelector("[data-policy-modal-root]");
        const title = document.querySelector("#landing-policy-title");
        const content = document.querySelector("[data-policy-content]");
        const triggers = Array.from(document.querySelectorAll("[data-policy-modal]"));
        const closeButtons = Array.from(document.querySelectorAll("[data-policy-close]"));

        if (!modalRoot || !title || !content || !triggers.length) {
            return;
        }

        let lastTrigger = null;

        function getFocusableElements() {
            return Array.from(
                modalRoot.querySelectorAll(
                    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
                )
            ).filter((element) => !element.disabled && element.offsetParent !== null);
        }

        function closeModal() {
            if (modalRoot.hidden) {
                return;
            }

            modalRoot.hidden = true;
            document.body.classList.remove("landing-modal-open");
            content.replaceChildren();

            if (lastTrigger) {
                lastTrigger.focus();
            }
        }

        function openModal(policyKey, trigger) {
            const template = document.querySelector(
                `[data-policy-template="${policyKey}"]`
            );

            if (!template) {
                return;
            }

            lastTrigger = trigger;
            title.textContent = template.dataset.policyTitle || "";
            content.replaceChildren(template.content.cloneNode(true));
            modalRoot.hidden = false;
            document.body.classList.add("landing-modal-open");

            window.requestAnimationFrame(() => {
                const focusableElements = getFocusableElements();
                const closeButton = focusableElements.find((element) =>
                    element.matches("[data-policy-close]")
                );

                (closeButton || modalRoot.querySelector('[role="dialog"]')).focus();
            });
        }

        triggers.forEach((trigger) => {
            trigger.addEventListener("click", () => {
                openModal(trigger.dataset.policyModal, trigger);
            });
        });

        closeButtons.forEach((button) => {
            button.addEventListener("click", closeModal);
        });

        modalRoot.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                event.preventDefault();
                closeModal();
                return;
            }

            if (event.key !== "Tab") {
                return;
            }

            const focusableElements = getFocusableElements();

            if (!focusableElements.length) {
                event.preventDefault();
                return;
            }

            const firstElement = focusableElements[0];
            const lastElement = focusableElements[focusableElements.length - 1];

            if (event.shiftKey && document.activeElement === firstElement) {
                event.preventDefault();
                lastElement.focus();
            } else if (!event.shiftKey && document.activeElement === lastElement) {
                event.preventDefault();
                firstElement.focus();
            }
        });
    }

    function initCreanaTour() {
        const tourRoot = document.querySelector("[data-creana-tour]");
        const map = document.querySelector("[data-creana-tour-map]");
        const fox = document.querySelector("[data-creana-tour-fox]");
        const desktopPath = document.querySelector(
            ".creana-tour__desktop-path .creana-tour__path-main"
        );
        const mobilePath = document.querySelector(
            ".creana-tour__mobile-path .creana-tour__path-main"
        );
        const desktopSvg = document.querySelector(".creana-tour__desktop-path");
        const popup = document.querySelector("[data-creana-tour-popup]");
        const dialog = document.querySelector(".creana-tour__dialog");
        const stepLabel = document.querySelector("[data-creana-tour-step-label]");
        const title = document.querySelector("[data-creana-tour-title]");
        const image = document.querySelector("[data-creana-tour-image]");
        const slideTitle = document.querySelector("[data-creana-tour-slide-title]");
        const description = document.querySelector("[data-creana-tour-description]");
        const count = document.querySelector("[data-creana-tour-count]");
        const previousButton = document.querySelector("[data-creana-tour-previous]");
        const nextButton = document.querySelector("[data-creana-tour-next]");
        const closeButtons = Array.from(
            document.querySelectorAll("[data-creana-tour-close]")
        );
        const triggers = Array.from(
            document.querySelectorAll("[data-creana-tour-step]")
        );
        const nodes = triggers;
        const imageDir = tourRoot?.dataset.creanaTourImageDir || "";
        const nodeHitRadius = 26;

        let activeStepIndex = 0;
        let activeSlideIndex = 0;
        let activeNodeStepIndex = null;
        let manuallyClosedStepIndex = null;

        function getActivePath() {
            const desktopVisible =
                desktopSvg && window.getComputedStyle(desktopSvg).display !== "none";

            return desktopVisible ? desktopPath : mobilePath;
        }

        function clamp(value, min, max) {
            return Math.min(max, Math.max(min, value));
        }

        function getPointOnActivePath(progress) {
            const path = getActivePath();

            if (!path || !map) {
                return null;
            }

            const svg = path.closest("svg");

            if (!svg) {
                return null;
            }

            const svgRect = svg.getBoundingClientRect();
            const mapRect = map.getBoundingClientRect();
            const totalLength = path.getTotalLength();
            const point = path.getPointAtLength(totalLength * progress);
            const viewBox = svg.viewBox.baseVal;
            const xInSvg =
                ((point.x - viewBox.x) / viewBox.width) * svgRect.width;
            const yInSvg =
                ((point.y - viewBox.y) / viewBox.height) * svgRect.height;

            return {
                x: svgRect.left - mapRect.left + xInSvg,
                y: svgRect.top - mapRect.top + yInSvg,
            };
        }

        function positionNodesOnPath() {
            nodes.forEach((node, index) => {
                const step = tourSteps[index];

                if (!step) {
                    return;
                }

                const point = getPointOnActivePath(step.progress);

                if (!point) {
                    return;
                }

                node.style.left = `${point.x}px`;
                node.style.top = `${point.y}px`;
                node.style.transform = "translate(-50%, -50%)";
            });
        }

        function setActiveNode(stepIndex) {
            triggers.forEach((trigger) => {
                const triggerIndex = Number(trigger.dataset.creanaTourStep);
                trigger.classList.toggle("is-active", triggerIndex === stepIndex);
            });
        }

        function renderSlide() {
            const step = tourSteps[activeStepIndex];

            if (
                !step ||
                !popup ||
                !stepLabel ||
                !title ||
                !image ||
                !slideTitle ||
                !description ||
                !count
            ) {
                return;
            }

            const slide = step.slides[activeSlideIndex];

            stepLabel.textContent = `${step.step} · ${step.label}`;
            title.textContent = step.title;
            image.src = `${imageDir}${slide.image}`;
            image.alt = slide.title;
            slideTitle.textContent = slide.title;
            description.textContent = slide.description;
            count.textContent = `${activeSlideIndex + 1} / ${step.slides.length}`;

            if (previousButton) {
                previousButton.disabled = activeSlideIndex === 0;
            }

            if (nextButton) {
                nextButton.disabled = activeSlideIndex === step.slides.length - 1;
            }
        }

        function positionPopupNearNode(stepIndex) {
            const node = nodes[stepIndex];

            if (!node || !popup || !dialog || !map) {
                return;
            }

            if (window.matchMedia("(max-width: 767.98px)").matches) {
                popup.style.left = "";
                popup.style.top = "";
                return;
            }

            const nodeRect = node.getBoundingClientRect();
            const viewportWidth = window.innerWidth;
            const viewportHeight = window.innerHeight;
            const popupWidth = dialog.offsetWidth || popup.offsetWidth || 280;
            const popupHeight = dialog.offsetHeight || popup.offsetHeight || 280;
            const margin = 16;
            const gap = 20;

            let left = nodeRect.right + gap;
            let top = nodeRect.top + nodeRect.height / 2 - popupHeight / 2;

            if (left + popupWidth > viewportWidth - margin) {
                left = nodeRect.left - popupWidth - gap;
            }

            left = Math.max(margin, Math.min(left, viewportWidth - popupWidth - margin));
            top = Math.max(margin, Math.min(top, viewportHeight - popupHeight - margin));

            popup.style.left = `${left}px`;
            popup.style.top = `${top}px`;
        }

        function openTourPopup(stepIndex, shouldFocus = false) {
            const step = tourSteps[stepIndex];

            if (!step || !popup) {
                return;
            }

            activeStepIndex = stepIndex;
            activeSlideIndex = 0;
            setActiveNode(stepIndex);
            renderSlide();
            popup.hidden = false;
            positionPopupNearNode(stepIndex);

            if (shouldFocus && dialog) {
                window.requestAnimationFrame(() => {
                    positionPopupNearNode(stepIndex);
                    dialog.focus();
                });
            } else {
                window.requestAnimationFrame(() => positionPopupNearNode(stepIndex));
            }
        }

        function closeTourPopup() {
            if (!popup || popup.hidden) {
                return;
            }

            popup.hidden = true;
            manuallyClosedStepIndex = activeNodeStepIndex;
        }

        function closeTourPopupWithoutDismiss() {
            if (!popup || popup.hidden) {
                return;
            }

            popup.hidden = true;
        }

        function getNodeCenterInMap(node) {
            const nodeRect = node.getBoundingClientRect();
            const mapRect = map.getBoundingClientRect();

            return {
                x: nodeRect.left - mapRect.left + nodeRect.width / 2,
                y: nodeRect.top - mapRect.top + nodeRect.height / 2,
            };
        }

        function getStepAtFoxPosition(foxX, foxY) {
            let closestIndex = null;
            let closestDistance = Infinity;

            nodes.forEach((node, index) => {
                const center = getNodeCenterInMap(node);
                const dx = foxX - center.x;
                const dy = foxY - center.y;
                const distance = Math.sqrt(dx * dx + dy * dy);

                if (distance < closestDistance) {
                    closestDistance = distance;
                    closestIndex = index;
                }
            });

            if (closestDistance <= nodeHitRadius) {
                return closestIndex;
            }

            return null;
        }

        function updatePopupByFoxPosition(foxX, foxY) {
            const matchedStepIndex = getStepAtFoxPosition(foxX, foxY);

            if (matchedStepIndex === null) {
                closeTourPopupWithoutDismiss();
                activeNodeStepIndex = null;
                manuallyClosedStepIndex = null;
                setActiveNode(null);
                return;
            }

            setActiveNode(matchedStepIndex);

            if (matchedStepIndex === manuallyClosedStepIndex) {
                return;
            }

            if (matchedStepIndex !== activeNodeStepIndex) {
                activeNodeStepIndex = matchedStepIndex;
                openTourPopup(matchedStepIndex);
                positionPopupNearNode(matchedStepIndex);
            } else if (popup && !popup.hidden) {
                positionPopupNearNode(matchedStepIndex);
            }
        }

        function updateFoxByScroll() {
            if (!map || !fox) {
                return;
            }

            const path = getActivePath();

            if (!path) {
                return;
            }

            const mapRect = map.getBoundingClientRect();
            const svg = path.closest("svg");
            const svgRect = svg.getBoundingClientRect();
            const mapTop = window.scrollY + mapRect.top;
            const mapHeight = map.offsetHeight;
            const scrollStart = mapTop - window.innerHeight * 0.55;
            const scrollEnd = mapTop + mapHeight - window.innerHeight * 0.45;
            const rawProgress =
                (window.scrollY - scrollStart) / (scrollEnd - scrollStart);
            const progress = clamp(rawProgress, 0, 1);
            const totalLength = path.getTotalLength();
            const point = path.getPointAtLength(totalLength * progress);
            const viewBox = svg.viewBox.baseVal;
            const xInSvg =
                ((point.x - viewBox.x) / viewBox.width) * svgRect.width;
            const yInSvg =
                ((point.y - viewBox.y) / viewBox.height) * svgRect.height;
            const mapLeftOffset = mapRect.left;
            const mapTopOffset = mapRect.top;
            const x = svgRect.left - mapLeftOffset + xInSvg;
            const y = svgRect.top - mapTopOffset + yInSvg;

            fox.style.left = `${x}px`;
            fox.style.top = `${y}px`;

            if (rawProgress < 0 || rawProgress > 1) {
                closeTourPopupWithoutDismiss();
                activeNodeStepIndex = null;
                manuallyClosedStepIndex = null;
                setActiveNode(null);
                return;
            }

            updatePopupByFoxPosition(x, y);
        }

        triggers.forEach((trigger) => {
            trigger.addEventListener("click", () => {
                const stepIndex = Number(trigger.dataset.creanaTourStep);

                if (!Number.isNaN(stepIndex)) {
                    activeNodeStepIndex = stepIndex;
                    manuallyClosedStepIndex = null;
                    openTourPopup(stepIndex, true);
                }
            });
        });

        closeButtons.forEach((button) => {
            button.addEventListener("click", closeTourPopup);
        });

        if (previousButton) {
            previousButton.addEventListener("click", () => {
                activeSlideIndex = Math.max(0, activeSlideIndex - 1);
                renderSlide();
            });
        }

        if (nextButton) {
            nextButton.addEventListener("click", () => {
                const step = tourSteps[activeStepIndex];

                if (!step) {
                    return;
                }

                activeSlideIndex = Math.min(
                    step.slides.length - 1,
                    activeSlideIndex + 1
                );
                renderSlide();
            });
        }

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && popup && !popup.hidden) {
                event.preventDefault();
                closeTourPopup();
            }
        });

        function handleResize() {
            positionNodesOnPath();
            updateFoxByScroll();

            if (activeNodeStepIndex !== null && popup && !popup.hidden) {
                positionPopupNearNode(activeNodeStepIndex);
            }
        }

        window.addEventListener("scroll", updateFoxByScroll, { passive: true });
        window.addEventListener("resize", handleResize);
        window.requestAnimationFrame(() => {
            positionNodesOnPath();
            updateFoxByScroll();
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        initPolicyModal();
        initCreanaTour();
    });
})();

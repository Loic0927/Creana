(function () {
    "use strict";

    const cookie = (name) => document.cookie.split(";").map((value) => value.trim()).find((value) => value.startsWith(name + "="))?.split("=").slice(1).join("=") || "";
    const element = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    };

    document.querySelectorAll("[data-analysis-agent]").forEach((root) => {
        if (root.dataset.analysisAgentReady === "true") return;
        root.dataset.analysisAgentReady = "true";
        const launcher = root.querySelector("[data-analysis-agent-open]");
        const panel = root.querySelector("[data-analysis-agent-panel]");
        const closeButton = root.querySelector("[data-analysis-agent-close]");
        const loading = root.querySelector("[data-analysis-agent-loading]");
        const status = root.querySelector("[data-analysis-agent-status]");
        const results = root.querySelector("[data-analysis-agent-results]");
        const error = root.querySelector("[data-analysis-agent-error]");
        const footer = root.querySelector("[data-analysis-agent-followups]");
        const trackButton = root.querySelector("[data-analysis-agent-track]");
        const refreshButton = root.querySelector("[data-analysis-agent-refresh]");
        const pickerBackButton = root.querySelector('[data-agent-action="picker-back"]');
        const trackPerformanceButton = root.querySelector('[data-agent-action="track-performance"]');
        const anotherPostButton = root.querySelector('[data-agent-action="another-post"]');
        const dashboardInsightButton = root.querySelector('[data-agent-action="dashboard-insight"]');
        const i18n = root.querySelector("[data-analysis-agent-i18n]").dataset;
        const gettext = (value) => ({
            "Select a post to track": i18n.select, "Search posts": i18n.search, "Back": i18n.back,
            "Track performance": i18n.trackPerformance, "Previous": i18n.previous, "Next": i18n.next,
            "posts found": i18n.postsFound, "Tracked post": i18n.trackedPost,
            "Previous recommendation": i18n.previousRecommendation, "Performance update": i18n.performanceUpdate,
            "Progress status": i18n.progressStatus, "What changed": i18n.whatChanged,
            "Content Goal": i18n.contentGoal, "Next action": i18n.nextAction,
            "Performance metrics": i18n.performanceMetrics, "Performance comparison": i18n.performanceComparison,
            "Current": i18n.current, "Baseline": i18n.baseline, "Since last tracking": i18n.sinceLast,
            "percentage points": i18n.percentagePoints, "Baseline time": i18n.baselineTime,
            "Latest tracking time": i18n.latestTime, "Time elapsed": i18n.timeElapsed,
            "Unavailable": i18n.unavailable, "Views": i18n.views, "Likes": i18n.likes,
            "Comments": i18n.comments, "Shares": i18n.shares, "Engagement rate": i18n.engagementRate,
            "Average watch time": i18n.averageWatchTime, "Completion rate": i18n.completionRate,
            "Retention rate": i18n.retentionRate, "Tracking snapshot summary": i18n.trackingSummary,
            "Baseline created": i18n.baselineCreated, "Not enough data": i18n.notEnoughData,
            "Improved": i18n.improved, "Stable": i18n.stable, "Declined": i18n.declined,
            "Another post": i18n.anotherPost, "Dashboard insight": i18n.dashboardInsight,
        }[value] || value);
        let loaded = false;
        let loadingRequest = false;
        let requestVersion = 0;
        let closeTimer = null;
        let insightHtml = "";
        let currentView = "dashboard-insight";
        let pickerQuery = "";
        let pickerPage = 1;
        let selectedPost = null;
        let trackingChartRoot = null;
        let searchController = null;
        let searchInFlight = false;
        let submittedSearch = null;

        async function requestJson(url, options = {}) {
            const response = await fetch(url, {...options, credentials: "same-origin", headers: {"X-Requested-With": "XMLHttpRequest", ...(options.headers || {})}});
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.success === false) throw new Error(payload.error || "Request failed");
            return payload;
        }

        function showLoading(label) {
            loading.hidden = false; results.hidden = true; error.hidden = true; footer.hidden = true;
            status.textContent = label;
        }

        async function loadAnalysis(force = false) {
            if (loadingRequest || (loaded && !force)) return;
            const version = ++requestVersion;
            loadingRequest = true;
            currentView = "dashboard-insight";
            showLoading(root.dataset.thinking);
            try {
                const analysisUrl = (value) => {
                    const url = new URL(value, window.location.origin);
                    if (force) url.searchParams.set("force_refresh", "1");
                    return url;
                };
                const requestOptions = force ? {cache: "no-store"} : {};
                const primary = await requestJson(analysisUrl(root.dataset.primaryUrl), requestOptions);
                if (version !== requestVersion) return;
                status.textContent = root.dataset.analysing;
                let secondary = "";
                if (root.dataset.secondaryUrl) {
                    try { secondary = (await requestJson(analysisUrl(root.dataset.secondaryUrl), requestOptions)).insight_html || ""; } catch (_) { secondary = ""; }
                }
                if (version !== requestVersion) return;
                insightHtml = (primary.insight_html || "") + secondary;
                results.innerHTML = insightHtml;
                results.hidden = false; loading.hidden = true;
                setFooter(trackButton ? ["track", "refresh"] : ["refresh"]);
                loaded = true;
            } catch (_) {
                if (version !== requestVersion) return;
                loading.hidden = true; error.hidden = false;
            } finally { if (version === requestVersion) loadingRequest = false; }
        }

        function setFooter(actions) {
            footer.querySelectorAll("[data-agent-action]").forEach((button) => { button.hidden = !actions.includes(button.dataset.agentAction); });
            footer.classList.toggle("is-result-actions", actions.includes("another-post"));
            footer.hidden = false;
        }

        function pickerShell() {
            destroyTrackingChart();
            currentView = "post-picker";
            results.replaceChildren(); results.hidden = false; loading.hidden = true; error.hidden = true;
            const heading = element("h3", "analysis-agent-view-title", gettext("Select a post to track"));
            heading.tabIndex = -1;
            const label = element("label", "sr-only", gettext("Search posts"));
            const search = element("input", "analysis-agent-search");
            const searchGroup = element("div", "analysis-agent-search-group");
            const searchButton = element("button", "analysis-agent-search-button");
            const searchId = "analysis-agent-post-search";
            label.htmlFor = searchId;
            search.id = searchId; search.type = "text"; search.inputMode = "search"; search.placeholder = gettext("Search posts"); search.value = pickerQuery;
            searchButton.type = "button"; searchButton.setAttribute("aria-label", gettext("Search posts"));
            searchButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="11" cy="11" r="6.5"></circle><path d="m16 16 4 4"></path></svg>';
            searchGroup.append(search, searchButton);
            const announcement = element("p", "sr-only"); announcement.setAttribute("role", "status"); announcement.setAttribute("aria-live", "polite");
            const list = element("div", "analysis-agent-post-list"); list.setAttribute("data-picker-list", "");
            trackPerformanceButton.disabled = !selectedPost;
            results.append(heading, label, searchGroup, announcement, list);
            setFooter(["picker-back", "track-performance"]);
            const submitSearch = () => {
                const nextQuery = search.value.trim();
                if (searchInFlight && nextQuery === submittedSearch) return;
                pickerQuery = nextQuery; pickerPage = 1; selectedPost = null; trackPerformanceButton.disabled = true;
                loadPosts(list, announcement, trackPerformanceButton, searchButton);
            };
            searchButton.addEventListener("click", submitSearch);
            search.addEventListener("keydown", (event) => {
                if (event.key === "Escape") event.stopPropagation();
                if (event.key === "Enter") { event.preventDefault(); event.stopPropagation(); submitSearch(); }
            });
            loadPosts(list, announcement, trackPerformanceButton, searchButton);
            heading.focus();
        }

        async function loadPosts(list, announcement, submit, searchButton = null) {
            searchController?.abort();
            searchController = new AbortController();
            const version = ++requestVersion;
            submittedSearch = pickerQuery;
            searchInFlight = true;
            if (searchButton) searchButton.disabled = true;
            list.replaceChildren(element("p", "analysis-agent-state", root.dataset.loadingPosts));
            announcement.textContent = root.dataset.loadingPosts;
            try {
                const url = new URL(root.dataset.postsUrl, window.location.origin);
                url.searchParams.set("q", pickerQuery); url.searchParams.set("page", String(pickerPage));
                const payload = await requestJson(url, {signal: searchController.signal});
                if (version !== requestVersion || currentView !== "post-picker") return;
                list.replaceChildren();
                if (!payload.posts.length) list.append(element("p", "analysis-agent-state", pickerQuery ? root.dataset.noResults : root.dataset.noPosts));
                payload.posts.forEach((post) => {
                    const card = element("button", "analysis-agent-post-card"); card.type = "button"; card.dataset.postId = post.id; card.setAttribute("aria-pressed", "false");
                    if (post.thumbnail_url) { const image = element("img", "analysis-agent-post-thumb"); image.src = post.thumbnail_url; image.alt = post.thumbnail_alt; image.loading = "lazy"; card.append(image); }
                    else { const placeholder = element("span", "analysis-agent-post-placeholder", "Aa"); placeholder.setAttribute("aria-hidden", "true"); card.append(placeholder); }
                    card.append(element("span", "analysis-agent-post-title", post.title));
                    card.addEventListener("click", () => { list.querySelectorAll("[aria-pressed=true]").forEach((item) => item.setAttribute("aria-pressed", "false")); card.setAttribute("aria-pressed", "true"); selectedPost = post; submit.disabled = false; });
                    list.append(card);
                });
                const pager = element("div", "analysis-agent-pagination");
                if (payload.has_previous) { const previous = element("button", "btn btn-secondary", gettext("Previous")); previous.type = "button"; previous.addEventListener("click", () => { pickerPage -= 1; loadPosts(list, announcement, submit, searchButton); }); pager.append(previous); }
                if (payload.has_next) { const next = element("button", "btn btn-secondary", gettext("Next")); next.type = "button"; next.addEventListener("click", () => { pickerPage += 1; loadPosts(list, announcement, submit, searchButton); }); pager.append(next); }
                list.append(pager); announcement.textContent = `${payload.count} ${gettext("posts found")}`;
            } catch (requestError) { if (requestError.name !== "AbortError" && version === requestVersion) { list.replaceChildren(element("p", "analysis-agent-error", root.dataset.searchError)); announcement.textContent = root.dataset.searchError; } }
            finally { if (version === requestVersion) { searchInFlight = false; if (searchButton) searchButton.disabled = false; } }
        }

        async function trackPost(postId) {
            const version = ++requestVersion; loadingRequest = true; showLoading(root.dataset.thinking);
            try {
                const url = root.dataset.trackUrlTemplate.replace(/0\/track\/$/, `${postId}/track/`);
                const payload = await requestJson(url, {method: "POST", headers: {"X-CSRFToken": decodeURIComponent(cookie("csrftoken"))}});
                if (version !== requestVersion) return;
                renderTrackingResult(payload.report);
            } catch (_) { if (version === requestVersion) { loading.hidden = true; error.hidden = false; error.querySelector("p").textContent = root.dataset.trackError; } }
            finally { if (version === requestVersion) loadingRequest = false; }
        }

        function reportSection(heading, value) { const section = element("section", "ai-insight-section"); section.append(element("h4", "", heading), element("p", "", value)); return section; }

        function destroyTrackingChart() {
            if (trackingChartRoot?.unmount) trackingChartRoot.unmount();
            trackingChartRoot = null;
        }

        function formatMetric(value, style = "number") {
            if (value === null || value === undefined) return gettext("Unavailable");
            if (style === "percent") return `${Number(value).toLocaleString(undefined, {maximumFractionDigits: 1})}%`;
            if (style === "seconds") return `${Number(value).toLocaleString(undefined, {maximumFractionDigits: 1})}s`;
            return Number(value).toLocaleString();
        }

        function metricDelta(report, key, style = "number") {
            const value = report.deltas?.[key];
            if (value === null || value === undefined) return gettext("Baseline");
            const number = Number(value);
            const prefix = number > 0 ? "+" : "";
            const formatted = Number(number).toLocaleString(undefined, {maximumFractionDigits: 1});
            if (style === "percent") return `${prefix}${formatted} ${gettext("percentage points")}`;
            return `${prefix}${formatted} ${gettext("Since last tracking")}`;
        }

        function metricCard(report, label, key, style = "number", explicitValue = undefined) {
            const card = element("article", "analysis-agent-metric-card");
            const available = explicitValue !== undefined ? explicitValue !== null : report.metric_availability?.[key] !== false;
            const value = explicitValue !== undefined ? explicitValue : report.metrics[key];
            card.append(element("span", "analysis-agent-metric-label", label));
            card.append(element("strong", "analysis-agent-metric-value", available ? formatMetric(value, style) : gettext("Unavailable")));
            if (explicitValue === undefined) card.append(element("span", "analysis-agent-metric-delta", available ? metricDelta(report, key, style) : gettext("Unavailable")));
            return card;
        }

        function renderComparisonChart(container, report) {
            const summary = element("p", "sr-only");
            if (!report.previous_metrics) {
                summary.textContent = i18n.firstChart;
                container.append(element("p", "analysis-agent-chart-baseline", i18n.firstChart), summary);
                return;
            }
            const chartData = [
                ["views", gettext("Views")], ["likes", gettext("Likes")],
                ["comments", gettext("Comments")], ["shares", gettext("Shares")],
            ].filter(([key]) => report.metric_availability?.[key] !== false).map(([key, label]) => ({metric: label, previous: report.previous_metrics[key], current: report.metrics[key]}));
            if (!chartData.length) {
                summary.textContent = gettext("Unavailable"); container.append(summary, element("p", "analysis-agent-chart-baseline", gettext("Unavailable"))); return;
            }
            summary.textContent = chartData.map((item) => `${item.metric}: ${gettext("Previous")} ${item.previous}, ${gettext("Current")} ${item.current}.`).join(" ");
            container.append(summary);
            if (!window.React || !window.ReactDOM || !window.Recharts) {
                container.append(element("p", "analysis-agent-chart-baseline", summary.textContent));
                return;
            }
            const chart = element("div", "analysis-agent-chart"); chart.setAttribute("aria-hidden", "true"); container.append(chart);
            const h = window.React.createElement;
            const {ResponsiveContainer, BarChart, Bar, CartesianGrid, XAxis, YAxis, Tooltip, Legend} = window.Recharts;
            const component = h(ResponsiveContainer, {width: "100%", height: 220}, h(BarChart, {data: chartData, margin: {top: 8, right: 4, left: -24, bottom: 4}},
                h(CartesianGrid, {strokeDasharray: "3 3", vertical: false}),
                h(XAxis, {dataKey: "metric", tick: {fontSize: 11}, interval: 0}),
                h(YAxis, {allowDecimals: false, tick: {fontSize: 11}}), h(Tooltip), h(Legend),
                h(Bar, {dataKey: "previous", name: gettext("Previous"), fill: "#94a3b8", radius: [4, 4, 0, 0], isAnimationActive: false}),
                h(Bar, {dataKey: "current", name: gettext("Current"), fill: "#2869be", radius: [4, 4, 0, 0], isAnimationActive: false})
            ));
            trackingChartRoot = window.ReactDOM.createRoot(chart); trackingChartRoot.render(component);
        }

        function renderTrackingResult(report) {
            destroyTrackingChart();
            currentView = "tracking-result"; results.replaceChildren(); results.hidden = false; loading.hidden = true; error.hidden = true;
            const trackedSection = element("section", "analysis-agent-tracked-post");
            const heading = element("h3", "analysis-agent-view-title", gettext("Tracked post")); heading.tabIndex = -1;
            const trackedRow = element("div", "analysis-agent-tracked-row");
            if (report.post.thumbnail_url) { const image = element("img", "analysis-agent-tracked-thumb"); image.src = report.post.thumbnail_url; image.alt = report.post.thumbnail_alt; image.loading = "lazy"; trackedRow.append(image); }
            else { const placeholder = element("span", "analysis-agent-post-placeholder", "Aa"); placeholder.setAttribute("aria-hidden", "true"); trackedRow.append(placeholder); }
            trackedRow.append(element("strong", "analysis-agent-tracked-title", report.post.title)); trackedSection.append(heading, trackedRow);
            const statusLabels = {baseline_created: gettext("Baseline created"), not_enough_data: gettext("Not enough data"), improved: gettext("Improved"), stable: gettext("Stable"), declined: gettext("Declined")};
            const statusSection = element("section", "analysis-agent-status-section"); statusSection.append(element("h4", "", gettext("Progress status")), element("span", `analysis-agent-status-badge is-${report.progress_status}`, statusLabels[report.progress_status] || statusLabels.stable));
            const metricsSection = element("section", "analysis-agent-result-section"); metricsSection.append(element("h4", "", gettext("Performance metrics")));
            const grid = element("div", "analysis-agent-metrics-grid");
            grid.append(metricCard(report, gettext("Views"), "views"), metricCard(report, gettext("Likes"), "likes"), metricCard(report, gettext("Comments"), "comments"), metricCard(report, gettext("Shares"), "shares"), metricCard(report, gettext("Engagement rate"), "engagement_rate", "percent"));
            if (report.video_metrics) grid.append(metricCard(report, gettext("Average watch time"), "average_watch_seconds", "seconds", report.video_metrics.average_watch_seconds), metricCard(report, gettext("Completion rate"), "completion_rate", "percent", report.video_metrics.completion_rate), metricCard(report, gettext("Retention rate"), "retention_percent", "percent"));
            metricsSection.append(grid);
            const chartSection = element("section", "analysis-agent-result-section"); chartSection.append(element("h4", "", gettext("Performance comparison"))); renderComparisonChart(chartSection, report);
            const snapshotSection = element("section", "analysis-agent-result-section"); snapshotSection.append(element("h4", "", gettext("Tracking snapshot summary")));
            const snapshotList = element("dl", "analysis-agent-snapshot-list");
            [[gettext("Baseline time"), report.snapshot.baseline_display], [gettext("Latest tracking time"), report.snapshot.latest_display], [gettext("Time elapsed"), report.snapshot.elapsed_display]].forEach(([term, value]) => snapshotList.append(element("dt", "", term), element("dd", "", value)));
            snapshotSection.append(snapshotList);
            results.append(trackedSection, statusSection, metricsSection, chartSection, snapshotSection, reportSection(gettext("Previous recommendation"), report.previous_recommendation), reportSection(gettext("Performance update"), report.performance_update), reportSection(gettext("What changed"), report.what_changed), reportSection(gettext("Content Goal"), report.content_goal_note), reportSection(gettext("Next action"), report.next_action));
            setFooter(["another-post", "dashboard-insight"]); heading.focus();
        }

        function showInsight() { destroyTrackingChart(); searchController?.abort(); currentView = "dashboard-insight"; ++requestVersion; results.innerHTML = insightHtml; results.hidden = false; loading.hidden = true; error.hidden = true; setFooter(trackButton ? ["track", "refresh"] : ["refresh"]); refreshButton?.focus(); }
        function openPanel() { if (closeTimer) { window.clearTimeout(closeTimer); closeTimer = null; } panel.hidden = false; launcher.setAttribute("aria-expanded", "true"); requestAnimationFrame(() => panel.classList.add("is-open")); loadAnalysis(); closeButton.focus(); }
        function closePanel() { if (panel.hidden) return; searchController?.abort(); ++requestVersion; loadingRequest = false; panel.classList.remove("is-open"); launcher.setAttribute("aria-expanded", "false"); closeTimer = window.setTimeout(() => { panel.hidden = true; closeTimer = null; }, 180); launcher.focus(); }

        launcher.addEventListener("click", openPanel); closeButton.addEventListener("click", closePanel);
        root.querySelector("[data-analysis-agent-retry]")?.addEventListener("click", () => currentView === "post-picker" ? pickerShell() : loadAnalysis(true));
        refreshButton?.addEventListener("click", () => loadAnalysis(true)); trackButton?.addEventListener("click", pickerShell);
        pickerBackButton?.addEventListener("click", showInsight);
        trackPerformanceButton?.addEventListener("click", () => selectedPost && trackPost(selectedPost.id));
        anotherPostButton?.addEventListener("click", () => { selectedPost = null; pickerShell(); });
        dashboardInsightButton?.addEventListener("click", showInsight);
        document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !panel.hidden && event.target.type !== "search") closePanel(); });
    });
}());

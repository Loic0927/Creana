(function () {
    function initializeDashboardAnalytics() {
        const rootElement = document.getElementById("dashboard-analytics-root");

        if (!rootElement) {
            console.warn(
                "Dashboard analytics charts were not rendered: missing #dashboard-analytics-root.",
            );
            return;
        }

        if (!window.React || !window.ReactDOM || !window.Recharts) {
            console.warn(
                "Dashboard analytics charts were not rendered: React, ReactDOM, or window.Recharts is missing.",
            );
            return;
        }

        const h = window.React.createElement;
        const { useEffect, useState } = window.React;
        const {
            CartesianGrid,
            Legend,
            Line,
            LineChart,
            ResponsiveContainer,
            Tooltip,
            XAxis,
            YAxis,
        } = window.Recharts;

        const requiredRechartsComponents = {
            CartesianGrid,
            Legend,
            Line,
            LineChart,
            ResponsiveContainer,
            Tooltip,
            XAxis,
            YAxis,
        };
        const missingRechartsComponents = Object.entries(requiredRechartsComponents)
            .filter(([, component]) => !component)
            .map(([name]) => name);

        if (missingRechartsComponents.length) {
            console.warn(
                `Dashboard analytics charts were not rendered: window.Recharts is missing ${missingRechartsComponents.join(", ")}.`,
            );
            return;
        }

    function readDashboardData(scriptId) {
        const scriptElement = document.getElementById(scriptId);

        if (!scriptElement) {
            console.warn(
                `Dashboard analytics data was not found: missing #${scriptId}.`,
            );
            return [];
        }

        try {
            return JSON.parse(scriptElement.textContent);
        } catch (error) {
            console.warn(
                `Dashboard analytics data could not be parsed from #${scriptId}.`,
                error,
            );
            return [];
        }
    }

    const trendData = readDashboardData("dashboard-trend-data");
    const labels = {
        ariaLabel: "",
        emptyTitle: "",
        emptyBody: "",
        engagementTrend: "",
        renewWeekly: "",
        views: "",
        likes: "",
        comments: "",
        shares: "",
        ...(window.dashboardTranslations || {}),
    };
    const hasTrendData = trendData.some(
        (row) => (row.views || 0) + (row.likes || 0) + (row.comments || 0) + (row.shares || 0) > 0,
    );

    const numberFormatter = new Intl.NumberFormat("en-US");

    function formatValue(value) {
        return numberFormatter.format(value);
    }

    function formatShortDateLabel(label) {
        const [monthName, day] = String(label).split(" ");
        const monthMap = {
            Jan: "1",
            Feb: "2",
            Mar: "3",
            Apr: "4",
            May: "5",
            Jun: "6",
            Jul: "7",
            Aug: "8",
            Sep: "9",
            Oct: "10",
            Nov: "11",
            Dec: "12",
        };

        return monthMap[monthName] && day ? `${monthMap[monthName]}/${day}` : label;
    }

    function AnalyticsTooltip({ active, payload, label }) {
        if (!active || !payload || !payload.length) {
            return null;
        }

        return h(
            "div",
            { className: "analytics-tooltip" },
            h("p", { className: "analytics-tooltip-title" }, label),
            payload.map((entry) =>
                h(
                    "p",
                    { className: "analytics-tooltip-row", key: entry.dataKey },
                    h("span", {
                        className: "analytics-tooltip-dot",
                        style: { backgroundColor: entry.color },
                    }),
                    `${entry.name}: ${formatValue(entry.value)}`,
                ),
            ),
        );
    }

    function useIsMobileChart() {
        const getIsMobile = () => window.matchMedia("(max-width: 640px)").matches;
        const [isMobile, setIsMobile] = useState(getIsMobile);

        useEffect(() => {
            const mediaQuery = window.matchMedia("(max-width: 640px)");
            const handleChange = () => setIsMobile(mediaQuery.matches);

            handleChange();

            if (typeof mediaQuery.addEventListener === "function") {
                mediaQuery.addEventListener("change", handleChange);
                return () => mediaQuery.removeEventListener("change", handleChange);
            }

            mediaQuery.addListener(handleChange);
            return () => mediaQuery.removeListener(handleChange);
        }, []);

        return isMobile;
    }

    function ChartHeader({ title, subtitle, note }) {
        return h(
            "div",
            { className: "analytics-chart-header" },
            h(
                "div",
                null,
                h(
                    "div",
                    { className: "analytics-chart-title-row" },
                    h("h2", { className: "card-title" }, title),
                    note
                        ? h("span", { className: "analytics-chart-note" }, note)
                        : null,
                ),
                subtitle ? h("p", { className: "card-subtitle" }, subtitle) : null,
            ),
        );
    }

    function EmptyChartState() {
        return h(
            "div",
            { className: "analytics-empty-state" },
            h("h3", { className: "card-title" }, labels.emptyTitle),
            h("p", { className: "metric-meta" }, labels.emptyBody),
        );
    }

    function EngagementLineChart() {
        const isMobile = useIsMobileChart();
        const chartMargin = isMobile
            ? { top: 36, right: 16, left: -8, bottom: 0 }
            : { top: 40, right: 32, left: 0, bottom: 0 };
        const legendProps = {
            verticalAlign: "top",
            align: "right",
            iconType: "circle",
            wrapperStyle: isMobile
                ? { top: 8, paddingBottom: 2, textAlign: "right" }
                : { top: -10 },
        };

        return h(
            "article",
            { className: "analytics-card" },
            h(ChartHeader, {
                title: labels.engagementTrend,
                note: labels.renewWeekly,
            }),
            hasTrendData
                ? h(
                "div",
                { className: "analytics-chart-shell analytics-line-chart" },
                h(
                    ResponsiveContainer,
                    { width: "100%", height: isMobile ? 220 : 320 },
                    h(
                        LineChart,
                        { data: trendData, margin: chartMargin },
                        h(CartesianGrid, { strokeDasharray: "4 4", vertical: false, stroke: "#e2e8f0" }),
                        h(XAxis, {
                            dataKey: "date",
                            axisLine: false,
                            tickLine: false,
                            interval: isMobile ? 0 : "preserveEnd",
                            padding: { right: isMobile ? 12 : 20 },
                            tick: { fill: "#64748b", fontSize: 12 },
                            tickFormatter: isMobile ? formatShortDateLabel : undefined,
                        }),
                        h(YAxis, {
                            allowDecimals: false,
                            axisLine: false,
                            tickLine: false,
                            tickMargin: 10,
                            tick: { fill: "#64748b", fontSize: 12 },
                            tickFormatter: formatValue,
                            width: isMobile ? 40 : 68,
                        }),
                        h(Tooltip, { content: h(AnalyticsTooltip, null) }),
                        h(Legend, legendProps),
                        h(Line, {
                            type: "monotone",
                            dataKey: "views",
                            name: labels.views,
                            stroke: "#2563eb",
                            strokeWidth: 3,
                            dot: { r: 4, strokeWidth: 2 },
                            activeDot: { r: 6 },
                        }),
                        h(Line, {
                            type: "monotone",
                            dataKey: "likes",
                            name: labels.likes,
                            stroke: "#10b981",
                            strokeWidth: 3,
                            dot: { r: 4, strokeWidth: 2 },
                            activeDot: { r: 6 },
                        }),
                        h(Line, {
                            type: "monotone",
                            dataKey: "comments",
                            name: labels.comments,
                            stroke: "#f59e0b",
                            strokeWidth: 3,
                            dot: { r: 4, strokeWidth: 2 },
                            activeDot: { r: 6 },
                        }),
                        h(Line, {
                            type: "monotone",
                            dataKey: "shares",
                            name: labels.shares,
                            stroke: "#8b5cf6",
                            strokeWidth: 3,
                            dot: { r: 4, strokeWidth: 2 },
                            activeDot: { r: 6 },
                        }),
                    ),
                ),
            )
                : h(EmptyChartState, null),
        );
    }

    function DashboardAnalytics() {
        return h(
            "section",
            { className: "dashboard-analytics-section", "aria-label": labels.ariaLabel },
            h(EngagementLineChart, null),
        );
    }

        if (typeof window.ReactDOM.createRoot === "function") {
            window.ReactDOM.createRoot(rootElement).render(h(DashboardAnalytics, null));
            return;
        }

        if (typeof window.ReactDOM.render === "function") {
            window.ReactDOM.render(h(DashboardAnalytics, null), rootElement);
            return;
        }

        console.warn(
            "Dashboard analytics charts were not rendered: ReactDOM has no supported render method.",
        );
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initializeDashboardAnalytics, {
            once: true,
        });
    } else {
        initializeDashboardAnalytics();
    }
})();

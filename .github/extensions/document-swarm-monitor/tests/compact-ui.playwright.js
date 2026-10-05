async (page) => {
    const assert = (condition, message) => { if (!condition) throw new Error(message); };
    const errors = [];
    const onError = error => errors.push(error.message);
    page.on("pageerror", onError);
    const measurements = {};
    try {
        await page.waitForSelector("#dashboard");
        assert((await page.locator("#execution-label").textContent()).includes("DEMONSTRAÇÃO"), "Use an isolated demonstration fixture");
        await page.setViewportSize({ width: 1440, height: 960 });
        if (await page.locator("#expand").getAttribute("aria-pressed") === "true") await page.locator("#expand").click();
        await page.getByRole("tab", { name: "Fluxo", exact: true }).click();
        await page.waitForFunction(() => document.querySelectorAll("[data-agent]").length > 0);
        measurements.desktop = await page.evaluate(() => {
            const dashboard = document.getElementById("dashboard").getBoundingClientRect();
            return {
                width: dashboard.width, height: dashboard.height,
                areaFraction: dashboard.width * dashboard.height / (innerWidth * innerHeight),
                metricsHeight: document.querySelector(".metrics").getBoundingClientRect().height,
            };
        });
        assert(measurements.desktop.width <= 720 && measurements.desktop.height <= 480, "Compact footprint exceeds 720x480");
        assert(measurements.desktop.width > 600 && measurements.desktop.height > 350, "A collapsed/empty panel must not pass the footprint check");
        assert(measurements.desktop.areaFraction <= .25, "Default panel must fit a quarter of the reference screen");
        assert(measurements.desktop.metricsHeight <= 48, "Summary strip is still too tall");
        const graph = await page.locator("[data-agent]").evaluateAll(nodes => {
            const cards = nodes.map(node => node.querySelector(".card").getBoundingClientRect());
            const overlap = cards.some((a, index) => cards.slice(index + 1).some(b => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top));
            const clippedLabels = nodes.flatMap(node => {
                const card = node.querySelector(".card").getBoundingClientRect();
                return [...node.querySelectorAll("text")].filter(text => {
                    const box = text.getBoundingClientRect();
                    return box.width <= 0 || box.height <= 0 || box.left < card.left - 1 || box.right > card.right + 1
                        || box.top < card.top - 1 || box.bottom > card.bottom + 1;
                }).map(text => text.textContent);
            });
            return { count: cards.length, visible: cards.every(card => card.width > 100 && card.height > 40), overlap, clippedLabels };
        });
        assert(graph.count > 0 && graph.visible, "Graph must contain visible rendered cards");
        assert(!graph.overlap && graph.clippedLabels.length === 0, `Graph labels/cards overlap: ${JSON.stringify(graph)}`);
        measurements.graph = graph;
        const agent = page.locator("[data-agent]").first();
        const agentId = await agent.getAttribute("data-agent");
        await agent.focus();
        await page.keyboard.press("Enter");
        assert(await page.getByRole("tab", { name: "Detalhes", exact: true }).getAttribute("aria-selected") === "true", "Agent selection must open Details");
        assert(await page.locator("#details h3").textContent() === agentId, "Agent detail was lost");
        await page.locator("#details-back").click();
        assert(await page.evaluate(() => document.activeElement?.dataset?.agent) === agentId, "Back must restore agent focus");

        await page.getByRole("tab", { name: "Notas", exact: true }).click();
        const topic = page.locator("[data-topic]").first();
        assert(await topic.count() > 0, "Fixture must have actual recorded grades");
        await topic.click();
        assert(await page.getByRole("tab", { name: "Detalhes", exact: true }).getAttribute("aria-selected") === "true", "Topic selection must open Details");
        await page.locator("#details-back").click();
        assert(await page.getByRole("tab", { name: "Notas", exact: true }).getAttribute("aria-selected") === "true", "Back must return to scores");
        await page.getByRole("tab", { name: "Detalhes", exact: true }).click();
        await page.getByRole("button", { name: "output/fixture.md", exact: true }).click();
        await page.waitForFunction(() => document.getElementById("artifact-text").textContent.includes("<script>"));
        assert(await page.locator("#artifact-text script").count() === 0, "Artifact text must not become executable markup");
        const modal = await page.locator(".artifact-window").boundingBox();
        const frame = await page.locator("#dashboard").boundingBox();
        assert(modal.width <= frame.width && modal.height <= frame.height, "Artifact viewer escapes the compact panel");
        await page.keyboard.press("Escape");
        assert(await page.locator("#artifact-view").isHidden(), "Artifact viewer should close with Escape");
        await page.getByRole("tab", { name: "Histórico", exact: true }).click();
        assert(await page.locator("#timeline li").count() > 0, "History must remain accessible");
        assert(await page.locator("[role=tabpanel]:visible").count() === 1, "Only the selected panel should be visible");
        await page.getByRole("tab", { name: "Histórico", exact: true }).focus();
        await page.keyboard.press("Home");
        assert(await page.getByRole("tab", { name: "Fluxo", exact: true }).getAttribute("aria-selected") === "true", "Home navigation failed");
        await page.keyboard.press("ArrowRight");
        assert(await page.getByRole("tab", { name: "Notas", exact: true }).getAttribute("aria-selected") === "true", "Arrow-key navigation failed");

        const originalCycle = await page.locator("#cycle").inputValue();
        await page.locator("#context-menu summary").click();
        await page.locator("#cycle").selectOption("1");
        assert(await page.getByRole("tab", { name: "Notas", exact: true }).getAttribute("aria-selected") === "true", "Cycle updates reset the active tab");
        assert(await page.locator("#gate-badge").isVisible(), "Gate state must be visible in every tab");
        await page.locator("#context-menu summary").click();
        await page.locator("#cycle").selectOption(originalCycle);

        await page.getByRole("tab", { name: "Fluxo", exact: true }).click();
        await page.locator("#expand").click();
        const expanded = await page.locator("#dashboard").boundingBox();
        assert(expanded.width > 1000 && expanded.height > 700, "Expand did not use the available host area");
        await page.locator("#expand").click();
        await page.setViewportSize({ width: 720, height: 480 });
        measurements.reference = await page.evaluate(() => ({
            width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight,
            viewportWidth: innerWidth, viewportHeight: innerHeight,
            metricsHeight: document.querySelector(".metrics").getBoundingClientRect().height,
        }));
        assert(measurements.reference.width <= 720 && measurements.reference.height <= 480, "Reference-size page overflows");
        assert(measurements.reference.metricsHeight <= 48, "Reference summary is too tall");
        await page.setViewportSize({ width: 390, height: 480 });
        measurements.mobile = await page.evaluate(() => ({
            width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight,
            viewportWidth: innerWidth, viewportHeight: innerHeight,
        }));
        assert(measurements.mobile.width <= 390 && measurements.mobile.height <= 480, "Small host should scroll inside panels, not the whole page");
        measurements.longMetrics = await page.evaluate(() => {
            const cycle = document.getElementById("cycle-count");
            const grade = document.getElementById("grade-count");
            const original = [cycle.textContent, grade.textContent];
            cycle.textContent = "Pré-ciclo";
            grade.textContent = "Divergente";
            const fits = [cycle, grade].every(node => {
                const box = node.getBoundingClientRect();
                const parent = node.parentElement.getBoundingClientRect();
                return box.width > 0 && box.height > 0 && box.right <= parent.right && node.scrollWidth <= node.clientWidth + 1;
            });
            [cycle.textContent, grade.textContent] = original;
            return { fits };
        });
        assert(measurements.longMetrics.fits, "Long pending/error metrics overflow their compact slots");
        assert(errors.length === 0, `Browser errors: ${errors.join("; ")}`);
        return { ...measurements, errors, tabs: ["Fluxo", "Notas", "Histórico", "Detalhes"] };
    } finally {
        page.off("pageerror", onError);
        await page.keyboard.press("Escape");
        await page.setViewportSize({ width: 720, height: 480 });
        await page.getByRole("tab", { name: "Fluxo", exact: true }).click();
    }
}

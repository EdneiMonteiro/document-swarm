import * as sdk from "@github/copilot-sdk/extension";
import { CANVAS_ID, MonitorManager } from "./manager.mjs";
import { yieldsToProject } from "./ownership.mjs";

let session;
const active = !await yieldsToProject(process.env.EXTENSION_PATH, process.cwd());
const log = message => {
    if (session) session.log(message, { level: "warning" }).catch(error => process.stderr.write(`Monitor log: ${error.message}\n`));
    else process.stderr.write(`${message}\n`);
};
const manager = new MonitorManager({ getSession: () => session, log });
const parameters = {
    type: "object", additionalProperties: false, required: ["operation"],
    properties: {
        operation: { type: "string", enum: ["start", "phase", "dispatch", "handoff", "refresh", "finish", "open", "status", "recovery"] },
        swarm_path: { type: "string", description: "Absolute path to the explicitly selected swarm folder." },
        execution_id: { type: "string" },
        auto_open: { type: "boolean", description: "Defaults to true; false is useful for an explicit headless observation." },
        phase: { type: "string", enum: ["setup", "agents", "authors", "consolidation", "sources", "tables", "reviews", "rubber-duck", "gate", "delivery", "done"] },
        cycle: { type: "integer", minimum: 0 },
        agent_id: { type: "string", description: "Exact declared agent name, never a guessed runtime task id." },
        task_id: { type: "string", description: "Previously observed runtime task id, only for a follow-up." },
        from: { type: "string" }, to: { type: "string" }, label: { type: "string" },
        rule: { type: "string", enum: ["R1", "R2", "R3", "R4", "R5"], description: "Recovery rule applied by the watchdog, per SKILL.md section 2.6." },
        detail: { type: "string", description: "What was measured and what was redone; never a grade or an approval." },
        status: { type: "string", enum: ["completed", "escalated", "aborted"] },
        surface: { type: "string", enum: ["auto", "browser"] },
    },
};
const canvases = typeof sdk.createCanvas === "function" ? [sdk.createCanvas({
    id: CANVAS_ID,
    displayName: "Document Swarm Monitor",
    description: "Local read-only agent graph, cycles, reviewer scoreboard and history for a document swarm.",
    inputSchema: {
        type: "object", required: ["swarm_path"],
        properties: { swarm_path: { type: "string" }, execution_id: { type: "string" } },
        additionalProperties: false,
    },
    open: ctx => manager.canvasOpen(ctx),
    onClose: ctx => manager.canvasClose(ctx),
})] : [];

session = await sdk.joinSession({
    ...(active && canvases.length ? { canvases } : {}),
    tools: active ? [{
        name: "docswarm_monitor",
        description: "Observe document-swarm, never execute or control its agents. Start after brief creation (auto-opens canvas/browser). Register each dispatch first, then use its exact task_name on the real task tool; task_id is only for observed follow-ups. Publish real phase/cycle and artifact handoffs. Record every watchdog recovery with operation recovery, so a resumed execution never looks clean. Refresh reads artifacts; finish only records coordinator closure. No prompts, secrets or inferred grades. If unavailable, explicitly warn and continue document production in the terminal.",
        parameters,
        handler: async (args, invocation) => {
            try {
                return JSON.stringify(await manager.action(args, invocation.sessionId));
            } catch (error) {
                log(`Monitor: ${error.message}. Não altere o fluxo de qualidade; continue no terminal se necessário.`);
                return { resultType: "failure", textResultForLlm: `Monitor indisponível: ${error.message}. Preserve os artefatos e continue o fluxo documental no terminal.` };
            }
        },
    }] : [],
});

if (active) {
    for (const type of [
        "tool.execution_start", "tool.execution_complete", "subagent.started", "subagent.configured",
        "subagent.completed", "subagent.failed", "session.background_tasks_changed",
        "assistant.turn_start", "assistant.idle", "session.idle", "session.task_complete",
    ]) {
        session.on(type, event => manager.native(event));
    }
} else {
    await session.log("Monitor pessoal em espera: a extensão deste projeto fornece o painel.", { level: "info", ephemeral: true });
}
let closing = false;
const close = async () => {
    if (closing) return;
    closing = true;
    await manager.shutdown();
};
session.on("session.shutdown", () => { close().catch(error => log(error.message)); });
process.on("SIGTERM", () => { close().finally(() => process.exit(0)); });
process.on("SIGINT", () => { close().finally(() => process.exit(0)); });

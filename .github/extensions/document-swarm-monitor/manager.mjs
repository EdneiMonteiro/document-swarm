import { execFile } from "node:child_process";
import { EventEmitter } from "node:events";
import { watch, promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { RunStore, INACTIVITY_THRESHOLD, assertText, history, readHistorical } from "./state.mjs";
import { createMonitorServer } from "./server.mjs";
import { canvasWindowTitle, createCanvasWindow } from "./window.mjs";

const execute = promisify(execFile);
export const CANVAS_ID = "document-swarm-monitor";
const TERMINAL = new Set(["completed", "escalated", "aborted"]);
const EVENTS = new Set([
    "tool.execution_start", "tool.execution_complete", "subagent.started",
    "subagent.configured", "subagent.completed", "subagent.failed",
    "assistant.turn_start", "assistant.idle", "session.idle", "session.task_complete",
]);
const extensionDirectory = path.dirname(await fs.realpath(fileURLToPath(import.meta.url)));
const readerPath = path.resolve(extensionDirectory, "..", "..", "..", "scripts", "checks", "progress.py");
let pythonPromise;
const pythonEnvironment = () => ({ ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" });

async function interpreter() {
    if (!pythonPromise) {
        pythonPromise = (async () => {
            const failures = [];
            for (const name of ["python3", "python"]) {
                try {
                    const { stdout } = await execute(name, ["-X", "utf8", "-c", "import sys; assert sys.version_info.major == 3; print(sys.executable)"], {
                        timeout: 10000, windowsHide: true, encoding: "utf8", env: pythonEnvironment(),
                    });
                    return stdout.trim();
                } catch (error) {
                    failures.push(`${name}: ${error.code ?? "indisponível"}`);
                }
            }
            throw new Error(`Python 3 não disponível para o monitor (${failures.join("; ")}).`);
        })();
        pythonPromise.catch(() => { pythonPromise = undefined; });
    }
    return pythonPromise;
}

export async function readEvidence(root) {
    const python = await interpreter();
    const { stdout } = await execute(python, ["-X", "utf8", readerPath, root], {
        timeout: 15000, maxBuffer: 16 * 1024 * 1024, windowsHide: true,
        encoding: "utf8", env: pythonEnvironment(),
    });
    const data = JSON.parse(stdout);
    if (data.schema_version !== 1 || !Array.isArray(data.agents) || !Array.isArray(data.cycles)) {
        throw new Error("Contrato de artefatos incompatível com o monitor.");
    }
    return data;
}

export async function openBrowser(url) {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" || parsed.hostname !== "127.0.0.1" || !parsed.port || parsed.username || parsed.password) {
        throw new Error("O monitor só abre URLs locais geradas pelo servidor.");
    }
    if (process.platform === "win32") {
        const script = `Start-Process -FilePath '${url.replaceAll("'", "''")}'`;
        await execute("powershell.exe", ["-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", Buffer.from(script, "utf16le").toString("base64")], { timeout: 10000, windowsHide: true });
    } else if (process.platform === "darwin") {
        await execute("open", [url], { timeout: 10000 });
    } else {
        await execute("xdg-open", [url], { timeout: 10000 });
    }
}

export class MonitorManager {
    constructor({ getSession, log, reader = readEvidence, browser = openBrowser, windowFactory = createCanvasWindow,
                  threshold = INACTIVITY_THRESHOLD, heartbeat = 15000 }) {
        this.getSession = getSession;
        this.log = log;
        this.reader = reader;
        this.browser = browser;
        this.windowFactory = windowFactory;
        this.threshold = threshold;
        this.heartbeat = heartbeat;
        this.runs = new Map();
        this.starting = new Map();
        this.runtimeQueue = Promise.resolve();
        this.suppressClose = new Set();
        this.warned = new Set();
        this.stopping = false;
    }

    warn(key, message) {
        if (this.warned.has(key)) return;
        this.warned.add(key);
        this.log(message);
    }

    entry(id) {
        const entry = this.runs.get(assertText(id, "execution_id"));
        if (!entry) throw new Error("Execução não aberta nesta sessão. Use start com a pasta do swarm.");
        return entry;
    }

    info(entry) {
        const state = entry.store.publicState;
        return {
            enabled: true, execution_id: state.execution_id, swarm_id: state.swarm_id,
            canvas_id: CANVAS_ID, instance_id: entry.instanceId, url: entry.server.url,
            cycle: state.cycle, phase: state.phase, status: state.status,
            session_activity: state.session_activity ?? null,
            closure: state.closure ?? null,
            connected: entry.server.connected, surface: entry.surface, historical: entry.readOnly || TERMINAL.has(state.status),
            health: state.health ?? null,
            recoveries: (state.recoveries ?? []).map(item => ({ rule: item.rule, agent_id: item.agent_id, cycle: item.cycle, attempt: item.attempt })),
            warnings: [...state.evidence.warnings, ...(state.reader_error ? [state.reader_error] : [])],
        };
    }

    async start(swarmPath, { sessionId, executionId, autoOpen = true } = {}) {
        assertText(swarmPath, "swarm_path", 4000);
        assertText(sessionId, "session_id");
        const root = await fs.realpath(swarmPath);
        const key = `${root}\0${executionId ?? ""}`;
        if (this.starting.has(key)) return this.starting.get(key);
        const action = this.startRun(root, { sessionId, executionId, autoOpen });
        this.starting.set(key, action);
        try { return await action; }
        finally { this.starting.delete(key); }
    }

    async startRun(root, { sessionId, executionId, autoOpen }) {
        const existing = [...this.runs.values()].find(entry => entry.store.root === root
            && (executionId ? entry.store.state.execution_id === executionId : !entry.readOnly && !TERMINAL.has(entry.store.state.status)));
        if (existing) {
            if (autoOpen) await this.present(existing, "auto");
            return this.info(existing);
        }
        const evidence = await this.reader(root);
        if (!evidence.monitor_enabled) return { enabled: false, reason: "Monitor desativado no brief. Continue no terminal." };
        const previous = await history(root);
        let id = executionId;
        if (!id) id = previous.find(item => item.session_id === sessionId && !TERMINAL.has(item.status))?.execution_id;
        const old = id ? previous.find(item => item.execution_id === id) : null;
        let store;
        const readOnly = !!old && (old.session_id !== sessionId || TERMINAL.has(old.status));
        if (readOnly) {
            store = new EventEmitter();
            store.root = root;
            store.state = await readHistorical(root, id);
            store.publicState = store.state;
        } else {
            store = await RunStore.create(root, { sessionId, evidence, executionId: id });
            store.on("reader", error => this.warn(`store:${store.state.execution_id}:${error}`, `Monitor: ${error}. O fluxo documental pode continuar no terminal.`));
        }
        let server;
        try {
            server = await createMonitorServer(store, { log: message => this.warn(message, message) });
        } catch (error) {
            if (!readOnly) await store.close();
            throw error;
        }
        const entry = {
            store, server, readOnly, instanceId: `run-${store.state.execution_id}`,
            canvasTitle: canvasWindowTitle(store.state.title, store.state.execution_id),
            canvasUrl: null,
            watcher: null, refreshTimer: null, healthTimer: null, refreshing: null, refreshQueued: false, surface: "not_opened",
            openAttempted: false, browserOpened: false, viewerClosed: false,
        };
        this.runs.set(store.state.execution_id, entry);
        if (!readOnly) {
            try {
                entry.watcher = watch(root, { recursive: true }, (_event, file) => {
                    if (!file || this.stopping || TERMINAL.has(store.state.status)) return;
                    const relative = String(file).replaceAll("\\", "/");
                    if (relative === "reports/progress" || relative.startsWith("reports/progress/") || relative.endsWith(".tmp")) return;
                    clearTimeout(entry.refreshTimer);
                    entry.refreshTimer = setTimeout(() => this.refresh(entry).catch(error => this.reportReadError(entry, error)), 200);
                });
                entry.watcher.on("error", error => this.reportReadError(entry, error));
            } catch (error) {
                this.reportReadError(entry, error);
            }
            const session = this.getSession();
            if (store.state.dispatches.length && typeof session?.getEvents === "function") {
                try {
                    for (const event of await session.getEvents()) {
                        if (EVENTS.has(event.type)) await store.observe(event);
                    }
                } catch (error) {
                    this.warn(`replay:${store.state.execution_id}`, `Monitor: não foi possível reconciliar o histórico do SDK: ${error.message}`);
                }
            }
            await this.reconcile();
            await this.beat(entry);
        }
        if (autoOpen) await this.present(entry, "auto");
        return this.info(entry);
    }

    beat(entry) {
        if (entry.readOnly || entry.healthTimer) return;
        const publish = () => entry.store.publishHealth({ threshold: this.threshold })
            .catch(error => this.warn(`health:${error.message}`, `Monitor: não foi possível publicar a saúde da execução: ${error.message}`));
        entry.healthTimer = setInterval(() => {
            if (this.stopping || TERMINAL.has(entry.store.state.status)) {
                clearInterval(entry.healthTimer);
                entry.healthTimer = null;
                return;
            }
            publish();
        }, this.heartbeat);
        entry.healthTimer.unref?.();
        return publish();
    }

    reportReadError(entry, error) {
        entry.store.readerError = error.message;
        entry.store.emit("reader", error.message);
        this.warn(`read:${entry.store.state.execution_id}:${error.message}`, `Monitor: dados indisponíveis ou desatualizados (${error.message}).`);
    }

    async refresh(entry) {
        if (entry.readOnly || TERMINAL.has(entry.store.state.status)) return;
        entry.refreshQueued = true;
        if (entry.refreshing) return entry.refreshing;
        entry.refreshing = (async () => {
            for (let attempt = 0; attempt < 2; attempt++) {
                entry.refreshQueued = false;
                const evidence = await this.reader(entry.store.root);
                if (this.stopping || TERMINAL.has(entry.store.state.status)) return;
                await entry.store.updateEvidence(evidence);
                if (entry.store.readerError) {
                    await entry.store.repairSnapshot();
                    entry.store.readerError = null;
                    entry.store.emit("reader", null);
                }
                if (!entry.refreshQueued) break;
            }
        })();
        try { await entry.refreshing; }
        finally {
            entry.refreshing = null;
            if (entry.refreshQueued && !this.stopping && !TERMINAL.has(entry.store.state.status)) {
                clearTimeout(entry.refreshTimer);
                entry.refreshTimer = setTimeout(() => this.refresh(entry).catch(error => this.reportReadError(entry, error)), 200);
            }
        }
    }

    native(event) {
        if (this.stopping) return;
        this.runtimeQueue = this.runtimeQueue.then(async () => {
            if (EVENTS.has(event.type)) {
                for (const entry of this.runs.values()) {
                    if (entry.readOnly || TERMINAL.has(entry.store.state.status)) continue;
                    if (event.type === "session.idle" && !event.agentId && entry.store.state.status === "closing") {
                        await this.refresh(entry);
                    }
                    await entry.store.observe(event);
                    if (TERMINAL.has(entry.store.state.status)) {
                        clearTimeout(entry.refreshTimer);
                        clearInterval(entry.healthTimer);
                        entry.healthTimer = null;
                        entry.watcher?.close();
                        await entry.store.publishHealth({ threshold: this.threshold }).catch(() => {});
                    }
                    if (event.type === "tool.execution_complete" && event.data?.success === false) {
                        await entry.store.record("runtime", state => {
                            const dispatch = state.dispatches.find(item => item.tool_call_id === event.data.toolCallId && item.status === "queued");
                            return dispatch ? { dispatch_id: dispatch.id, status: "failed" } : null;
                        }, "runtime", { id: event.id, at: event.timestamp });
                    }
                }
            }
            if (event.type === "session.background_tasks_changed" || event.type.startsWith("subagent.")) await this.reconcile();
        }).catch(error => this.warn(`native:${error.message}`, `Monitor: falha na observação nativa: ${error.message}`));
    }

    async reconcile() {
        const session = this.getSession();
        if (typeof session?.rpc?.tasks?.list !== "function") {
            this.warn("tasks-unavailable", "Monitor: consulta de tarefas não disponível; estados não observados permanecerão explícitos.");
            return;
        }
        try {
            const at = new Date().toISOString();
            const result = await session.rpc.tasks.list();
            if (!Array.isArray(result.tasks)) throw new Error("Contrato de tarefas incompatível");
            for (const entry of this.runs.values()) {
                if (!entry.readOnly && !TERMINAL.has(entry.store.state.status)) await entry.store.reconcile(result.tasks, at);
            }
        } catch (error) {
            this.warn(`tasks:${error.message}`, `Monitor: não foi possível consultar estados reais das tarefas: ${error.message}`);
        }
    }

    async browserOnce(entry) {
        if (entry.browserOpened || entry.viewerClosed) return;
        entry.browserOpened = true;
        try {
            await this.browser(entry.server.url);
            entry.surface = "browser_requested";
            entry.server.waitForClient().then(connected => {
                if (!connected && !entry.viewerClosed && !this.stopping) {
                    this.warn(`no-viewer:${entry.store.state.execution_id}`, "Monitor: nenhum navegador confirmou conexão. Reabra com a operação open; o trabalho continua no terminal.");
                }
            });
        } catch (error) {
            entry.browserOpened = false;
            entry.surface = "unavailable";
            this.warn(`browser:${error.message}`, `Monitor: não foi possível abrir o navegador: ${error.message}. O fluxo documental continua no terminal.`);
        }
    }

    async present(entry, surface, force = false) {
        if (entry.openAttempted && !force) return;
        entry.openAttempted = true;
        entry.viewerClosed = false;
        if (force) entry.browserOpened = false;
        const session = this.getSession();
        if (surface !== "browser" && typeof session?.rpc?.canvas?.open === "function") {
            try {
                const old = session.openCanvases?.find(item => item.instanceId === entry.instanceId && item.canvasId === CANVAS_ID);
                if (old && old.url !== (entry.canvasUrl ?? entry.server.url) && session.rpc.canvas.close) {
                    this.suppressClose.add(entry.instanceId);
                    await session.rpc.canvas.close({ instanceId: entry.instanceId });
                }
                await session.rpc.canvas.open({
                    canvasId: CANVAS_ID, instanceId: entry.instanceId,
                    input: { swarm_path: entry.store.root, execution_id: entry.store.state.execution_id },
                });
                entry.surface = "canvas_requested";
                entry.server.waitForClient().then(async connected => {
                    if (!connected && !entry.viewerClosed && !this.stopping) {
                        this.warn(`canvas-unreachable:${entry.store.state.execution_id}`, "Monitor: canvas sem conexão confirmada; abrindo a alternativa no navegador local.");
                        await this.browserOnce(entry);
                    }
                }).catch(error => this.warn(`viewer:${error.message}`, `Monitor: ${error.message}`));
                return;
            } catch (error) {
                this.warn(`canvas:${error.message}`, `Monitor: canvas indisponível (${error.message}); usando navegador local.`);
            }
        }
        await this.browserOnce(entry);
    }

    async canvasOpen(ctx) {
        let entry = ctx.input?.execution_id ? this.runs.get(ctx.input.execution_id) : null;
        if (entry) {
            if (await fs.realpath(ctx.input.swarm_path) !== entry.store.root) throw new Error("Canvas points to a different swarm");
        } else {
            const result = await this.start(ctx.input?.swarm_path, {
                executionId: ctx.input?.execution_id, sessionId: ctx.sessionId, autoOpen: false,
            });
            if (!result.enabled) throw new Error(result.reason);
            entry = this.entry(result.execution_id);
        }
        entry.server.enableCanvas();
        if (!entry.canvasUrl) {
            const controller = this.windowFactory({
                title: entry.canvasTitle, ownerPid: Number(process.env.COPILOT_EXTENSION_PARENT_PID),
            });
            entry.canvasUrl = controller ? entry.server.attachWindow(controller) : entry.server.url;
        }
        return { url: entry.canvasUrl, title: entry.canvasTitle, status: "Monitor de leitura" };
    }

    canvasClose(ctx) {
        if (this.suppressClose.delete(ctx.instanceId)) return;
        const entry = [...this.runs.values()].find(item => item.instanceId === ctx.instanceId);
        if (entry) entry.viewerClosed = true;
    }

    async action(args, sessionId) {
        if (args.operation === "start") {
            return this.start(args.swarm_path, { sessionId, executionId: args.execution_id, autoOpen: args.auto_open !== false });
        }
        const entry = this.entry(args.execution_id);
        if (args.operation === "open") {
            await this.present(entry, args.surface ?? "auto", true);
            return this.info(entry);
        }
        if (args.operation === "status") {
            // The watchdog asks for status; an answer up to one heartbeat old would hide a fresh stall.
            if (!entry.readOnly && !TERMINAL.has(entry.store.state.status)) {
                await entry.store.publishHealth({ threshold: this.threshold })
                    .catch(error => this.warn(`health:${error.message}`, `Monitor: não foi possível medir a saúde da execução: ${error.message}`));
            }
            return this.info(entry);
        }
        if (entry.readOnly || TERMINAL.has(entry.store.state.status)) throw new Error("Execução histórica: apenas consulta e abertura são permitidas.");
        if (args.operation === "refresh") await this.refresh(entry);
        else if (args.operation === "phase") {
            await this.refresh(entry);
            await entry.store.phase(args.phase, args.cycle);
        } else if (args.operation === "dispatch") {
            await this.refresh(entry);
            await this.reconcile();
            return entry.store.prepareDispatch(args.agent_id, args.cycle, args.task_id);
        } else if (args.operation === "handoff") {
            await entry.store.record("handoff", {
                from: assertText(args.from, "from"), to: assertText(args.to, "to"),
                label: assertText(args.label, "label", 500), cycle: entry.store.state.cycle,
            });
        } else if (args.operation === "recovery") {
            await entry.store.record("recovery", {
                rule: args.rule, agent_id: args.agent_id ? assertText(args.agent_id, "agent_id") : null,
                cycle: Number.isSafeInteger(args.cycle) ? args.cycle : entry.store.state.cycle,
                detail: assertText(args.detail, "detail", 500),
            });
        } else if (args.operation === "finish") {
            await this.refresh(entry);
            await entry.store.record("finish", { status: args.status, await_runtime_idle: true });
        } else throw new Error("Operação de monitoramento desconhecida.");
        return this.info(entry);
    }

    async shutdown() {
        this.stopping = true;
        await this.runtimeQueue;
        for (const entry of this.runs.values()) {
            clearTimeout(entry.refreshTimer);
            clearInterval(entry.healthTimer);
            entry.healthTimer = null;
            entry.watcher?.close();
            try {
                if (entry.refreshing) await entry.refreshing;
                await entry.server.close();
                if (!entry.readOnly) await entry.store.close();
            } catch (error) {
                this.log(`Monitor: encerramento incompleto: ${error.message}`);
            }
        }
        this.runs.clear();
    }
}

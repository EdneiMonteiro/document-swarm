import { EventEmitter } from "node:events";
import { randomUUID } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";

export const PHASES = ["setup", "agents", "authors", "consolidation", "sources", "tables", "reviews", "rubber-duck", "gate", "delivery", "done"];
const STATES = new Set(["queued", "running", "idle", "completed", "failed", "cancelled", "unknown"]);
const ID = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}$/;
const now = () => new Date().toISOString();

export function assertText(value, label, maximum = 240) {
    if (typeof value !== "string" || !value.trim() || value.length > maximum) throw new Error(`${label}: invalid text`);
    return value;
}

export function inside(root, candidate) {
    const relative = path.relative(root, candidate);
    return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
}

function runtimeDispatch(state, { agentId, toolCallId, at, terminal = false }) {
    return [...state.dispatches].reverse().find(item => {
        if (!item.tool_call_id || (item.bound_at ?? item.registered_at) > at) return false;
        const sameAgent = agentId && (item.task_id === agentId || item.native_agent_id === agentId);
        const sameCall = toolCallId && (item.tool_call_id === toolCallId || item.origin_tool_call_id === toolCallId);
        if (!sameAgent && !sameCall) return false;
        // A queued follow-up cannot consume the preceding turn's late completion.
        return !terminal || !item.follow_up || (item.started_at && item.started_at <= at);
    });
}

export async function atomicJson(destination, data) {
    const temporary = path.join(path.dirname(destination), `.${path.basename(destination)}.${randomUUID()}.tmp`);
    try {
        const handle = await fs.open(temporary, "wx", 0o600);
        try {
            await handle.writeFile(`${JSON.stringify(data)}\n`, "utf8");
            await handle.sync();
        } finally {
            await handle.close();
        }
        await fs.rename(temporary, destination);
    } finally {
        await fs.unlink(temporary).catch(error => { if (error.code !== "ENOENT") throw error; });
    }
}

export function initialState({ executionId, sessionId, evidence }) {
    return {
        schema_version: 1, execution_id: executionId, session_id: sessionId,
        swarm_id: evidence.swarm_id, title: evidence.title,
        cycle: 0, phase: "setup", status: "observing", connection: "connected",
        created_at: now(), updated_at: now(), sequence: 0,
        agents: evidence.agents, dispatches: [], edges: [], events: [],
        session_activity: { status: "unknown", observed_at: null, stale: true },
        closure: null,
        seen_ids: [], evidence,
    };
}

export function reduce(state, event) {
    if (state.seen_ids.includes(event.id)) return state;
    const next = structuredClone(state);
    const data = event.data;
    if (event.type === "evidence") {
        if (data.schema_version !== 1 || data.swarm_id !== state.swarm_id || !Array.isArray(data.agents)) {
            throw new Error("Evidence does not belong to this swarm");
        }
        const roster = new Map(next.agents.map(agent => [agent.id, { ...agent, declaration_missing: true }]));
        for (const agent of data.agents) roster.set(agent.id, agent);
        next.agents = [...roster.values()];
        next.evidence = data;
        next.title = data.title;
    } else if (event.type === "phase") {
        if (!PHASES.includes(data.phase) || !Number.isSafeInteger(data.cycle) || data.cycle < next.cycle) {
            throw new Error("Invalid phase or backwards cycle");
        }
        next.phase = data.phase;
        next.cycle = data.cycle;
        next.status = "active";
        next.closure = null;
    } else if (event.type === "dispatch") {
        if (!next.agents.some(agent => agent.id === data.agent_id && !agent.declaration_missing)) throw new Error("Agent has no valid declaration");
        if (!Number.isSafeInteger(data.cycle) || data.cycle < 1 || data.cycle < next.cycle) throw new Error("Invalid dispatch cycle");
        if (data.task_id && !next.dispatches.some(item => item.task_id === data.task_id || item.native_agent_id === data.task_id)) {
            throw new Error("Follow-up task is not associated with this execution");
        }
        next.dispatches.push({ ...data, status: "queued", registered_at: event.at, observed_at: null });
    } else if (event.type === "binding") {
        const dispatch = next.dispatches.find(item => item.id === data.dispatch_id);
        if (!dispatch) throw new Error("Unknown dispatch");
        Object.assign(dispatch, data);
    } else if (event.type === "runtime") {
        const dispatch = next.dispatches.find(item => item.id === data.dispatch_id);
        const terminal = data.lifecycle === true && ["completed", "failed", "cancelled"].includes(data.status);
        if (!dispatch || (event.source === "runtime" && dispatch.observed_at && event.at < dispatch.observed_at
            && !(terminal && (!dispatch.started_at || dispatch.started_at <= event.at)))) return state;
        if (data.status && !STATES.has(data.status)) throw new Error("Unknown runtime status");
        const update = { ...data };
        if (["failed", "cancelled"].includes(dispatch.status) && data.status && !["failed", "cancelled"].includes(data.status)) {
            update.status = dispatch.status;
        }
        if (dispatch.status === "completed" && ["queued", "running", "idle", "unknown"].includes(data.status)) {
            update.status = "completed";
        }
        Object.assign(dispatch, update);
        if (event.source === "runtime") Object.assign(dispatch, {
            observed_at: dispatch.observed_at && dispatch.observed_at > event.at ? dispatch.observed_at : event.at,
            observation_stale: false,
        });
    } else if (event.type === "session") {
        if (!["processing", "waiting", "idle", "completion_declared"].includes(data.status)) throw new Error("Invalid session activity");
        if (next.session_activity?.observed_at && event.at < next.session_activity.observed_at) return state;
        next.session_activity = { status: data.status, signal: data.signal, observed_at: event.at, stale: false };
        if (data.status === "idle" && next.closure && next.status === "closing" && event.at >= next.closure.requested_at) {
            next.status = next.closure.status;
            next.phase = "done";
            next.closure.settled_at = event.at;
            next.closure.confirmation = "session.idle";
        }
    } else if (event.type === "handoff") {
        for (const id of [data.from, data.to]) {
            if (!next.agents.some(agent => agent.id === id)) throw new Error("Handoff endpoint is not a declared agent");
        }
        next.edges.push({ ...data, id: event.id, at: event.at, source: event.source });
    } else if (event.type === "finish") {
        if (!["completed", "escalated", "aborted"].includes(data.status)) throw new Error("Invalid finish status");
        next.closure = { status: data.status, requested_at: event.at, confirmation: "not_recorded" };
        next.status = data.await_runtime_idle === true ? "closing" : data.status;
        next.phase = data.await_runtime_idle === true ? "delivery" : "done";
    } else if (event.type === "connection") {
        if (!["connected", "disconnected", "historical"].includes(data.connection)) throw new Error("Invalid connection state");
        next.connection = data.connection;
        next.session_activity = { ...(next.session_activity ?? { status: "unknown", observed_at: null }), stale: true };
        for (const dispatch of next.dispatches) {
            if (["running", "idle", "queued", "unknown"].includes(dispatch.status)) dispatch.observation_stale = true;
        }
    } else {
        throw new Error(`Unsupported monitoring event: ${event.type}`);
    }
    next.sequence = event.sequence;
    next.updated_at = event.at;
    next.seen_ids = [...next.seen_ids, event.id].slice(-4096);
    const detail = event.type === "evidence" ? { agents: data.agents.length, cycles: data.cycles.length } : data;
    next.events = [...next.events, { ...event, data: detail }].slice(-1000);
    return next;
}

async function safeDirectory(root, destination) {
    if (!inside(root, destination)) throw new Error("Progress directory escapes swarm");
    const relative = path.relative(root, destination).split(path.sep).filter(Boolean);
    let current = root;
    for (const part of relative) {
        current = path.join(current, part);
        await fs.mkdir(current).catch(error => { if (error.code !== "EEXIST") throw error; });
        const real = await fs.realpath(current);
        if (!inside(root, real) || (await fs.lstat(current)).isSymbolicLink()) throw new Error("Progress directories must not be links");
    }
}

export class RunStore extends EventEmitter {
    static async create(root, { sessionId, evidence, executionId = randomUUID() }) {
        if (!ID.test(executionId)) throw new Error("Invalid execution identifier");
        const realRoot = await fs.realpath(root);
        const directory = path.join(realRoot, "reports", "progress", executionId);
        await safeDirectory(realRoot, directory);
        const ownerFile = path.join(directory, "owner.json");
        try {
            const previous = JSON.parse(await fs.readFile(ownerFile, "utf8"));
            if (!Number.isSafeInteger(previous.pid) || previous.pid < 1) throw new Error("Invalid existing monitor owner");
            let alive = true;
            try { process.kill(previous.pid, 0); }
            catch (error) {
                if (error.code === "ESRCH") alive = false;
                else if (error.code !== "EPERM") throw error;
            }
            if (alive) throw new Error("Another monitor may still own this execution");
            await fs.unlink(ownerFile);
        } catch (error) {
            if (error.code !== "ENOENT") throw error;
        }
        const owner = { pid: process.pid, session_id: sessionId, nonce: randomUUID() };
        const ownerHandle = await fs.open(ownerFile, "wx", 0o600);
        await ownerHandle.writeFile(JSON.stringify(owner), "utf8");
        await ownerHandle.close();
        const store = new RunStore(realRoot, directory, owner);
        try {
            let state;
            try {
                const file = path.join(directory, "snapshot.json");
                const info = await fs.stat(file);
                if (info.size > 16 * 1024 * 1024) throw new Error("Snapshot exceeds limit");
                state = JSON.parse(await fs.readFile(file, "utf8"));
                if (state.schema_version !== 1 || state.execution_id !== executionId
                    || state.session_id !== sessionId || state.swarm_id !== evidence.swarm_id) {
                    throw new Error("Snapshot belongs to a different execution or session");
                }
            } catch (error) {
                if (error.code !== "ENOENT") throw error;
                state = initialState({ executionId, sessionId, evidence });
            }
            try {
                const journalFile = path.join(directory, "events.jsonl");
                if ((await fs.stat(journalFile)).size > 64 * 1024 * 1024) throw new Error("Journal exceeds replay limit");
                const journal = await fs.readFile(journalFile, "utf8");
                const lines = journal.split("\n");
                for (let index = 0; index < lines.length; index++) {
                    if (!lines[index]) continue;
                    let entry;
                    try { entry = JSON.parse(lines[index]); }
                    catch (error) {
                        // Only a torn final append is recoverable; retain it separately for diagnosis.
                        if (index !== lines.length - 1) throw new Error("Corrupted monitor journal", { cause: error });
                        await fs.writeFile(path.join(directory, `torn-${randomUUID()}.txt`), lines[index], "utf8");
                        await fs.writeFile(journalFile, `${lines.slice(0, index).join("\n")}\n`, "utf8");
                        store.healthError = "Journal incompleto recuperado; diagnóstico preservado.";
                        break;
                    }
                    if (entry.sequence > state.sequence) state = reduce(state, entry);
                }
            } catch (error) {
                if (error.code !== "ENOENT") throw error;
            }
            store.state = state;
            await store.record("connection", { connection: "connected" }, "observer");
            await store.updateEvidence(evidence);
            return store;
        } catch (error) {
            await fs.unlink(ownerFile);
            throw error;
        }
    }

    constructor(root, directory, owner) {
        super();
        this.root = root;
        this.directory = directory;
        this.owner = owner;
        this.queue = Promise.resolve();
        this.healthError = null;
        this.closed = false;
    }

    get publicState() {
        const { seen_ids, ...state } = this.state;
        return { ...state, health_error: this.healthError };
    }

    record(type, producer, source = "coordinator", options = {}) {
        const pending = this.queue.then(async () => {
            if (this.closed) throw new Error("Monitor is closed");
            const data = typeof producer === "function" ? producer(this.state) : producer;
            if (data === null) return null;
            const event = {
                id: options.id ?? randomUUID(), sequence: this.state.sequence + 1,
                at: options.at ?? now(), source, type, data,
                cycle: data.cycle ?? this.state.dispatches.find(item => item.id === data.dispatch_id)?.cycle ?? this.state.cycle,
            };
            const next = reduce(this.state, event);
            if (next === this.state) return null;
            await fs.appendFile(path.join(this.directory, "events.jsonl"), `${JSON.stringify(event)}\n`, { encoding: "utf8", mode: 0o600 });
            // The journal is authoritative even if publishing the projection fails.
            this.state = next;
            await atomicJson(path.join(this.directory, "snapshot.json"), next);
            this.emit("change", this.publicState);
            return data;
        });
        this.queue = pending.catch(error => {
            this.healthError = error.message;
            this.emit("health", error.message);
        });
        return pending;
    }

    updateEvidence(evidence) {
        return this.record("evidence", state => JSON.stringify(state.evidence) === JSON.stringify(evidence) ? null : evidence, "artifacts");
    }

    async repairSnapshot() {
        await this.queue;
        await atomicJson(path.join(this.directory, "snapshot.json"), this.state);
    }

    phase(phase, cycle) {
        return this.record("phase", { phase, cycle });
    }

    prepareDispatch(agentId, cycle, taskId = null) {
        const id = randomUUID();
        return this.record("dispatch", state => {
            const agent = state.agents.find(item => item.id === agentId);
            const previous = taskId ? [...state.dispatches].reverse().find(item => item.task_id === taskId || item.native_agent_id === taskId) : null;
            return {
                id, agent_id: assertText(agentId, "agent"), cycle,
                task_name: `ds-${state.execution_id.slice(0, 8)}-${id.slice(0, 8)}`,
                task_id: taskId ? assertText(taskId, "task") : null,
                tool_call_id: null, native_agent_id: previous?.native_agent_id ?? null, observed_model: null,
                origin_tool_call_id: previous?.origin_tool_call_id ?? previous?.tool_call_id ?? null,
                follow_up: !!taskId, bound_at: null, started_at: null,
                declared_model: agent?.declared_model, role: agent?.role, agent_kind: agent?.kind,
            };
        });
    }

    async observe(event) {
        const data = event.data ?? {};
        const at = event.timestamp ?? now();
        if (!event.agentId && ["assistant.turn_start", "assistant.idle", "session.idle", "session.task_complete"].includes(event.type)) {
            if (at < this.state.created_at) return;
            const status = {
                "assistant.turn_start": "processing", "assistant.idle": "waiting",
                "session.idle": "idle", "session.task_complete": "completion_declared",
            }[event.type];
            await this.record("session", state => state.session_activity?.status === status && !state.session_activity.stale
                ? null : { status, signal: event.type }, "runtime", { id: event.id, at });
            return;
        }
        if (event.type === "tool.execution_start") {
            const name = (data.toolName ?? "").split(".").at(-1);
            if (!["task", "write_agent"].includes(name)) return;
            const args = data.arguments;
            if (!args || typeof args !== "object" || Array.isArray(args)) return;
            const ids = name === "write_agent" ? [args.agent_id, ...(Array.isArray(args.agent_ids) ? args.agent_ids : [])] : [];
            const matches = this.state.dispatches.filter(item => !item.tool_call_id
                && (name === "task" ? item.task_name === args.name : ids.includes(item.task_id)));
            for (const dispatch of matches) {
                await this.record("binding", {
                    dispatch_id: dispatch.id, tool_call_id: assertText(data.toolCallId, "tool call"),
                    bound_at: at,
                }, "runtime", { id: `${event.id}:${dispatch.id}`, at });
            }
            return;
        }
        if (event.agentId && ["assistant.turn_start", "assistant.idle"].includes(event.type)) {
            await this.record("runtime", state => {
                const dispatch = runtimeDispatch(state, { agentId: event.agentId, at });
                if (!dispatch || ["completed", "failed", "cancelled"].includes(dispatch.status)) return null;
                const status = event.type === "assistant.turn_start" ? "running" : data.aborted ? "cancelled" : "idle";
                if (dispatch.status === status && !dispatch.observation_stale && dispatch.native_agent_id === event.agentId) return null;
                return {
                    dispatch_id: dispatch.id, status, native_agent_id: event.agentId,
                    started_at: dispatch.started_at ?? (status === "running" ? at : null),
                    lifecycle: status === "cancelled",
                };
            }, "runtime", { id: event.id, at });
            return;
        }
        if (event.type === "subagent.configured") {
            if (typeof data.model !== "string" || data.model === "auto") return;
            await this.record("runtime", state => {
                const dispatch = runtimeDispatch(state, { agentId: event.agentId, at });
                return dispatch && dispatch.model_source !== "first_dispatched"
                    ? { dispatch_id: dispatch.id, observed_model: data.model, model_source: "runtime_selected" } : null;
            }, "runtime", { id: event.id, at });
            return;
        }
        if (!["subagent.started", "subagent.completed", "subagent.failed"].includes(event.type)) return;
        await this.record("runtime", state => {
            const dispatch = runtimeDispatch(state, {
                agentId: event.agentId, toolCallId: data.toolCallId, at,
                terminal: event.type !== "subagent.started",
            });
            if (!dispatch) return null;
            const status = event.type === "subagent.started" ? "running"
                : event.type === "subagent.failed" ? "failed" : data.cancelled ? "cancelled" : "completed";
            const update = {
                dispatch_id: dispatch.id, status,
                native_agent_id: event.agentId ?? dispatch.native_agent_id,
                started_at: dispatch.started_at ?? (status === "running" ? at : null),
                lifecycle: true,
            };
            const model = data.firstDispatchedModel ?? data.model;
            if (typeof model === "string" && model !== "auto") {
                update.observed_model = model;
                update.model_source = data.firstDispatchedModel ? "first_dispatched" : "runtime_selected";
            }
            return update;
        }, "runtime", { id: event.id, at });
    }

    async reconcile(tasks, at = now()) {
        for (const task of tasks) {
            if (task.type !== "agent" || !STATES.has(task.status)) continue;
            await this.record("runtime", state => {
                const candidates = state.dispatches.filter(item => (item.task_id && item.task_id === task.id)
                    || (item.tool_call_id && item.tool_call_id === task.toolCallId));
                const dispatch = [...candidates].reverse().find(item => {
                    if (item.tool_call_id === task.toolCallId) return true;
                    const changed = task.status === "running" ? task.activeStartedAt
                        : task.status === "idle" ? task.idleSince : task.completedAt;
                    return item.tool_call_id && changed && changed >= (item.bound_at ?? item.registered_at);
                });
                if (!dispatch) return null;
                const status = ["completed", "failed", "cancelled"].includes(dispatch.status) ? dispatch.status : task.status;
                const model = dispatch.model_source === "first_dispatched" ? dispatch.observed_model
                    : typeof task.resolvedModel === "string" && task.resolvedModel !== "auto" ? task.resolvedModel : dispatch.observed_model;
                if (dispatch.task_id === task.id && dispatch.status === status && dispatch.task_status === task.status
                    && dispatch.origin_tool_call_id === task.toolCallId
                    && dispatch.observed_model === model && !dispatch.observation_stale) return null;
                return {
                    dispatch_id: dispatch.id, task_id: task.id, status, task_status: task.status,
                    origin_tool_call_id: task.toolCallId,
                    started_at: dispatch.started_at ?? (task.status === "running" ? task.activeStartedAt ?? at : null),
                    observed_model: model, model_source: dispatch.model_source ?? "runtime_selected",
                };
            }, "runtime", { at });
        }
        const present = new Set(tasks.filter(task => task.type === "agent").map(task => task.id));
        for (const dispatch of this.state.dispatches) {
            if (dispatch.task_id && !present.has(dispatch.task_id) && !dispatch.observation_stale
                && ["running", "idle", "queued"].includes(dispatch.status)) {
                await this.record("runtime", { dispatch_id: dispatch.id, observation_stale: true }, "observer");
            }
        }
    }

    async close() {
        if (this.closed) return;
        await this.record("connection", { connection: "disconnected" }, "observer");
        await this.queue;
        this.closed = true;
        const ownerFile = path.join(this.directory, "owner.json");
        const current = JSON.parse(await fs.readFile(ownerFile, "utf8"));
        if (current.nonce !== this.owner.nonce) throw new Error("Execution owner changed; refusing lock removal");
        await fs.unlink(ownerFile);
    }
}

export async function history(root) {
    const base = path.join(root, "reports", "progress");
    let entries;
    try { entries = await fs.readdir(base, { withFileTypes: true }); }
    catch (error) { if (error.code === "ENOENT") return []; throw error; }
    const result = [];
    for (const entry of entries.slice(0, 1000)) {
        if (!entry.isDirectory() || !ID.test(entry.name)) continue;
        let data;
        try { data = await readHistorical(root, entry.name); }
        catch (error) {
            if (error.code === "ENOENT") continue;
            throw error;
        }
        result.push({
            execution_id: data.execution_id, session_id: data.session_id, swarm_id: data.swarm_id,
            title: data.title, created_at: data.created_at, updated_at: data.updated_at, status: data.status,
        });
    }

    return result.sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export async function readHistorical(root, id) {
    if (!ID.test(id)) throw new Error("Invalid historical execution identifier");
    const file = path.join(root, "reports", "progress", id, "snapshot.json");
    const real = await fs.realpath(file);
    if (!inside(root, real) || (await fs.stat(real)).size > 16 * 1024 * 1024) throw new Error("Unsafe historical snapshot");
    const data = JSON.parse(await fs.readFile(real, "utf8"));
    if (data.schema_version !== 1 || data.execution_id !== id) throw new Error("Invalid historical snapshot");
    const { seen_ids, ...publicData } = data;
    return { ...publicData, connection: "historical" };
}

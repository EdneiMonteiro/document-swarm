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

export const RECOVERY_RULES = new Set(["R1", "R2", "R3", "R4", "R5"]);
export const RECOVERY_LIMITS = { perAgentCycle: 2, perExecution: 6 };

// -- what the deterministic executor's journal says, as the projection in scripts/checks/executor_view.py hands it over --
// Everything in a batch crosses a file the executor writes and a process the monitor spawns, so it is read as untrusted:
// each event is validated whole before it changes anything, and the fields copied into a dispatch are named here, one
// by one, never spread from what arrived.
const STAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$/;
const EXECUTOR_TYPES = new Set(["phase", "dispatch", "runtime", "handoff", "executor", "finish"]);
const EXECUTOR_DISPATCH = /^x\d{1,9}$/;
const EXECUTOR_WORD = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
const EXECUTOR_IDENTITY = /^[0-9a-f]{1,64}$/;
const EXECUTOR_OUTCOMES = new Set(["accepted", "rejected", "null", "superseded"]);
const EXECUTOR_FINISH = new Set(["completed", "escalated"]);
export const EXECUTOR_BATCH_LIMIT = 20000;
export const EXECUTOR_READ_GRACE = 60;

function executorMoment(value) {
    if (typeof value !== "string" || !STAMP.test(value) || !Number.isFinite(Date.parse(value))) throw new Error("Invalid executor time");
    return value;
}

function executorText(value, label, maximum) {
    if (typeof value !== "string" || !value.trim() || value.length > maximum) throw new Error(`Invalid executor ${label}`);
    return value;
}

function executorInteger(value, label, minimum = 0) {
    if (!Number.isSafeInteger(value) || value < minimum || value > 1e9) throw new Error(`Invalid executor ${label}`);
    return value;
}

function executorWord(value, label) {
    if (typeof value !== "string" || !EXECUTOR_WORD.test(value)) throw new Error(`Invalid executor ${label}`);
    return value;
}

function declaredAgent(state, id) {
    const agent = state.agents.find(item => item.id === id && !item.declaration_missing);
    if (!agent) throw new Error("Agent has no valid declaration");
    return agent;
}

// What each kind of event may carry, with nothing else: the shape is validated and copied here, with no state, so that what is
// written to the journal of the execution is already clean, and so that applying an event cannot depend on a field nobody named.
const EXECUTOR_SHAPE = {
    phase(data) {
        const { phase, cycle } = data;
        if (!PHASES.includes(phase) || !Number.isSafeInteger(cycle) || cycle < 0) throw new Error("Invalid phase or backwards cycle");
        return { phase, cycle };
    },
    dispatch(data) {
        const id = data.id;
        if (typeof id !== "string" || !EXECUTOR_DISPATCH.test(id)) throw new Error("Invalid executor dispatch");
        if (data.identity !== null && !(typeof data.identity === "string" && EXECUTOR_IDENTITY.test(data.identity))) {
            throw new Error("Invalid executor identity");
        }
        return {
            id, agent_id: executorWord(data.agent_id, "agent"), cycle: executorInteger(data.cycle, "cycle", 1),
            round: executorInteger(data.round, "round"), stage: executorWord(data.stage, "stage"),
            attempt: executorInteger(data.attempt, "attempt", 1), label: executorText(data.label, "label", 300),
            executor_task: executorText(data.executor_task, "task", 200), identity: data.identity,
        };
    },
    runtime(data) {
        const clean = { dispatch_id: executorText(data.dispatch_id, "dispatch", 20) };
        if (!EXECUTOR_DISPATCH.test(clean.dispatch_id)) throw new Error("Invalid executor dispatch");
        if (data.status !== undefined) {
            if (!STATES.has(data.status)) throw new Error("Unknown runtime status");
            clean.status = data.status;
        }
        if (data.started_at !== undefined) clean.started_at = executorMoment(data.started_at);
        if (data.ended_at !== undefined) clean.ended_at = executorMoment(data.ended_at);
        if (data.outcome !== undefined) {
            if (!EXECUTOR_OUTCOMES.has(data.outcome)) throw new Error("Unknown executor outcome");
            clean.outcome = data.outcome;
        }
        if (data.seconds !== undefined) {
            if (typeof data.seconds !== "number" || !(data.seconds >= 0) || data.seconds >= 1e7) throw new Error("Invalid executor duration");
            clean.seconds = data.seconds;
        }
        if (data.observed_model !== undefined) clean.observed_model = executorText(data.observed_model, "model", 100);
        if (data.error !== undefined) clean.error = executorText(data.error, "reason", 300);
        if (data.cause !== undefined) clean.cause = executorText(data.cause, "cause", 60);
        return clean;
    },
    handoff(data) {
        return {
            from: executorWord(data.from, "agent"), to: executorWord(data.to, "agent"),
            label: executorText(data.label, "label", 500), cycle: executorInteger(data.cycle, "cycle"),
        };
    },
    executor(data) {
        return { kind: executorWord(data.kind, "kind"), label: executorText(data.label, "label", 300) };
    },
    finish(data) {
        if (!EXECUTOR_FINISH.has(data.status)) throw new Error("Invalid finish status");
        return { status: data.status };
    },
};

/** An event of the executor's journal as the projection hands it over, reduced to what it may carry; throws when it is not one. */
export function cleanExecutorEvent(item) {
    if (!item || typeof item !== "object" || Array.isArray(item) || !EXECUTOR_TYPES.has(item.type)) throw new Error("Unsupported executor event");
    if (!item.data || typeof item.data !== "object" || Array.isArray(item.data)) throw new Error("Executor event without data");
    return {
        seq: executorInteger(item.seq, "sequence", 1), n: executorInteger(item.n, "part"), at: executorMoment(item.at),
        type: item.type, cycle: executorInteger(item.cycle, "cycle"), data: EXECUTOR_SHAPE[item.type](item.data),
    };
}

// What an event does to the state, given the state: nothing is changed until everything it needs has been found.
const EXECUTOR_APPLY = {
    phase(next, data) {
        if (data.cycle < next.cycle) throw new Error("Invalid phase or backwards cycle");
        next.phase = data.phase;
        next.cycle = data.cycle;
        next.status = "active";
    },
    dispatch(next, data, { at }) {
        if (next.dispatches.some(item => item.id === data.id)) throw new Error("Repeated executor dispatch");
        const agent = declaredAgent(next, data.agent_id);
        if (data.cycle < next.cycle) throw new Error("Invalid dispatch cycle");
        next.dispatches.push({
            ...data, source: "executor",
            task_name: null, task_id: null, tool_call_id: null, native_agent_id: null, origin_tool_call_id: null,
            observed_model: null, model_source: null, follow_up: false, bound_at: null, started_at: null, ended_at: null,
            declared_model: agent.declared_model, role: agent.role, agent_kind: agent.kind,
            status: "queued", registered_at: at, observed_at: at,
        });
    },
    runtime(next, data, { at }) {
        const dispatch = next.dispatches.find(item => item.id === data.dispatch_id);
        if (!dispatch) throw new Error("Unknown executor dispatch");
        const { dispatch_id: _, observed_model: model, ...update } = data;
        if (model !== undefined) {
            update.observed_model = model;
            update.model_source = "executor_journal";
        }
        if (update.status) {
            // An ended dispatch stays ended, as a native one does.
            if (["failed", "cancelled"].includes(dispatch.status) && !["failed", "cancelled"].includes(update.status)) update.status = dispatch.status;
            if (dispatch.status === "completed" && ["queued", "running", "idle", "unknown"].includes(update.status)) update.status = "completed";
        }
        Object.assign(dispatch, update, { observed_at: at, observation_stale: false });
    },
    handoff(next, data, { at, id }) {
        declaredAgent(next, data.from);
        declaredAgent(next, data.to);
        next.edges.push({ ...data, id, at, source: "executor" });
    },
    executor() {},
    finish(next, data, { at }) {
        next.closure = { status: data.status, requested_at: at, settled_at: at, confirmation: "executor_journal" };
        next.status = data.status;
        next.phase = "done";
    },
};

function applyExecutorEvent(next, item, sequence) {
    const clean = cleanExecutorEvent(item);
    const id = `x${clean.seq}.${clean.n}`;
    EXECUTOR_APPLY[clean.type](next, clean.data, { at: clean.at, id });
    next.events.push({ id, sequence, at: clean.at, source: "executor", type: clean.type, cycle: clean.cycle, data: clean.data });
}

function applyExecutorBatch(state, next, data, sequence) {
    const known = state.executor ?? null;
    const dropped = data?.dropped ?? 0;
    if (!data || typeof data.epoch !== "string" || !STAMP.test(data.epoch) || !Number.isSafeInteger(data.cursor) || data.cursor < 0
        || !Array.isArray(data.events) || data.events.length > EXECUTOR_BATCH_LIMIT
        || !Number.isSafeInteger(dropped) || dropped < 0 || dropped > EXECUTOR_BATCH_LIMIT) {
        throw new Error("Invalid executor batch");
    }
    if (known && known.epoch !== data.epoch) throw new Error("The executor journal is not the one this execution follows");
    if (known && data.cursor <= known.cursor) return false;
    let skipped = (known?.skipped ?? 0) + dropped;
    for (const item of data.events) {
        try { applyExecutorEvent(next, item, sequence); }
        catch { skipped += 1; }
    }
    next.executor = { epoch: data.epoch, cursor: data.cursor, skipped };
    return true;
}

export function initialState({ executionId, sessionId, evidence }) {
    return {
        schema_version: 1, execution_id: executionId, session_id: sessionId,
        swarm_id: evidence.swarm_id, title: evidence.title,
        cycle: 0, phase: "setup", status: "observing", connection: "connected",
        created_at: now(), updated_at: now(), sequence: 0,
        agents: evidence.agents, dispatches: [], edges: [], events: [], recoveries: [],
        session_activity: { status: "unknown", observed_at: null, stale: true },
        closure: null,
        seen_ids: [], evidence,
    };
}

export const INACTIVITY_THRESHOLD = 180;
const TERMINAL_STATUS = new Set(["completed", "escalated", "aborted"]);
const DISPATCH_COUNTS = ["queued", "running", "idle", "completed", "failed", "cancelled", "unknown"];

function stamp(value) {
    const parsed = Date.parse(value ?? "");
    return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Measure how long this execution has been without any observed progress.
 *
 * The caller cannot trust `session_activity.status` alone: a wedged agent loop
 * stays on `processing` forever.  What separates work from a wedge is the age
 * of the newest observation, so the age decides and the label only explains.
 */
export function vitality(state, { threshold = INACTIVITY_THRESHOLD, at = Date.now() } = {}) {
    const marks = [];
    const session = state.session_activity ?? {};
    if (session.observed_at) marks.push([stamp(session.observed_at), `sessão ${session.status}`]);
    for (const dispatch of state.dispatches) {
        for (const [value, label] of [[dispatch.observed_at, "observação"], [dispatch.started_at, "início"],
            [dispatch.bound_at, "chamada"], [dispatch.registered_at, "registro"]]) {
            if (value) marks.push([stamp(value), `${label} de ${dispatch.agent_id}`]);
        }
    }
    // Last, so a stable sort keeps the specific signal ahead of the generic one on a tie.
    marks.push([stamp(state.updated_at), "último evento registrado"]);
    const newest = marks.filter(([value]) => value !== null).sort((a, b) => b[0] - a[0])[0] ?? null;
    const inactive = newest ? Math.max(0, (at - newest[0]) / 1000) : null;
    const counts = Object.fromEntries(DISPATCH_COUNTS.map(name => [name, 0]));
    let stale = 0;
    for (const dispatch of state.dispatches) {
        if (dispatch.status in counts) counts[dispatch.status] += 1;
        if (dispatch.observation_stale && ["queued", "running", "idle", "unknown"].includes(dispatch.status)) stale += 1;
    }
    const working = counts.running > 0;
    const everObserved = !!session.observed_at;
    let health;
    let reason;
    if (TERMINAL_STATUS.has(state.status)) {
        health = "closed";
        reason = `encerramento ${state.status} registrado`;
    } else if (state.connection !== "connected" || (everObserved && session.stale === true) || inactive === null) {
        health = "unobserved";
        reason = "a extensão não tem observação atual desta sessão";
    } else if (inactive > threshold * 3) {
        health = "stalled";
        reason = `nada observado há ${Math.round(inactive)} s, além do triplo do limiar`;
    } else if (inactive > threshold && !working) {
        health = "stalled";
        reason = `nada observado há ${Math.round(inactive)} s e nenhum agente em execução`;
    } else if (session.status === "processing") {
        health = "active";
        reason = "a sessão está processando dentro do limiar";
    } else if (working || counts.queued > 0) {
        health = "waiting";
        reason = `${counts.running} em execução e ${counts.queued} na fila dentro do limiar`;
    } else {
        health = "waiting";
        reason = `sem despacho em curso; última observação há ${Math.round(inactive)} s`;
    }
    return {
        state: health, reason, threshold_seconds: threshold,
        inactive_seconds: inactive === null ? null : Number(inactive.toFixed(1)),
        last_progress_at: newest ? new Date(newest[0]).toISOString() : null,
        last_progress_signal: newest ? newest[1] : null,
        observed_at: new Date(at).toISOString(),
        connection: state.connection,
        session_activity: { status: session.status ?? "unknown", observed_at: session.observed_at ?? null, stale: session.stale !== false },
        dispatches: counts,
        stale_dispatches: stale,
    };
}

const HEALTH_STATES = new Set(["active", "waiting", "stalled", "unobserved", "closed"]);

/** Whether the executor's journal was read recently enough for what it says to be taken as current. */
export function executorFeedFresh(live, at = Date.now()) {
    return (at - Date.parse(live?.observed_at ?? "")) / 1000 <= EXECUTOR_READ_GRACE;
}

/**
 * The state as it is published: the dispatches an executor journal reports are as observed as the last reading of that
 * journal, however long ago the monitor itself connected, and are not observed at all once the journal cannot be read.
 */
export function viewState(state, live, at = Date.now()) {
    if (!state.executor) return state;
    const fresh = executorFeedFresh(live, at);
    return {
        ...state,
        dispatches: state.dispatches.map(item => item.source === "executor" ? { ...item, observation_stale: !fresh } : item),
    };
}

/**
 * The health of a run an executor drives.  The executor classifies itself with the rule the `health` command uses
 * (journal, driver heartbeat, how long since either spoke), so a stall is told here as it is told there; the monitor adds
 * only what it alone knows, that it has stopped being able to read the journal.
 */
export function executorVitality(state, live, { threshold = INACTIVITY_THRESHOLD, at = Date.now() } = {}) {
    const measured = vitality(viewState(state, live, at), { threshold, at });
    if (TERMINAL_STATUS.has(state.status)) return measured;
    const reported = live?.health;
    if (!executorFeedFresh(live, at)) {
        return { ...measured, state: "unobserved", reason: "o monitor não tem uma leitura recente do journal do executor" };
    }
    if (!reported || !HEALTH_STATES.has(reported.state) || typeof reported.reason !== "string") {
        return { ...measured, state: "unobserved", reason: "o executor não informou uma classificação que o monitor conheça" };
    }
    return { ...measured, state: reported.state, reason: reported.reason };
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
    } else if (event.type === "recovery") {
        if (!RECOVERY_RULES.has(data.rule)) throw new Error("Unknown recovery rule");
        if (data.agent_id && !next.agents.some(agent => agent.id === data.agent_id)) throw new Error("Recovery target is not a declared agent");
        const recoveries = next.recoveries ?? [];
        if (recoveries.length >= RECOVERY_LIMITS.perExecution) {
            throw new Error(`Recovery ceiling reached for this execution (${RECOVERY_LIMITS.perExecution}); escalate to the user`);
        }
        const sameTarget = recoveries.filter(item => item.agent_id === data.agent_id && item.cycle === data.cycle);
        if (data.agent_id && sameTarget.length >= RECOVERY_LIMITS.perAgentCycle) {
            throw new Error(`Recovery ceiling reached for ${data.agent_id} in cycle ${data.cycle}; escalate to the user`);
        }
        next.recoveries = [...recoveries, { ...data, id: event.id, at: event.at, attempt: sameTarget.length + 1 }];
    } else if (event.type === "connection") {
        if (!["connected", "disconnected", "historical"].includes(data.connection)) throw new Error("Invalid connection state");
        next.connection = data.connection;
        next.session_activity = { ...(next.session_activity ?? { status: "unknown", observed_at: null }), stale: true };
        for (const dispatch of next.dispatches) {
            // The executor's journal is read again at every poll, so what it said stays as true as it was: whether it is
            // being read now is decided where the state is published.
            if (dispatch.source !== "executor" && ["running", "idle", "queued", "unknown"].includes(dispatch.status)) dispatch.observation_stale = true;
        }
    } else if (event.type === "executor_batch") {
        if (!applyExecutorBatch(state, next, data, event.sequence)) return state;
    } else {
        throw new Error(`Unsupported monitoring event: ${event.type}`);
    }
    next.sequence = event.sequence;
    next.updated_at = event.at;
    next.seen_ids = [...next.seen_ids, event.id].slice(-4096);
    // A batch is recorded as the events it holds, each with its own time, and not once more as the batch itself.
    if (event.type !== "executor_batch") {
        const detail = event.type === "evidence" ? { agents: data.agents.length, cycles: data.cycles.length } : data;
        next.events = [...next.events, { ...event, data: detail }];
    }
    next.events = next.events.slice(-1000);
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
                        store.readerError = "Journal incompleto recuperado; diagnóstico preservado.";
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
        this.readerError = null;
        this.health = null;
        this.executorLive = null;
        // Set by the manager once the swarm has an executor journal, whether or not a reading of it has worked yet.
        this.executorDriven = false;
        this.closed = false;
    }

    get publicState() {
        const { seen_ids, ...state } = viewState(this.state, this.executorLive);
        return { ...state, reader_error: this.readerError, health: this.health, executor_live: this.executorLive };
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
            this.readerError = error.message;
            this.emit("reader", error.message);
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

    /**
     * Apply what the executor's journal added since `cursor`, as one event: one journal line and one snapshot.
     * What is written is the events reduced to what they may carry, so nothing else the reader returned reaches the disk.
     */
    async observeExecutor(batch) {
        if (!Array.isArray(batch?.events)) throw new Error("Invalid executor batch");
        const events = [];
        let dropped = 0;
        for (const item of batch.events) {
            try { events.push(cleanExecutorEvent(item)); }
            catch { dropped += 1; }
        }
        return this.record("executor_batch", state => {
            const known = state.executor;
            if (known && known.epoch === batch.epoch && batch.cursor <= known.cursor) return null;
            return { epoch: batch.epoch, cursor: batch.cursor, events, dropped };
        }, "executor", { id: `xb:${batch.epoch}:${batch.cursor}` });
    }

    /**
     * What the last reading of the executor's journal and heartbeat found.  It is not part of the persisted state: it is
     * as old as the reading, and only the fields the panel shows are kept from what the reader returned.
     */
    setExecutorLive(result, { error = null, at = now() } = {}) {
        const before = this.executorLive ? JSON.stringify([this.executorLive.running, this.executorLive.driver?.state,
            this.executorLive.driver?.stage, this.executorLive.finished, this.executorLive.error]) : null;
        if (!result) {
            this.executorLive = null;
        } else {
            const summary = result.summary ?? {};
            const count = value => Number.isSafeInteger(value) && value >= 0 ? value : 0;
            const text = (value, maximum) => typeof value === "string" ? value.slice(0, maximum) : null;
            const driver = summary.driver && typeof summary.driver === "object" ? summary.driver : null;
            const health = result.health && typeof result.health === "object" ? result.health : {};
            this.executorLive = {
                observed_at: at, error,
                finished: summary.finished === true, outcome: text(summary.outcome, 20),
                cycle: Number.isSafeInteger(summary.cycle) ? summary.cycle : null,
                counts: Object.fromEntries(["issued", "accepted", "rejected", "null", "repairs"].map(name => [name, count(summary.counts?.[name])])),
                running: (Array.isArray(summary.running) ? summary.running : []).filter(row => row && typeof row === "object").slice(0, 100).map(row => ({
                    agent: text(row.agent, 100), label: text(row.label, 300), stage: text(row.stage, 100),
                    cycle: Number.isSafeInteger(row.cycle) ? row.cycle : null,
                    state: row.state === "running" ? "running" : "queued", since: text(row.since, 40),
                })),
                driver: driver && {
                    state: text(driver.state, 40), stage: text(driver.stage, 100), cycle: Number.isSafeInteger(driver.cycle) ? driver.cycle : null,
                    detail: text(driver.detail, 300), backend: text(driver.backend, 40),
                    age_seconds: typeof driver.age_seconds === "number" && driver.age_seconds >= 0 ? Math.round(driver.age_seconds) : null,
                },
                health: { state: text(health.state, 20), reason: text(health.reason, 400), threshold_seconds: count(health.threshold_seconds) },
                skipped_lines: count(result.skipped_lines),
            };
        }
        const after = this.executorLive ? JSON.stringify([this.executorLive.running, this.executorLive.driver?.state,
            this.executorLive.driver?.stage, this.executorLive.finished, this.executorLive.error]) : null;
        if (before !== after && !this.closed) this.emit("change", this.publicState);
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

    async publishHealth(options = {}) {
        if (this.closed) return this.health;
        const measured = this.state.executor || this.executorDriven ? executorVitality(this.state, this.executorLive, options) : vitality(this.state, options);
        const record = {
            schema_version: 1, execution_id: this.state.execution_id, swarm_id: this.state.swarm_id,
            cycle: this.state.cycle, phase: this.state.phase, status: this.state.status, ...measured,
        };
        const changed = this.health?.state !== record.state || this.health?.reason !== record.reason;
        this.health = record;
        // The file is the only channel that survives this process; publish it even when nothing changed.
        await atomicJson(path.join(this.directory, "health.json"), record);
        if (changed) this.emit("change", this.publicState);
        return record;
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

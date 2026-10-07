import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { RunStore, cleanExecutorEvent, executorFeedFresh, executorVitality, history, readHistorical, reduce, viewState, vitality } from "../state.mjs";
import { createMonitorServer, readArtifact } from "../server.mjs";
import { MonitorManager as BaseMonitorManager, readEvidence, readExecutor } from "../manager.mjs";
import { yieldsToProject } from "../ownership.mjs";
import { canvasWindowTitle, createCanvasWindow } from "../window.mjs";
import { AGENT_STATUS, APPROVAL_GRADES, OUTCOME_LABELS, agentStateText, agentStatus, approvalGrade, approvalLegend, archived, dispatchClock, dispatchStatus, executorLabel, executorWarnings, formatDuration, gradeCaption, gradeCount, gradeKind, gradeView, healthLabel, meetsApproval, runStatusText, sessionLabel, sessionWorking } from "../ui/app.mjs";

const digest = value => createHash("sha256").update(value).digest("hex");
// Hooks run in the order they were registered, so a manager shut down by a hook the test registered runs after the one that
// removes the folder it watches, and under load the watcher and the server it leaves behind keep the process from exiting.
// Every manager a test creates is shut down by the cleanup of its fixture, before the folder goes, whichever hook the test used.
const created = new Set();
class MonitorManager extends BaseMonitorManager {
    constructor(options) {
        super(options);
        created.add(this);
    }
}
async function fixture(t) {
    const temporary = await fs.mkdtemp(path.join(os.tmpdir(), "docswarm-monitor-"));
    const root = path.join(temporary, "swarm");
    await fs.mkdir(root);
    await fs.writeFile(path.join(root, "brief.md"), "# Fixture\n");
    const evidence = {
        schema_version: 1, swarm_id: "fixture", title: "Fixture", skill_version: "3.1.0",
        max_cycles: 3, monitor_enabled: true, demo: true, grade_scale: ["B+", "A-", "A", "A+"],
        agents: [
            { id: "coordinator", kind: "coordinator", role: "Coordination", declared_model: "auto", path: "agents/coordinator.md" },
            { id: "author-01", kind: "author", role: "Evidence", declared_model: "auto", path: "agents/author-01.md" },
        ],
        cycles: [], sources: { status: "pending", counts: {} }, warnings: [],
        artifacts: [{ id: "brief", name: "brief.md", path: "brief.md", sha256: digest("# Fixture\n"), bytes: 10 }],
    };
    const resources = [];
    t.after(async () => {
        for (const manager of created) await manager.shutdown();
        created.clear();
        for (const resource of resources.reverse()) await resource.close();
        await fs.rm(temporary, { recursive: true, force: true });
    });
    return { root, temporary, evidence, resources };
}
function native(type, data, { id = crypto.randomUUID(), at = new Date().toISOString(), agentId = "native-agent" } = {}) {
    return { id, type, timestamp: at, agentId, data };
}
async function spawned(store, cycle = 1, taskId = null) {
    await store.phase("authors", cycle);
    const dispatch = await store.prepareDispatch("author-01", cycle, taskId);
    const call = `call-${cycle}`;
    await store.observe(native("tool.execution_start", {
        toolName: taskId ? "write_agent" : "task", toolCallId: call,
        arguments: taskId ? { agent_id: taskId, message: "PRIVATE_MESSAGE" } : { name: dispatch.task_name, prompt: "PRIVATE_PROMPT" },
    }));
    await store.observe(native("subagent.started", { toolCallId: call, model: "selected-model" }));
    return { dispatch, call };
}

test("planned agents are not executing; unrelated SDK data is never journaled", async t => {
    const f = await fixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    assert.equal(store.state.dispatches.length, 0);
    const before = store.state.sequence;
    await store.observe(native("tool.execution_start", { toolName: "task", toolCallId: "other", arguments: { name: "unrelated", prompt: "PRIVATE_PROMPT" } }));
    assert.equal(store.state.sequence, before);
    const { dispatch } = await spawned(store);
    assert.equal(store.state.dispatches.find(item => item.id === dispatch.id).status, "running");
    assert.ok(!(await fs.readFile(path.join(store.directory, "events.jsonl"), "utf8")).includes("PRIVATE_PROMPT"));
});

test("cancelled completion remains cancelled, duplicates and late starts do not change it", async t => {
    const f = await fixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    const { call } = await spawned(store);
    const at = new Date(Date.now() + 100).toISOString();
    const complete = native("subagent.completed", { toolCallId: call, cancelled: true, firstDispatchedModel: "actual-model" }, { id: "cancelled", at });
    await store.observe(complete);
    const sequence = store.state.sequence;
    await store.observe(complete);
    assert.equal(store.state.sequence, sequence);
    await store.observe(native("subagent.started", { toolCallId: call }, { at: new Date(Date.parse(at) - 10).toISOString() }));
    await store.observe(native("subagent.completed", { toolCallId: call }, { at: new Date(Date.parse(at) + 10).toISOString() }));
    await store.reconcile([{ type: "agent", id: "task-one", toolCallId: call, status: "completed", resolvedModel: "selected-model" }], new Date(Date.parse(at) + 20).toISOString());
    const item = store.state.dispatches[0];
    assert.equal(item.status, "cancelled");
    assert.equal(item.observed_model, "actual-model");
    assert.equal(item.model_source, "first_dispatched");
});

test("follow-up dispatch gets its own cycle and old task snapshots do not start it", async t => {
    const f = await fixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    const first = await spawned(store);
    await store.reconcile([{ type: "agent", id: "task-one", toolCallId: first.call, status: "idle", prompt: "PRIVATE_PROMPT" }]);
    const second = await store.prepareDispatch("author-01", 2, "task-one");
    await store.observe(native("tool.execution_start", { toolName: "write_agent", toolCallId: "follow-up", arguments: { agent_id: "task-one", message: "PRIVATE_MESSAGE" } }));
    await store.reconcile([{ type: "agent", id: "task-one", toolCallId: first.call, status: "running", activeStartedAt: "2000-01-01T00:00:00.000Z" }]);
    assert.equal(store.state.dispatches.find(item => item.id === second.id).status, "queued");
    const bound = store.state.dispatches.find(item => item.id === second.id).bound_at;
    await store.reconcile([{ type: "agent", id: "task-one", toolCallId: first.call, status: "running", activeStartedAt: new Date(Date.parse(bound) + 1).toISOString() }]);
    assert.equal(store.state.dispatches.find(item => item.id === second.id).status, "running");
    const journal = await fs.readFile(path.join(store.directory, "events.jsonl"), "utf8");
    assert.ok(!journal.includes("PRIVATE_MESSAGE"));
    assert.ok(!journal.includes("PRIVATE_PROMPT"));
    await assert.rejects(store.prepareDispatch("author-01", 2, "foreign-task"), /not associated/);
});

test("one writer per execution; reconnect marks old activity unobserved until reconciled", async t => {
    const f = await fixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    const first = await spawned(store);
    await store.reconcile([{ type: "agent", id: "task-one", toolCallId: first.call, status: "running" }]);
    const id = store.state.execution_id;
    await assert.rejects(RunStore.create(f.root, { sessionId: "session", evidence: f.evidence, executionId: id }), /Another monitor/);
    await store.close();
    const resumed = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence, executionId: id });
    f.resources.push(resumed);
    assert.equal(resumed.state.dispatches[0].observation_stale, true);
    await resumed.reconcile([{ type: "agent", id: "task-one", toolCallId: first.call, status: "running" }]);
    assert.equal(resumed.state.dispatches[0].observation_stale, false);
    await resumed.reconcile([]);
    assert.equal(resumed.state.dispatches[0].observation_stale, true);
    assert.equal((await history(f.root)).length, 1);
    assert.equal((await readHistorical(f.root, id)).connection, "historical");
});

test("a failed snapshot publication cannot reuse sequence numbers or lose its journal event", async t => {
    const f = await fixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    const file = path.join(store.directory, "snapshot.json");
    await fs.rename(file, `${file}.backup`);
    await fs.mkdir(file);
    const before = store.state.sequence;
    await assert.rejects(store.phase("authors", 1));
    assert.equal(store.state.sequence, before + 1);
    assert.ok(store.readerError);
    await fs.rmdir(file);
    await store.repairSnapshot();
    await store.phase("reviews", 1);
    const entries = (await fs.readFile(path.join(store.directory, "events.jsonl"), "utf8")).trim().split("\n").map(JSON.parse);
    assert.equal(new Set(entries.map(item => item.sequence)).size, entries.length);
    assert.ok(entries.some(item => item.type === "phase" && item.data.phase === "authors"));
    assert.equal(JSON.parse(await fs.readFile(file, "utf8")).phase, "reviews");
});

test("HTTP is loopback, authenticated, read-only and origin checked", async t => {
    const f = await fixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    const server = await createMonitorServer(store);
    f.resources.push(server);
    const token = new URLSearchParams(new URL(server.url).hash.slice(1)).get("token");
    const headers = { Authorization: `Bearer ${token}` };
    assert.equal(new URL(server.url).hostname, "127.0.0.1");
    assert.equal((await fetch(`${server.origin}/api/state`)).status, 401);
    assert.equal((await fetch(`${server.origin}/api/state`, { headers })).status, 200);
    assert.equal(server.connected, false, "a fetched snapshot alone is not proof of a connected viewer");
    assert.equal((await fetch(`${server.origin}/api/state`, { headers: { ...headers, Origin: "https://untrusted.example" } })).status, 403);
    assert.equal((await fetch(`${server.origin}/api/state`, { headers: { ...headers, Origin: "null" } })).status, 403);
    server.enableCanvas();
    assert.equal((await fetch(`${server.origin}/api/state`, { headers: { ...headers, Origin: "null" } })).status, 200);
    assert.equal((await fetch(`${server.origin}/api/state`, { method: "POST", headers })).status, 405);
    assert.equal((await fetch(`${server.origin}/../../.git/config`, { headers })).status, 404);
    const asset = await fetch(`${server.origin}/`);
    assert.equal(asset.status, 200);
    assert.match(asset.headers.get("content-security-policy"), /script-src 'self'/);
    assert.ok(!(await asset.text()).includes(token));
    const abort = new AbortController();
    const response = await fetch(`${server.origin}/api/events`, { headers, signal: abort.signal });
    const reader = response.body.getReader();
    const first = await reader.read();
    assert.match(new TextDecoder().decode(first.value), /event: state/);
    assert.equal(server.connected, true);
    await store.phase("authors", 1);
    const second = await reader.read();
    assert.match(new TextDecoder().decode(second.value), /"phase":"authors"/);
    abort.abort();
});

test("artifact access is allowlisted, hash-bound, contained and returned as inert text", async t => {
    const f = await fixture(t);
    const state = { evidence: f.evidence };
    assert.equal((await readArtifact(f.root, state, "brief")).text, "# Fixture\n");
    await assert.rejects(readArtifact(f.root, state, "../outside"), /não autorizado/);
    await fs.writeFile(path.join(f.root, "brief.md"), "<script>window.injected=true</script>");
    await assert.rejects(readArtifact(f.root, state, "brief"), /alterado/);
    const outside = path.join(f.temporary, "private.md");
    await fs.writeFile(outside, "PRIVATE");
    const poisoned = { evidence: { artifacts: [{ id: "outside", path: "../private.md", sha256: digest("PRIVATE") }] } };
    await assert.rejects(readArtifact(f.root, poisoned, "outside"), /fora do swarm/);
});

test("manager opens canvas without recursive start deadlock and keeps monitoring after view close", { timeout: 10000 }, async t => {
    const f = await fixture(t);
    let opened = 0;
    let browsers = 0;
    const warnings = [];
    const session = { openCanvases: [], rpc: { tasks: { list: async () => ({ tasks: [] }) }, canvas: {} } };
    const manager = new MonitorManager({ getSession: () => session, reader: async () => f.evidence, log: message => warnings.push(message), browser: async () => { browsers++; } });
    f.resources.push({ close: () => manager.shutdown() });
    session.rpc.canvas.open = async params => {
        opened++;
        return manager.canvasOpen({ ...params, sessionId: "session" });
    };
    const info = await manager.start(f.root, { sessionId: "session", executionId: "explicit-execution" });
    assert.equal(info.surface, "canvas_requested");
    assert.equal(opened, 1);
    await manager.start(f.root, { sessionId: "session" });
    assert.equal(opened, 1);
    manager.canvasClose({ instanceId: info.instance_id });
    await manager.action({ operation: "phase", execution_id: info.execution_id, phase: "authors", cycle: 1 }, "session");
    assert.equal(manager.entry(info.execution_id).store.state.phase, "authors");
    assert.equal(browsers, 0);
    assert.deepEqual(warnings, []);
});

test("browser fallback is explicit and only opens once; disabled brief writes nothing", async t => {
    const f = await fixture(t);
    let opened = 0;
    const warnings = [];
    const session = { rpc: { tasks: { list: async () => ({ tasks: [] }) }, canvas: { open: async () => { throw new Error("unsupported host"); } } } };
    const manager = new MonitorManager({ getSession: () => session, reader: async () => f.evidence, log: message => warnings.push(message), browser: async () => { opened++; } });
    f.resources.push({ close: () => manager.shutdown() });
    const result = await manager.start(f.root, { sessionId: "session" });
    await manager.start(f.root, { sessionId: "session" });
    assert.equal(opened, 1);
    assert.equal(result.surface, "browser_requested");
    assert.ok(warnings.some(message => message.includes("canvas indisponível")));
    const disabled = path.join(f.temporary, "disabled");
    await fs.mkdir(disabled);
    const other = new MonitorManager({ getSession: () => session, reader: async () => ({ ...f.evidence, monitor_enabled: false }), log: () => {}, browser: async () => assert.fail("must not open") });
    assert.equal((await other.start(disabled, { sessionId: "session" })).enabled, false);
    assert.deepEqual(await fs.readdir(disabled), []);
});

test("separate swarms do not receive each other's task events", async t => {
    const f = await fixture(t);
    const secondRoot = path.join(f.temporary, "second");
    await fs.mkdir(secondRoot);
    const session = { rpc: { tasks: { list: async () => ({ tasks: [] }) } } };
    const manager = new MonitorManager({ getSession: () => session, reader: async root => ({ ...f.evidence, swarm_id: root === f.root ? "one" : "two" }), log: () => {}, browser: async () => {} });
    f.resources.push({ close: () => manager.shutdown() });
    const first = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    const second = await manager.start(secondRoot, { sessionId: "session", autoOpen: false });
    const dispatch = await manager.action({ operation: "dispatch", execution_id: first.execution_id, agent_id: "author-01", cycle: 1 }, "session");
    manager.native(native("tool.execution_start", { toolName: "task", toolCallId: "bound", arguments: { name: dispatch.task_name } }));
    manager.native(native("subagent.started", { toolCallId: "bound", model: "actual" }));
    await manager.runtimeQueue;
    assert.equal(manager.entry(first.execution_id).store.state.dispatches[0].status, "running");
    assert.equal(manager.entry(second.execution_id).store.state.dispatches.length, 0);
});

test("Python bridge preserves Portuguese and emoji even with an inherited legacy encoding", async t => {
    const f = await fixture(t);
    const title = "Demonstração · execução · revisão · ação 🚀";
    await fs.writeFile(path.join(f.root, "brief.md"), `---\nswarm_id: fixture\nskill_version: "3.1.0"\nmax_cycles: 3\n---\n# ${title}\r\n`, "utf8");
    const previous = process.env.PYTHONIOENCODING;
    process.env.PYTHONIOENCODING = "cp1252";
    try {
        assert.equal((await readEvidence(f.root)).title, title);
    } finally {
        if (previous === undefined) delete process.env.PYTHONIOENCODING;
        else process.env.PYTHONIOENCODING = previous;
    }
});

test("an update requested during an artifact read is not lost", async t => {
    const f = await fixture(t);
    let reads = 0;
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    const session = { rpc: { tasks: { list: async () => ({ tasks: [] }) } } };
    const manager = new MonitorManager({
        getSession: () => session, log: () => {}, browser: async () => {},
        reader: async () => {
            reads++;
            if (reads === 2) { await pending; return f.evidence; }
            return reads > 2 ? { ...f.evidence, title: "Updated evidence" } : f.evidence;
        },
    });
    f.resources.push({ close: () => manager.shutdown() });
    const info = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    const entry = manager.entry(info.execution_id);
    const first = manager.refresh(entry);
    const second = manager.refresh(entry);
    release();
    await Promise.all([first, second]);
    assert.equal(entry.store.state.title, "Updated evidence");
    assert.equal(reads, 3);
});

test("a later invocation on the same native task cannot restart a completed old dispatch", async t => {
    const f = await fixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    const { call } = await spawned(store);
    const completed = new Date(Date.now() + 10).toISOString();
    await store.observe(native("subagent.completed", { toolCallId: call }, { at: completed }));
    await store.observe(native("subagent.started", { toolCallId: call }, { at: new Date(Date.parse(completed) + 1).toISOString() }));
    assert.equal(store.state.dispatches[0].status, "completed");
});

test("personal registration yields to a project copy without depending on clone identity", async t => {
    const f = await fixture(t);
    const project = path.join(f.temporary, "project");
    const local = path.join(project, ".github", "extensions", "document-swarm-monitor", "extension.mjs");
    const personal = path.join(f.temporary, "home", ".copilot", "extensions", "document-swarm-monitor", "extension.mjs");
    await fs.mkdir(path.dirname(local), { recursive: true });
    await fs.mkdir(path.join(project, ".git"));
    await fs.mkdir(path.join(project, "nested"));
    await fs.writeFile(local, "// independent project copy");
    assert.equal(await yieldsToProject(personal, project), true);
    assert.equal(await yieldsToProject(personal, path.join(project, "nested")), true);
    assert.equal(await yieldsToProject(local, project), false);
    assert.equal(await yieldsToProject(personal, f.root), false);
    assert.equal(await yieldsToProject(undefined, project), false);
});

test("canvas resizing is bound to the exact owner and execution title", async () => {
    const title = canvasWindowTitle("Monitor de demonstração", "execution-01");
    assert.notEqual(title, canvasWindowTitle("Monitor de demonstração", "execution-02"));
    assert.ok(canvasWindowTitle("🎬".repeat(150), "x".repeat(100)).length <= 260);
    assert.ok(!canvasWindowTitle("First\nSecond", "execution-01").includes("\n"));
    assert.equal(createCanvasWindow({ title, ownerPid: 200, platform: "linux" }), null);
    assert.equal(createCanvasWindow({ title, ownerPid: NaN, platform: "win32" }), null);
    const requests = [];
    let fails = true;
    const window = createCanvasWindow({
        title, ownerPid: 200, platform: "win32",
        run: async (command, args, options) => {
            requests.push({ command, args, options });
            if (fails) { fails = false; throw new Error("Window not found"); }
            return { stdout: JSON.stringify({ fitted: true, mode: args.at(-1), owner_pid: 200, client_width: 900, client_height: 600 }) };
        },
    });
    await assert.rejects(window.resize("arbitrary"), /Unsupported window mode/);
    await assert.rejects(window.resize("compact"), /Window not found/);
    const measured = await window.resize("compact");
    assert.equal(measured.fitted, true);
    const { command, args, options } = requests.at(-1);
    assert.equal(command, "powershell.exe");
    assert.equal(args[args.indexOf("-OwnerPid") + 1], "200");
    assert.equal(args[args.indexOf("-WindowTitle") + 1], title);
    assert.equal(options.windowsHide, true);
    const invalid = createCanvasWindow({
        title, ownerPid: 200, platform: "win32",
        run: async () => ({ stdout: JSON.stringify({ fitted: true, mode: "compact", owner_pid: 201, client_width: 720, client_height: 480 }) }),
    });
    await assert.rejects(invalid.resize("compact"), /could not be verified/);
});

test("window endpoint accepts only authenticated, window-bound preset resizing", async t => {
    const f = await fixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    const server = await createMonitorServer(store);
    f.resources.push(server);
    const token = new URLSearchParams(new URL(server.url).hash.slice(1)).get("token");
    const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
    const calls = [];
    const canvasUrl = server.attachWindow({ resize: async mode => { calls.push(mode); return { fitted: true, mode }; } });
    const key = new URLSearchParams(new URL(canvasUrl).hash.slice(1)).get("window");
    assert.equal(new URLSearchParams(new URL(server.url).hash.slice(1)).has("window"), false);
    const post = (body, extraHeaders = headers) => fetch(`${server.origin}/api/window`, { method: "POST", headers: extraHeaders, body: JSON.stringify(body) });
    assert.equal((await post({ mode: "compact", window_key: key }, { "Content-Type": "application/json" })).status, 401);
    assert.equal((await post({ mode: "compact", window_key: key }, { ...headers, Origin: "https://other.example" })).status, 403);
    assert.equal((await post({ mode: "compact", window_key: "0".repeat(32) })).status, 403);
    assert.equal((await post({ mode: "compact", window_key: "ç".repeat(32) })).status, 403);
    assert.equal((await post({ mode: "compact", window_key: key, owner_pid: 200 })).status, 400);
    assert.equal((await post({ mode: "close", window_key: key })).status, 400);
    assert.deepEqual(calls, []);
    assert.equal((await post({ mode: "compact", window_key: key })).status, 200);
    assert.equal((await post({ mode: "expanded", window_key: key })).status, 200);
    assert.deepEqual(calls, ["compact", "expanded"]);
    assert.equal((await fetch(`${server.origin}/api/state`, { method: "POST", headers })).status, 405);
    assert.ok(!(await fs.readFile(path.join(store.directory, "snapshot.json"), "utf8")).includes(key));
});

test("canvas uses its fitted URL without closing it on an ordinary refocus", async t => {
    const f = await fixture(t);
    let closes = 0;
    const session = { openCanvases: [], rpc: { tasks: { list: async () => ({ tasks: [] }) }, canvas: {} } };
    const manager = new MonitorManager({
        getSession: () => session, reader: async () => f.evidence, browser: async () => {}, log: () => {},
        windowFactory: () => ({ resize: async mode => ({ fitted: true, mode }) }),
    });
    f.resources.push({ close: () => manager.shutdown() });
    session.rpc.canvas.close = async () => { closes++; };
    session.rpc.canvas.open = async params => {
        const result = await manager.canvasOpen({ ...params, sessionId: "session" });
        session.openCanvases = [{ ...result, instanceId: params.instanceId, canvasId: params.canvasId }];
        return result;
    };
    const info = await manager.start(f.root, { sessionId: "session" });
    assert.match(session.openCanvases[0].url, /&window=[a-f0-9]{32}$/);
    assert.ok(session.openCanvases[0].title.includes(info.execution_id));
    await manager.action({ operation: "open", execution_id: info.execution_id }, "session");
    assert.equal(closes, 0);
});

test("follow-up lifecycle may keep the original call ID and finish after a newer idle snapshot", async t => {
    const f = await fixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    const first = await spawned(store);
    await store.reconcile([{ type: "agent", id: "task-one", toolCallId: first.call, status: "idle" }]);
    await store.observe(native("subagent.completed", { toolCallId: first.call }));
    const next = await store.prepareDispatch("author-01", 2, "task-one");
    await store.observe(native("tool.execution_start", {
        toolName: "write_agent", toolCallId: "follow-up-call", arguments: { agent_id: "task-one" },
    }));
    const bound = store.state.dispatches.at(-1).bound_at;
    await store.observe(native("subagent.completed", { toolCallId: first.call }, {
        agentId: "task-one", at: new Date(Date.parse(bound) - 1).toISOString(),
    }));
    assert.equal(store.state.dispatches.at(-1).status, "queued");
    const start = Date.parse(bound) + 100;
    const at = offset => new Date(start + offset).toISOString();
    await store.observe(native("assistant.turn_start", { turnId: "0", content: "PRIVATE_REASONING" }, { agentId: "task-one", at: at(0) }));
    assert.equal(store.state.dispatches.at(-1).status, "running");
    await store.observe(native("assistant.turn_end", { turnId: "0" }, { agentId: "task-one", at: at(10) }));
    assert.equal(store.state.dispatches.at(-1).status, "running", "model-turn completion is not task completion");
    await store.reconcile([{
        type: "agent", id: "task-one", toolCallId: first.call, status: "idle",
        activeStartedAt: first.dispatch.registered_at ?? "2000-01-01T00:00:00.000Z", idleSince: at(20),
    }], at(30));
    assert.equal(store.state.dispatches.at(-1).status, "idle", "idleSince, not an older activeStartedAt, identifies this follow-up");
    await store.observe(native("subagent.completed", { toolCallId: first.call }, { agentId: "task-one", at: at(25) }));
    const current = store.state.dispatches.find(item => item.id === next.id);
    assert.equal(current.status, "completed");
    assert.equal(current.native_agent_id, "task-one");
    assert.equal(current.observed_at, at(30));
    assert.equal(store.state.dispatches[0].status, "completed");
    assert.ok(!(await fs.readFile(path.join(store.directory, "events.jsonl"), "utf8")).includes("PRIVATE_REASONING"));
});

test("idle follow-ups do not mean user input or a stopped coordinator", async t => {
    const f = await fixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    const { call } = await spawned(store);
    await store.reconcile([{ type: "agent", id: "task-one", toolCallId: call, status: "idle" }]);
    await store.observe(native("assistant.turn_start", { turnId: "1" }, { agentId: null }));
    const state = store.publicState;
    assert.equal(agentStatus(state, f.evidence.agents[0], 1, true), "running");
    assert.equal(agentStatus(state, f.evidence.agents[1], 1, true), "idle");
    assert.equal(AGENT_STATUS.idle, "Disponível");
    assert.equal(sessionLabel(state, 1, true), "Sessão processando");
    assert.equal(sessionLabel(state, 0, true), "Rodada histórica");
    assert.equal(agentStatus(state, f.evidence.agents[0], 0, true), "declared");
    assert.equal(dispatchStatus(state, state.dispatches[0], 1, false), "unknown");
    const old = { ...state, status: "completed" };
    assert.equal(archived(old, 1), true);
    assert.equal(sessionLabel(old, 1, true), "Registro encerrado");
    assert.equal(agentStatus(old, f.evidence.agents[0], 1, true), "declared");
    assert.equal(dispatchStatus(old, old.dispatches[0], 1, true), "idle");
});

test("coordinator finish keeps observation alive until session.idle, not task_complete", async t => {
    const f = await fixture(t);
    const session = { rpc: { tasks: { list: async () => ({ tasks: [] }) } } };
    const manager = new MonitorManager({ getSession: () => session, reader: async () => f.evidence, log: () => {}, browser: async () => {} });
    f.resources.push({ close: () => manager.shutdown() });
    const info = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    await manager.action({ operation: "phase", execution_id: info.execution_id, phase: "delivery", cycle: 1 }, "session");
    const entry = manager.entry(info.execution_id);
    manager.native(native("assistant.turn_start", { turnId: "10" }, { agentId: null }));
    await manager.runtimeQueue;
    const result = await manager.action({ operation: "finish", execution_id: info.execution_id, status: "completed" }, "session");
    assert.equal(result.status, "closing");
    assert.equal(result.historical, false);
    assert.equal(sessionLabel(entry.store.state, 1, true), "Sessão finalizando");
    const after = Date.parse(entry.store.state.closure.requested_at) + 100;
    const at = offset => new Date(after + offset).toISOString();
    manager.native(native("assistant.turn_end", { turnId: "10" }, { agentId: null, at: at(0) }));
    manager.native(native("assistant.idle", {}, { agentId: null, at: at(10) }));
    await manager.runtimeQueue;
    assert.equal(entry.store.state.status, "closing");
    assert.equal(entry.store.state.session_activity.status, "waiting");
    manager.native(native("session.task_complete", { summary: "PRIVATE_FINAL_TEXT" }, { agentId: null, at: at(20) }));
    await manager.runtimeQueue;
    assert.equal(entry.store.state.status, "closing");
    assert.equal(entry.store.state.session_activity.status, "completion_declared");
    manager.native(native("session.idle", {}, { agentId: null, at: at(30) }));
    await manager.runtimeQueue;
    assert.equal(entry.store.state.status, "completed");
    assert.equal(entry.store.state.phase, "done");
    assert.equal(entry.store.state.closure.confirmation, "session.idle");
    assert.equal(agentStatus(entry.store.state, f.evidence.agents[0], 1, true), "completed");
    const sequence = entry.store.state.sequence;
    manager.native(native("assistant.turn_start", { turnId: "another-request" }, { agentId: null, at: at(40) }));
    await manager.runtimeQueue;
    assert.equal(entry.store.state.sequence, sequence, "later unrelated requests cannot rewrite a closed execution");
    assert.ok(!(await fs.readFile(path.join(entry.store.directory, "events.jsonl"), "utf8")).includes("PRIVATE_FINAL_TEXT"));
});

test("legacy finish records stay historical and stale idle events cannot settle a new close", async t => {
    const f = await fixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    await store.phase("delivery", 1);
    await store.record("finish", { status: "completed" });
    assert.equal(store.state.status, "completed");
    assert.equal(store.state.closure.confirmation, "not_recorded");
    await store.phase("delivery", 1);
    await store.record("finish", { status: "completed", await_runtime_idle: true });
    const before = new Date(Date.parse(store.state.closure.requested_at) - 1).toISOString();
    await store.observe(native("session.idle", {}, { agentId: null, at: before }));
    assert.equal(store.state.status, "closing");
});


test("a session frozen on processing is measured as stalled, not as activity", async t => {
    const f = await fixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    await spawned(store);
    await store.observe(native("assistant.turn_start", {}, { agentId: null }));
    const at = Date.parse(store.state.updated_at);
    assert.equal(vitality(store.state, { threshold: 180, at: at + 60000 }).state, "active");
    const running = vitality(store.state, { threshold: 180, at: at + 400000 });
    assert.equal(running.state, "active", "a dispatch still running earns grace inside three thresholds");
    const wedged = vitality(store.state, { threshold: 180, at: at + 900000 });
    assert.equal(wedged.state, "stalled");
    assert.equal(wedged.session_activity.status, "processing");
    assert.ok(wedged.inactive_seconds > 540);
    assert.ok(wedged.reason.includes("triplo"));
});

test("a registered dispatch that was never called is stalled once the threshold passes", async t => {
    const f = await fixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    await store.phase("authors", 1);
    await store.prepareDispatch("author-01", 1);
    const at = Date.parse(store.state.updated_at);
    assert.equal(vitality(store.state, { threshold: 180, at: at + 60000 }).state, "waiting");
    const stalled = vitality(store.state, { threshold: 180, at: at + 300000 });
    assert.equal(stalled.state, "stalled");
    assert.equal(stalled.dispatches.queued, 1);
    assert.equal(stalled.dispatches.running, 0);
    assert.ok(stalled.last_progress_signal.includes("author-01"));
});

test("a lost connection is never published as activity and health reaches disk", async t => {
    const f = await fixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    await spawned(store);
    const published = await store.publishHealth({ threshold: 180 });
    assert.equal(published.schema_version, 1);
    assert.equal(published.state, "waiting");
    const file = JSON.parse(await fs.readFile(path.join(store.directory, "health.json"), "utf8"));
    assert.deepEqual(file, published);
    assert.equal(store.publicState.health.state, "waiting");
    await store.record("connection", { connection: "disconnected" }, "observer");
    const lost = vitality(store.state, { threshold: 180 });
    assert.equal(lost.state, "unobserved");
    assert.notEqual(lost.state, "active");
});

test("the heartbeat keeps publishing health outside the agent loop and stops at closure", async t => {
    const f = await fixture(t);
    const session = { getEvents: async () => [], rpc: { tasks: { list: async () => ({ tasks: [] }) } } };
    const manager = new MonitorManager({
        getSession: () => session, reader: async () => f.evidence, log: () => {},
        browser: async () => {}, heartbeat: 20, threshold: 180,
    });
    t.after(() => manager.shutdown());
    const info = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    assert.equal(info.health.state, "waiting", "a fresh execution with no session signal is waiting, not active");
    const entry = manager.entry(info.execution_id);
    const file = path.join(entry.store.directory, "health.json");
    const first = JSON.parse(await fs.readFile(file, "utf8"));
    await entry.store.observe(native("assistant.turn_start", {}, { agentId: null }));
    for (let attempt = 0; attempt < 100 && JSON.parse(await fs.readFile(file, "utf8")).state === first.state; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.equal(JSON.parse(await fs.readFile(file, "utf8")).state, "active", "the heartbeat republishes without any agent turn");
    await entry.store.record("finish", { status: "completed" }, "coordinator");
    for (let attempt = 0; attempt < 100 && entry.healthTimer; attempt++) await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(entry.healthTimer, null, "the heartbeat stops itself once the execution is terminal");
    assert.equal((await entry.store.publishHealth({ threshold: 180 })).state, "closed");
});


test("the panel shows a stall instead of a comfortable session label", async t => {
    const base = {
        status: "active", connection: "connected", cycle: 1, phase: "reviews",
        session_activity: { status: "processing", observed_at: new Date().toISOString(), stale: false },
        evidence: { cycles: [] }, dispatches: [], agents: [],
    };
    assert.equal(healthLabel(base, 1, true), null);
    assert.equal(sessionLabel(base, 1, true), "Sessão processando");
    const stalled = { ...base, health: { state: "stalled", reason: "nada observado há 900 s" } };
    assert.equal(healthLabel(stalled, 1, true).title, "Execução parada");
    assert.ok(healthLabel(stalled, 1, true).detail.includes("900 s"));
    assert.equal(sessionLabel(stalled, 1, true), "Sessão processando", "the raw label stays raw; the stall is reported beside it");
    assert.equal(healthLabel(stalled, 1, false), null, "a disconnected panel claims nothing about the session");
    assert.equal(healthLabel({ ...base, health: { state: "active", reason: "ok" } }, 1, true), null);
});

test("the panel judges grades against the bar its review was judged under, not against a hard-coded A", () => {
    const scale = ["B+", "A-", "A", "A+"];
    assert.deepEqual(APPROVAL_GRADES, ["A-", "A"]);
    // The grade of the cycle's own review wins, then the swarm's, then the original A.
    assert.equal(approvalGrade({ approval_grade: "A-" }, { approval_grade: "A" }), "A-");
    assert.equal(approvalGrade({ approval_grade: "A" }, { approval_grade: "A-" }), "A");
    assert.equal(approvalGrade({}, { approval_grade: "A-" }), "A-");
    assert.equal(approvalGrade(undefined, undefined), "A", "evidence recorded before the field existed");
    for (const odd of ["B+", "A+", "", null, 3, ["A-"], {}]) {
        assert.equal(approvalGrade({ approval_grade: odd }, { approval_grade: odd }), "A", `ignored: ${JSON.stringify(odd)}`);
    }
    // Under A- an A- passes, as the gate decided; under A it does not; nothing below the bar ever does.
    assert.equal(meetsApproval(scale, "A-", "A-"), true);
    assert.equal(meetsApproval(scale, "A-", "A"), false);
    assert.equal(meetsApproval(scale, "A", "A-"), true);
    assert.equal(meetsApproval(scale, "A+", "A"), true);
    assert.equal(meetsApproval(scale, "B+", "A-"), false);
    assert.equal(meetsApproval(scale, "Z", "A-"), false, "a grade that is not on the scale never passes");
    assert.equal(meetsApproval(scale, undefined, "A-"), false);
});

test("the count, the cell colour and the legend of a cycle all follow its bar", () => {
    const evidence = { grade_scale: ["B+", "A-", "A", "A+"], approval_grade: "A" };
    const topics = ["B+", "A-", "A", "A+"].map((grade, index) => ({ id: `T0${index + 1}`, grade }));
    const under = bar => ({ approval_grade: bar, topics, consistent: true });
    assert.equal(gradeCount(under("A-"), evidence), "3 / 4");
    assert.equal(gradeCount(under("A"), evidence), "2 / 4");
    assert.equal(gradeCount({ topics, consistent: true }, evidence), "2 / 4", "no declaration on the cycle: the swarm's bar");
    assert.equal(gradeCount({ topics, consistent: true }, { ...evidence, approval_grade: "A-" }), "3 / 4");
    assert.equal(gradeCount({ ...under("A-"), consistent: false }, evidence), "Divergente");
    assert.equal(gradeCount({ approval_grade: "A-", topics: [], consistent: true }, evidence), "Pendente");
    assert.equal(gradeCount(undefined, evidence), "Pendente");
    assert.deepEqual(["B+", "A-", "A", "A+"].map(grade => gradeKind(grade, evidence.grade_scale, "A-")), ["bad", "good", "good", "good"]);
    assert.deepEqual(["B+", "A-", "A", "A+"].map(grade => gradeKind(grade, evidence.grade_scale, "A")), ["bad", "bad", "good", "good"]);
    for (const absent of [undefined, null, "", "Z"]) assert.equal(gradeKind(absent, evidence.grade_scale, "A-"), "missing");
    // The cell a person reads is built from the cycle, not from a bar the caller has to remember to pass.
    assert.deepEqual(gradeView("A-", under("A-"), evidence), { text: "A-", kind: "good" });
    assert.deepEqual(gradeView("A-", under("A"), evidence), { text: "A-", kind: "bad" });
    assert.deepEqual(gradeView("A-", { topics }, { ...evidence, approval_grade: "A-" }), { text: "A-", kind: "good" });
    assert.deepEqual(gradeView("A-", undefined, evidence), { text: "A-", kind: "bad" });
    assert.deepEqual(gradeView(undefined, under("A-"), evidence), { text: "N/D", kind: "missing" });
    assert.deepEqual(gradeView("Z", under("A-"), evidence), { text: "N/D", kind: "missing" });
    assert.equal(approvalLegend(under("A-"), evidence), "Nota de aprovação: A- · abaixo bloqueia · achado crítico veta");
    assert.equal(approvalLegend(under("A"), evidence), "Nota de aprovação: A · abaixo bloqueia · achado crítico veta");
    assert.equal(approvalLegend(undefined, { ...evidence, approval_grade: "A-" }), "Nota de aprovação: A- · abaixo bloqueia · achado crítico veta");
    assert.deepEqual(gradeCaption(under("A-"), evidence), { label: "≥ A-", title: "Tópicos com nota mínima A- ou acima; isso não substitui o gate" });
    assert.deepEqual(gradeCaption(under("A"), evidence), { label: "≥ A", title: "Tópicos com nota mínima A ou acima; isso não substitui o gate" });
    assert.equal(gradeCaption(undefined, { ...evidence, approval_grade: "A-" }).label, "≥ A-");
});

test("the panel source does not hard-code a pass mark again", async () => {
    // The helpers above are only worth anything while the panel uses them; these are the forms it used to use.
    const source = await fs.readFile(new URL("../ui/app.mjs", import.meta.url), "utf8");
    assert.doesNotMatch(source, /indexOf\(\s*"A"\s*\)/, "the index of a literal A is a pass mark");
    assert.doesNotMatch(source, /"A- bloqueia/, "the legend must say what the cycle's bar is");
    assert.match(source, /gradeCount\(cycle, data\.evidence\)/);
    assert.match(source, /approvalLegend\(cycle, shownState\.evidence\)/);
    assert.match(source, /gradeView\(value, cycle, shownState\.evidence\)/);
    assert.match(source, /gradeCaption\(cycle, data\.evidence\)/);
    assert.match(source, /\$\("grade-label"\)\.textContent = caption\.label;/);
    assert.match(source, /\$\("grade-card"\)\.title = caption\.title;/);
    assert.equal(source.match(/\.append\(grade\([^)]*, cycle\)\)/g)?.length, 2, "both cells of the table are built from the cycle");
    const html = await fs.readFile(new URL("../ui/index.html", import.meta.url), "utf8");
    assert.doesNotMatch(html, /≥ A|A ou A\+/, "the page does not say which grade passes before the cycle says it");
    assert.match(html, /id="grade-label"/);
    assert.match(html, /id="grade-card"/);
});

test("a grade that is not on the scale never passes, even against a bar that is not on it either", () => {
    assert.equal(meetsApproval(["A-", "A"], "Z", "Q"), false, "-1 >= -1 would pass it");
});

test("the Python bridge carries the approval grade and the executor's ceiling to the panel", async t => {
    const f = await fixture(t);
    await fs.writeFile(path.join(f.root, "brief.md"), `---\nswarm_id: fixture\nskill_version: "3.1.0"\nmax_cycles: 3\n---\n# Fixture\n`, "utf8");
    const execution = path.join(f.root, "reports", "execution");
    await fs.mkdir(execution, { recursive: true });
    await fs.writeFile(path.join(execution, "plan.json"), JSON.stringify({
        max_cycles: 3, approval_grade: "A-", options: { max_cycles: 6, approval_grade: "A-" },
    }));
    const evidence = await readEvidence(f.root);
    assert.equal(evidence.approval_grade, "A-");
    assert.equal(evidence.max_cycles, 6, "what a person set with --max-cycles, not the brief's");
});

test("a recovery is journaled, capped per agent and capped per execution", async t => {
    const f = await fixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    await spawned(store);
    await store.record("recovery", { rule: "R1", agent_id: "author-01", cycle: 1, detail: "despacho sem chamada real" });
    await store.record("recovery", { rule: "R1", agent_id: "author-01", cycle: 1, detail: "segunda tentativa" });
    assert.deepEqual(store.state.recoveries.map(item => item.attempt), [1, 2]);
    await assert.rejects(store.record("recovery", { rule: "R1", agent_id: "author-01", cycle: 1, detail: "terceira" }),
        /ceiling reached for author-01/);
    await assert.rejects(store.record("recovery", { rule: "R9", agent_id: "author-01", cycle: 1, detail: "regra inventada" }),
        /Unknown recovery rule/);
    await assert.rejects(store.record("recovery", { rule: "R1", agent_id: "ghost", cycle: 1, detail: "agente inexistente" }),
        /not a declared agent/);
    for (let extra = 0; extra < 4; extra++) {
        await store.record("recovery", { rule: "R4", agent_id: null, cycle: 1, detail: `check ${extra}` });
    }
    assert.equal(store.state.recoveries.length, 6);
    await assert.rejects(store.record("recovery", { rule: "R4", agent_id: null, cycle: 1, detail: "sétima" }),
        /ceiling reached for this execution/);
    const journal = await fs.readFile(path.join(store.directory, "events.jsonl"), "utf8");
    assert.equal(journal.split("\n").filter(line => line.includes('"type":"recovery"')).length, 6);
    assert.ok(!journal.includes("sétima"));
});


test("status measures health at the moment it is asked, not at the last heartbeat", async t => {
    const f = await fixture(t);
    const session = { getEvents: async () => [], rpc: { tasks: { list: async () => ({ tasks: [] }) } } };
    const manager = new MonitorManager({
        getSession: () => session, reader: async () => f.evidence, log: () => {},
        browser: async () => {}, heartbeat: 3600000, threshold: 180,
    });
    t.after(() => manager.shutdown());
    const started = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    const entry = manager.entry(started.execution_id);
    await entry.store.record("connection", { connection: "disconnected" }, "observer");
    assert.equal(entry.store.health.state, "waiting", "the stored value is still the one from the single heartbeat");
    const asked = await manager.action({ operation: "status", execution_id: started.execution_id }, "session");
    assert.equal(asked.health.state, "unobserved");
    assert.equal(JSON.parse(await fs.readFile(path.join(entry.store.directory, "health.json"), "utf8")).state, "unobserved");
});

// -- the live feed from the deterministic executor ---------------------------------------------------------------------
const EPOCH = "2026-10-07T03:00:00.000Z";
const moment = seconds => new Date(Date.parse(EPOCH) + seconds * 1000).toISOString();
const inner = (seq, type, data, { n = 0, seconds = seq, cycle = 1 } = {}) => ({ seq, n, at: moment(seconds), type, cycle, data });
const asked = (seq, agent, extra = {}, options = {}) => inner(seq, "dispatch", {
    id: `x${seq}`, agent_id: agent, cycle: 1, round: 0, stage: "authors", attempt: 1,
    label: `c01.r0.authors.${agent}.a1`, executor_task: `c01.r0.authors.${agent}`, identity: "abcdef012345", ...extra,
}, { n: 1, ...options });
const pick = (value, keys) => Object.fromEntries(keys.map(key => [key, value[key]]));
async function until(check, { timeout = 5000 } = {}) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
        if (await check()) return;
        await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.fail("the condition was not reached in time");
}
async function executorFixture(t) {
    const f = await fixture(t);
    f.evidence.agents.push(
        { id: "author-02", kind: "author", role: "Adoption", declared_model: "auto", path: "agents/author-02.md" },
        { id: "reviewer-01", kind: "reviewer", role: "Facts", declared_model: "auto", path: "agents/reviewer-01.md" },
    );
    return f;
}
async function writeExecutorJournal(root, entries = [{ seq: 1, event: "run_started" }]) {
    const directory = path.join(root, "reports", "execution");
    await fs.mkdir(directory, { recursive: true });
    await fs.writeFile(path.join(directory, "journal.jsonl"), entries.map(entry => JSON.stringify(entry)).join("\n") + "\n");
}
// A reader that answers like the Python projection does, from a list the test grows while the manager is looking.
function executorFeed() {
    const feed = { events: [], summary: null, health: { state: "active", reason: "o executor está ativo", threshold_seconds: 180 }, calls: [], reset: false, failure: null, per: 0 };
    feed.reader = async (_root, args) => {
        feed.calls.push({ ...args });
        if (feed.failure) throw new Error(feed.failure);
        const newer = feed.events.filter(item => item.seq > args.after);
        const piece = feed.per ? newer.slice(0, feed.per) : newer;
        const more = piece.length < newer.length;
        const newest = Math.max(args.after, ...feed.events.map(item => item.seq));
        return {
            schema_version: 1, present: true, epoch: EPOCH, reset: feed.reset, more, skipped_lines: 0,
            cursor: feed.reset ? args.after : more ? piece.at(-1).seq : newest, events: feed.reset ? [] : piece,
            summary: feed.summary ?? { finished: false, outcome: null, cycle: 1, counts: {}, running: [], driver: null }, health: feed.health,
        };
    };
    return feed;
}
const quietSession = { getEvents: async () => [], rpc: { tasks: { list: async () => ({ tasks: [] }) } } };

test("a batch from the executor's journal becomes dispatches, handoffs, phases and the end of the run, in one journal line", async t => {
    const f = await executorFixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    const journal = path.join(store.directory, "events.jsonl");
    const lines = async () => (await fs.readFile(journal, "utf8")).split("\n").filter(Boolean).length;
    const before = await lines();
    await store.observeExecutor({ epoch: EPOCH, cursor: 9, events: [
        inner(1, "phase", { phase: "setup", cycle: 0 }, { cycle: 0 }),
        inner(1, "executor", { kind: "run", label: "Executor iniciado" }, { n: 1, cycle: 0 }),
        inner(2, "phase", { phase: "authors", cycle: 1 }),
        asked(2, "author-01"),
        inner(3, "runtime", { dispatch_id: "x2", status: "running", started_at: moment(3) }),
        inner(4, "runtime", { dispatch_id: "x2", status: "completed", outcome: "accepted", ended_at: moment(70), seconds: 66.4, observed_model: "gpt-5.5" }, { seconds: 70 }),
        inner(5, "phase", { phase: "consolidation", cycle: 1 }, { seconds: 71 }),
        asked(5, "coordinator", { stage: "consolidation", label: "c01.r0.consolidation.coordinator.a1", executor_task: "c01.r0.consolidation.coordinator" }, { seconds: 71 }),
        inner(5, "handoff", { from: "author-01", to: "coordinator", label: "seções", cycle: 1 }, { n: 2, seconds: 71 }),
    ] });
    assert.equal((await lines()) - before, 1, "one journal line and one snapshot for the whole batch");
    const [author, coordinator] = store.state.dispatches;
    assert.deepEqual([author.id, author.agent_id, author.status, author.source, author.task_id, author.tool_call_id], ["x2", "author-01", "completed", "executor", null, null]);
    assert.deepEqual([author.started_at, author.ended_at, author.seconds, author.outcome], [moment(3), moment(70), 66.4, "accepted"]);
    assert.deepEqual([author.observed_model, author.model_source, author.declared_model, author.role, author.agent_kind], ["gpt-5.5", "executor_journal", "auto", "Evidence", "author"]);
    assert.deepEqual([author.registered_at, coordinator.status, coordinator.stage], [moment(2), "queued", "consolidation"]);
    assert.deepEqual([author.observed_at, coordinator.registered_at, coordinator.observed_at], [moment(70), moment(71), moment(71)], "observed at the last thing the journal said of it");
    assert.deepEqual(store.state.edges.map(({ from, to, label, cycle, source }) => [from, to, label, cycle, source]), [["author-01", "coordinator", "seções", 1, "executor"]]);
    assert.deepEqual([store.state.phase, store.state.cycle, store.state.status], ["consolidation", 1, "active"]);
    assert.deepEqual(store.state.executor, { epoch: EPOCH, cursor: 9, skipped: 0 });
    assert.ok(store.state.events.every(event => event.type !== "executor_batch"), "the batch is recorded as the events it holds, not once more as itself");
    assert.deepEqual(store.state.events.filter(event => event.source === "executor").map(event => event.id),
        ["x1.0", "x1.1", "x2.0", "x2.1", "x3.0", "x4.0", "x5.0", "x5.1", "x5.2"]);
    assert.deepEqual(store.state.events.find(event => event.id === "x1.1"), {
        id: "x1.1", sequence: store.state.sequence, at: moment(1), source: "executor", type: "executor", cycle: 0, data: { kind: "run", label: "Executor iniciado" },
    });
    await store.observeExecutor({ epoch: EPOCH, cursor: 12, events: [inner(12, "finish", { status: "completed" }, { seconds: 120 })] });
    assert.deepEqual([store.state.status, store.state.phase], ["completed", "done"]);
    assert.deepEqual(store.state.closure, { status: "completed", requested_at: moment(120), settled_at: moment(120), confirmation: "executor_journal" });
    await store.observeExecutor({ epoch: EPOCH, cursor: 14, events: [inner(14, "finish", { status: "escalated" }, { seconds: 130 })] });
    assert.equal(store.state.status, "escalated");
});

test("only the fields the executor feed is expected to carry reach the state and the journal of the execution", async t => {
    const f = await executorFixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    // Parsed from text, as a reading is: JSON.parse makes "__proto__" an own property, which a literal would not.
    const hostile = JSON.parse(`{"seq":2,"n":0,"at":"${moment(2)}","type":"dispatch","cycle":1,"prompt":"PRIVATE_PROMPT","data":{"id":"x2","agent_id":"author-01","cycle":1,"round":0,"stage":"authors","attempt":1,"label":"l","executor_task":"t","identity":null,"prompt":"PRIVATE_PROMPT","task_id":"native-1","tool_call_id":"call-x","status":"completed","source":"runtime","declared_model":"fake","__proto__":{"polluted":true}}}`);
    const running = JSON.parse(`{"seq":3,"n":0,"at":"${moment(3)}","type":"runtime","cycle":1,"data":{"dispatch_id":"x2","status":"running","started_at":"${moment(3)}","task_id":"native-2","prompt":"PRIVATE_PROMPT","model_source":"first_dispatched","observation_stale":true,"declared_model":"fake","__proto__":{"polluted":true}}}`);
    await store.observeExecutor({ epoch: EPOCH, cursor: 3, events: [hostile, running] });
    const [dispatch] = store.state.dispatches;
    assert.deepEqual([dispatch.task_id, dispatch.tool_call_id, dispatch.status, dispatch.source, dispatch.declared_model, dispatch.model_source],
        [null, null, "running", "executor", "auto", null], "the fields a native dispatch correlates by, and the declared model, are not the feed's to set");
    assert.equal(dispatch.observation_stale, false);
    assert.equal(Object.hasOwn(dispatch, "prompt"), false);
    assert.equal(Object.hasOwn(dispatch, "polluted"), false);
    assert.equal({}.polluted, undefined);
    const written = await fs.readFile(path.join(store.directory, "events.jsonl"), "utf8");
    for (const secret of ["PRIVATE_PROMPT", "native-1", "native-2", "call-x", "polluted"]) {
        assert.ok(!written.includes(secret), `${secret} must not reach the disk`);
        assert.ok(!JSON.stringify(store.publicState).includes(secret), `${secret} must not reach the panel`);
    }
});

test("an event the state cannot hold is skipped and counted, never half applied, and the cursor still moves", async t => {
    const f = await executorFixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    await store.observeExecutor({ epoch: EPOCH, cursor: 8, events: [
        inner(1, "phase", { phase: "authors", cycle: 2 }, { cycle: 2 }),
        inner(2, "phase", { phase: "authors", cycle: 1 }),
        asked(3, "ghost", { cycle: 2 }, { cycle: 2 }),
        inner(4, "runtime", { dispatch_id: "x99", status: "running" }),
        inner(5, "handoff", { from: "author-01", to: "ghost", label: "x", cycle: 2 }, { cycle: 2 }),
        inner(5, "handoff", { from: "ghost", to: "author-01", label: "x", cycle: 2 }, { n: 1, cycle: 2 }),
        { seq: 6, n: 0, at: moment(6), type: "constructor", cycle: 1, data: {} },
        { seq: 7, n: 0, at: "ontem", type: "phase", cycle: 1, data: { phase: "authors", cycle: 1 } },
        asked(8, "author-01", { cycle: 2 }, { cycle: 2 }),
        asked(9, "author-02", { cycle: 1 }),
    ] });
    assert.deepEqual(store.state.dispatches.map(item => item.agent_id), ["author-01"], "the one that could be held is held, and a dispatch for a cycle that is over is not");
    assert.deepEqual(store.state.edges, [], "a handoff with an endpoint nobody declared leaves no edge behind");
    assert.deepEqual([store.state.phase, store.state.cycle], ["authors", 2], "and a phase that goes back leaves the cycle where it was");
    assert.deepEqual(store.state.executor, { epoch: EPOCH, cursor: 8, skipped: 8 }, "six the state refused and two that were not events at all");
    assert.ok(store.state.events.every(event => ["x1.0", "x8.1"].includes(event.id) || event.source !== "executor"), "only what was applied is in the history");
    await store.observeExecutor({ epoch: EPOCH, cursor: 12, events: [asked(11, "author-02", { id: "x8", cycle: 2 }, { cycle: 2 })] });
    assert.equal(store.state.executor.skipped, 9, "what was skipped is counted over every reading, not only the last");
});

test("the same reading twice changes nothing, an older one is ignored and a journal of another run is refused", async t => {
    const f = await executorFixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    const journal = path.join(store.directory, "events.jsonl");
    const batch = { epoch: EPOCH, cursor: 3, events: [inner(1, "phase", { phase: "setup", cycle: 0 }, { cycle: 0 }), inner(2, "phase", { phase: "authors", cycle: 1 }), asked(3, "author-01")] };
    await store.observeExecutor(batch);
    const [sequence, written] = [store.state.sequence, await fs.readFile(journal, "utf8")];
    assert.equal(await store.observeExecutor(batch), null);
    assert.equal(await store.observeExecutor({ ...batch, cursor: 2, events: [] }), null);
    assert.equal(await store.observeExecutor({ ...batch, cursor: 3, events: [asked(3, "author-02", { id: "x7" })] }), null, "nothing new past the cursor");
    assert.deepEqual([store.state.sequence, await fs.readFile(journal, "utf8")], [sequence, written]);
    await assert.rejects(store.observeExecutor({ epoch: "2026-10-08T00:00:00.000Z", cursor: 40, events: [] }), /not the one this execution follows/);
    await assert.rejects(store.observeExecutor({ epoch: "2026-10-08T00:00:00.000Z", cursor: 1, events: [] }), /not the one this execution follows/,
        "a journal of another run is never taken for an older reading of this one");
    for (const bad of [{ epoch: "ontem", cursor: 4, events: [] }, { epoch: EPOCH, cursor: 4.5, events: [] },
        { epoch: EPOCH, cursor: 4, events: "none" }, { epoch: EPOCH, cursor: 4, events: new Array(20001).fill(null) }]) {
        await assert.rejects(store.observeExecutor(bad), /Invalid executor batch/, JSON.stringify(bad).slice(0, 60));
    }
    assert.equal(await store.observeExecutor({ epoch: EPOCH, cursor: -1, events: [] }), null, "a cursor behind the one applied is the same as an older reading");
    assert.deepEqual(store.state.executor, { epoch: EPOCH, cursor: 3, skipped: 0 });
    await store.observeExecutor({ epoch: EPOCH, cursor: 6, events: [inner(6, "runtime", { dispatch_id: "x3", status: "running", started_at: moment(6) })] });
    assert.equal(store.state.dispatches[0].status, "running");
    assert.equal(store.state.executor.cursor, 6);
});

test("an ended dispatch stays ended and a repeated dispatch is refused", async t => {
    const f = await executorFixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    await store.observeExecutor({ epoch: EPOCH, cursor: 7, events: [
        inner(1, "phase", { phase: "authors", cycle: 1 }), asked(2, "author-01"), asked(3, "author-02"),
        asked(4, "coordinator", { stage: "consolidation", label: "c01.r0.consolidation.coordinator.a1", executor_task: "c01.r0.consolidation.coordinator" }),
        inner(5, "runtime", { dispatch_id: "x2", status: "failed", outcome: "rejected", error: "a lista não fecha", ended_at: moment(9) }),
        inner(6, "runtime", { dispatch_id: "x3", status: "cancelled", outcome: "superseded", ended_at: moment(9) }),
        inner(7, "runtime", { dispatch_id: "x4", status: "completed", outcome: "accepted", ended_at: moment(9) }),
    ] });
    await store.observeExecutor({ epoch: EPOCH, cursor: 13, events: [
        inner(8, "runtime", { dispatch_id: "x2", status: "running", started_at: moment(10) }),
        inner(9, "runtime", { dispatch_id: "x2", status: "completed" }),
        inner(10, "runtime", { dispatch_id: "x3", status: "completed" }),
        inner(11, "runtime", { dispatch_id: "x4", status: "running" }),
        inner(12, "runtime", { dispatch_id: "x4", status: "queued" }),
        asked(13, "author-01", { id: "x2" }),
    ] });
    const [first, second, third] = store.state.dispatches;
    assert.deepEqual([first.status, first.outcome, first.error], ["failed", "rejected", "a lista não fecha"]);
    assert.equal(second.status, "cancelled");
    assert.equal(third.status, "completed", "a result that was accepted is not undone by a later start");
    assert.equal(store.state.dispatches.length, 3);
    assert.equal(store.state.executor.skipped, 1, "the repeated dispatch");
});

test("what the executor feed recorded survives a restart of the monitor, with or without the snapshot", async t => {
    const f = await executorFixture(t);
    const executionId = "exec-restart";
    const first = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence, executionId });
    await first.observeExecutor({ epoch: EPOCH, cursor: 4, events: [
        inner(1, "phase", { phase: "authors", cycle: 1 }), asked(2, "author-01"),
        inner(3, "runtime", { dispatch_id: "x2", status: "running", started_at: moment(3) }, { seconds: 3 }),
        inner(4, "executor", { kind: "run", label: "Executor iniciado" }),
    ] });
    const expected = { dispatches: structuredClone(first.state.dispatches), executor: { ...first.state.executor }, edges: structuredClone(first.state.edges) };
    await first.close();
    const second = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence, executionId });
    f.resources.push(second);
    assert.deepEqual([second.state.dispatches, second.state.executor, second.state.edges], [expected.dispatches, expected.executor, expected.edges]);
    assert.notEqual(second.state.dispatches[0].observation_stale, true, "reconnecting does not make a journal's dispatch unobserved");
    assert.equal((await second.publishHealth({ threshold: 180 })).state, "unobserved", "but nothing has been read yet, and the health says so");
    await second.close();
    await fs.unlink(path.join(second.directory, "snapshot.json"));
    const third = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence, executionId });
    f.resources.push(third);
    assert.deepEqual([third.state.dispatches, third.state.executor], [expected.dispatches, expected.executor], "the journal alone rebuilds it");
    await third.observeExecutor({ epoch: EPOCH, cursor: 4, events: [inner(4, "executor", { kind: "run", label: "de novo" })] });
    assert.equal(third.state.events.filter(event => event.source === "executor" && event.id === "x4.0").length, 1, "a reading already applied is not applied again");
});

test("dispatches the executor's journal reports are as observed as the last reading of it", async t => {
    const f = await executorFixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    await store.observeExecutor({ epoch: EPOCH, cursor: 3, events: [inner(1, "phase", { phase: "authors", cycle: 1 }), asked(2, "author-01"),
        inner(3, "runtime", { dispatch_id: "x2", status: "running", started_at: moment(3) })] });
    const reading = { summary: { finished: false, cycle: 1, running: [], counts: {} }, health: { state: "active", reason: "ok", threshold_seconds: 180 } };
    assert.equal(store.publicState.dispatches[0].observation_stale, true, "nothing has been read since the monitor started");
    store.setExecutorLive(reading);
    assert.equal(store.publicState.dispatches[0].observation_stale, false);
    store.setExecutorLive(reading, { at: new Date(Date.now() - 61000).toISOString() });
    assert.equal(store.publicState.dispatches[0].observation_stale, true, "a reading older than a minute is not current");
    assert.equal(executorFeedFresh(undefined), false);
    assert.equal(executorFeedFresh({ observed_at: "nonsense" }), false);
    assert.equal(executorFeedFresh({ observed_at: new Date().toISOString() }), true);
    for (const [age, fresh] of [[59000, true], [60000, true], [61000, false]]) {
        assert.equal(executorFeedFresh({ observed_at: new Date(1000000 - age).toISOString() }, 1000000), fresh, `${age} ms old`);
    }
    store.setExecutorLive(reading);
    await store.record("connection", { connection: "disconnected" }, "observer");
    assert.notEqual(store.state.dispatches[0].observation_stale, true, "the persisted dispatch is not marked by a connection event");
    assert.equal(store.state.dispatches[0].status, "running", "and the state still says what the journal said");
    const legacy = { dispatches: [{ id: "n1", status: "running" }] };
    assert.equal(viewState(legacy, null), legacy, "a swarm the coordinator drives is published as it is");
    const native = { executor: { cursor: 1 }, dispatches: [{ id: "n1", status: "running", observation_stale: false }, { id: "x1", source: "executor", status: "running" }] };
    assert.deepEqual(viewState(native, null).dispatches.map(item => item.observation_stale), [false, true], "only the executor's own dispatches follow the reading");
});

test("only the fields the panel shows are kept from a reading, and the health it reports is the executor's own", async t => {
    const f = await executorFixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    await store.observeExecutor({ epoch: EPOCH, cursor: 3, events: [inner(1, "phase", { phase: "authors", cycle: 1 }), asked(2, "author-01"),
        inner(3, "runtime", { dispatch_id: "x2", status: "running", started_at: moment(3) })] });
    store.setExecutorLive({
        summary: {
            finished: "yes", outcome: ["approved"], cycle: 1, counts: { issued: 3, accepted: "many", prompt: "PRIVATE" },
            running: [{ agent: "author-01", label: "x".repeat(1000), stage: "authors", cycle: 1, state: "running", since: moment(3), secret: "PRIVATE" },
                { agent: "author-02", state: "levitating" }, null, 7, ...Array.from({ length: 150 }, (_, index) => ({ agent: `a${index}`, state: "queued" }))],
            driver: { state: "running", stage: "authors", cycle: 1, detail: "d", backend: "copilot-cli", age_seconds: 4.4, pid: 77, running: [{ prompt: "PRIVATE" }] },
        },
        health: { state: "active", reason: "o executor está ativo", threshold_seconds: 180, secret: "PRIVATE" }, skipped_lines: 2, extra: "PRIVATE",
    });
    const live = store.publicState.executor_live;
    assert.ok(!JSON.stringify(live).includes("PRIVATE"));
    assert.deepEqual(live.counts, { issued: 3, accepted: 0, rejected: 0, null: 0, repairs: 0 });
    assert.equal(live.outcome, null);
    assert.equal(live.finished, false, "only the boolean true is a run that finished");
    assert.deepEqual([live.running.length, live.running[0].label.length, live.running[0].state, live.running[1].state], [100, 300, "running", "queued"],
        "a hundred rows at most, and a state that is not one of the two is waiting");
    assert.deepEqual(live.driver, { state: "running", stage: "authors", cycle: 1, detail: "d", backend: "copilot-cli", age_seconds: 4 });
    assert.equal(live.skipped_lines, 2);
    const now = Date.now();
    assert.equal(executorVitality(store.state, live, { at: now }).state, "active");
    const stalled = { ...live, health: { state: "stalled", reason: "o executor foi interrompido; o mesmo comando retoma de onde parou" } };
    assert.deepEqual(pick(executorVitality(store.state, stalled, { at: now }), ["state", "reason"]), { state: "stalled", reason: stalled.health.reason });
    assert.equal(executorVitality(store.state, live, { at: now + 120000 }).state, "unobserved", "a reading two minutes old");
    assert.match(executorVitality(store.state, live, { at: now + 120000 }).reason, /leitura recente do journal/);
    assert.equal(executorVitality(store.state, { ...live, health: { state: "bogus", reason: "x" } }, { at: now }).state, "unobserved");
    assert.equal(executorVitality(store.state, { ...live, health: { state: "active", reason: null } }, { at: now }).state, "unobserved");
    assert.equal(executorVitality(store.state, null, { at: now }).state, "unobserved");
    assert.equal(executorVitality(store.state, { observed_at: new Date(now).toISOString() }, { at: now }).state, "unobserved", "a reading with no health in it");
    assert.equal(executorVitality({ ...store.state, status: "completed" }, live, { at: now }).state, "closed");
    assert.equal((await store.publishHealth({ threshold: 180 })).state, "active", "the health that is published is the executor's");
    store.setExecutorLive({ summary: live, health: stalled.health });
    assert.equal((await store.publishHealth({ threshold: 180 })).state, "stalled");
    const written = JSON.parse(await fs.readFile(path.join(store.directory, "health.json"), "utf8"));
    assert.deepEqual([written.state, written.reason], ["stalled", stalled.health.reason], "and it reaches the file the watchdog reads");
});

test("an event is read the same whether it arrives from the reader or from the journal of the execution", () => {
    const wanted = inner(2, "dispatch", asked(2, "author-01").data, { n: 1 });
    const once = cleanExecutorEvent({ ...wanted, extra: "x", data: { ...wanted.data, extra: "x" } });
    assert.deepEqual(once, wanted);
    assert.deepEqual(cleanExecutorEvent(once), once, "cleaning twice is cleaning once");
    for (const bad of [null, [], "x", { ...wanted, type: "binding" }, { ...wanted, type: "__proto__" }, { ...wanted, data: null }, { ...wanted, data: [] },
        { ...wanted, seq: 0 }, { ...wanted, seq: 1.5 }, { ...wanted, n: -1 }, { ...wanted, cycle: "1" }, { ...wanted, at: "2026-10-07" },
        { ...wanted, data: { ...wanted.data, id: "y2" } }, { ...wanted, data: { ...wanted.data, identity: "XYZ" } },
        { ...wanted, data: { ...wanted.data, agent_id: "../x" } }, { ...wanted, data: { ...wanted.data, label: "" } },
        { ...wanted, data: { ...wanted.data, label: "x".repeat(301) } }, { ...wanted, data: { ...wanted.data, attempt: 0 } }]) {
        assert.throws(() => cleanExecutorEvent(bad), undefined, JSON.stringify(bad)?.slice(0, 80));
    }
    assert.throws(() => cleanExecutorEvent(inner(3, "runtime", { dispatch_id: "x2", status: "levitating" })), /Unknown runtime status/);
    assert.throws(() => cleanExecutorEvent(inner(3, "runtime", { dispatch_id: "x2", outcome: "great" })), /Unknown executor outcome/);
    assert.throws(() => cleanExecutorEvent(inner(3, "runtime", { dispatch_id: "x2", seconds: -1 })), /duration/);
    assert.throws(() => cleanExecutorEvent(inner(3, "runtime", { dispatch_id: "x2", seconds: "9" })), /duration/);
    assert.throws(() => cleanExecutorEvent(inner(3, "finish", { status: "aborted" })), /finish status/);
    assert.throws(() => cleanExecutorEvent(inner(3, "phase", { phase: "dessert", cycle: 1 })), /phase/);
    const withData = (type, data, options) => inner(3, type, data, options);
    const dispatchWith = extra => ({ ...wanted, data: { ...wanted.data, ...extra } });
    for (const bad of [
        dispatchWith({ cycle: 0 }), dispatchWith({ round: -1 }), dispatchWith({ executor_task: "t".repeat(201) }), dispatchWith({ stage: "a b" }),
        dispatchWith({ identity: "" }), dispatchWith({ agent_id: "a".repeat(101) }), { ...wanted, seq: 2e9 }, { ...wanted, at: "2026-13-45T25:61:61Z" },
        withData("phase", { phase: "authors", cycle: -1 }),
        withData("runtime", { dispatch_id: "y2", status: "running" }), withData("runtime", { dispatch_id: 2, status: "running" }),
        withData("runtime", { dispatch_id: ["x2"], status: "running" }),
        withData("runtime", { dispatch_id: "x2", started_at: "ontem" }), withData("runtime", { dispatch_id: "x2", ended_at: "ontem" }),
        withData("runtime", { dispatch_id: "x2", seconds: 1e7 }), withData("runtime", { dispatch_id: "x2", observed_model: "m".repeat(101) }),
        withData("runtime", { dispatch_id: "x2", observed_model: "" }), withData("runtime", { dispatch_id: "x2", error: "e".repeat(301) }),
        withData("runtime", { dispatch_id: "x2", cause: "c".repeat(61) }),
        withData("handoff", { from: "../x", to: "coordinator", label: "l", cycle: 1 }),
        withData("handoff", { from: "author-01", to: "../x", label: "l", cycle: 1 }),
        withData("handoff", { from: "author-01", to: "coordinator", label: "l".repeat(501), cycle: 1 }),
        withData("handoff", { from: "author-01", to: "coordinator", label: "l", cycle: -1 }),
        withData("executor", { kind: "a b", label: "l" }), withData("executor", { kind: "run", label: "l".repeat(301) }), withData("executor", { kind: "run", label: "  " }),
    ]) assert.throws(() => cleanExecutorEvent(bad), undefined, JSON.stringify(bad).slice(0, 100));
    // And the largest of each is still fine.
    for (const fine of [
        dispatchWith({ executor_task: "t".repeat(200), label: "l".repeat(300), identity: null, round: 0 }), dispatchWith({ cycle: 1 }), dispatchWith({ agent_id: "a".repeat(100) }),
        withData("runtime", { dispatch_id: "x2", seconds: 9999999, observed_model: "m".repeat(100), error: "e".repeat(300), cause: "c".repeat(60) }),
        withData("runtime", { dispatch_id: "x2", seconds: 0 }), withData("runtime", { dispatch_id: "x2" }),
        withData("handoff", { from: "author-01", to: "coordinator", label: "l".repeat(500), cycle: 0 }),
        withData("executor", { kind: "run", label: "l".repeat(300) }), withData("phase", { phase: "authors", cycle: 0 }),
    ]) assert.doesNotThrow(() => cleanExecutorEvent(fine), JSON.stringify(fine).slice(0, 100));
});

test("the manager follows the executor's journal from start to its end and closes the execution by itself", { timeout: 20000 }, async t => {
    const f = await executorFixture(t);
    await writeExecutorJournal(f.root);
    const feed = executorFeed();
    feed.events.push(inner(1, "phase", { phase: "authors", cycle: 1 }), asked(2, "author-01"),
        inner(3, "runtime", { dispatch_id: "x2", status: "running", started_at: moment(3) }));
    feed.summary = {
        finished: false, outcome: null, cycle: 1, counts: { issued: 1, accepted: 0, rejected: 0, null: 0, repairs: 0 },
        running: [{ agent: "author-01", label: "c01.r0.authors.author-01.a1", stage: "authors", cycle: 1, state: "running", since: moment(3) }],
        driver: { state: "running", stage: "authors", cycle: 1, detail: "", backend: "copilot-cli", age_seconds: 2 },
    };
    const manager = new MonitorManager({ getSession: () => quietSession, reader: async () => f.evidence, executorReader: feed.reader, log: () => {}, browser: async () => {}, heartbeat: 20 });
    t.after(() => manager.shutdown());
    const started = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    const entry = manager.entry(started.execution_id);
    assert.deepEqual(feed.calls[0], { after: 0, epoch: null }, "the first reading is the whole journal");
    assert.deepEqual([started.executor.running.map(item => item.agent), started.executor.journal_cursor, started.executor.driver.state], [["author-01"], 3, "running"], "status says who is running now");
    assert.equal(entry.store.state.dispatches[0].status, "running");
    assert.equal(started.health.state, "active", "the health is the executor's own");
    feed.events.push(inner(4, "runtime", { dispatch_id: "x2", status: "completed", outcome: "accepted", ended_at: moment(70) }, { seconds: 70 }));
    await until(() => entry.store.state.dispatches[0].status === "completed");
    assert.ok(feed.calls.some(call => call.after === 3 && call.epoch === EPOCH), "the next readings start where the last one stopped");
    feed.summary = { ...feed.summary, finished: true, outcome: "approved", running: [] };
    feed.events.push(inner(5, "finish", { status: "completed" }, { seconds: 80 }));
    await until(() => entry.store.state.status === "completed");
    await until(() => entry.healthTimer === null);
    const calls = feed.calls.length;
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(feed.calls.length, calls, "an execution that ended is not read any more");
    const closed = await manager.action({ operation: "status", execution_id: started.execution_id }, "session");
    assert.deepEqual([closed.status, closed.historical, closed.health.state, closed.executor.finished, closed.executor.outcome], ["completed", true, "closed", true, "approved"]);
    assert.equal(feed.calls.length, calls, "and status does not read it either");
});

test("a reading that says there is more is followed until there is no more", async t => {
    const f = await executorFixture(t);
    await writeExecutorJournal(f.root);
    const feed = executorFeed();
    feed.per = 2;
    feed.events.push(inner(1, "phase", { phase: "authors", cycle: 1 }), asked(2, "author-01"), asked(3, "author-02"),
        inner(4, "runtime", { dispatch_id: "x2", status: "running", started_at: moment(4) }),
        inner(5, "runtime", { dispatch_id: "x3", status: "running", started_at: moment(5) }));
    const manager = new MonitorManager({ getSession: () => quietSession, reader: async () => f.evidence, executorReader: feed.reader, log: () => {}, browser: async () => {}, heartbeat: 3600000 });
    t.after(() => manager.shutdown());
    const started = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    assert.deepEqual(feed.calls.slice(0, 3).map(call => call.after), [0, 2, 4]);
    assert.ok(feed.calls.length < 10, "and when the reading says there is no more, that is the end of it");
    assert.deepEqual(manager.entry(started.execution_id).store.state.dispatches.map(item => item.status), ["running", "running"]);
    assert.equal(started.executor.journal_cursor, 5);
});

test("a journal that is not the one being followed is shown as an error and never applied, and the error goes when it does", async t => {
    const f = await executorFixture(t);
    await writeExecutorJournal(f.root);
    const feed = executorFeed();
    feed.events.push(inner(1, "phase", { phase: "authors", cycle: 1 }), asked(2, "author-01"));
    const manager = new MonitorManager({ getSession: () => quietSession, reader: async () => f.evidence, executorReader: feed.reader, log: () => {}, browser: async () => {}, heartbeat: 3600000 });
    t.after(() => manager.shutdown());
    const started = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    const entry = manager.entry(started.execution_id);
    assert.equal(entry.store.state.dispatches.length, 1);
    feed.reset = true;
    feed.events.push(asked(3, "author-02"));
    const refused = await manager.action({ operation: "status", execution_id: started.execution_id }, "session");
    assert.ok(refused.warnings.some(message => /não é o que esta execução vinha acompanhando/.test(message)), refused.warnings.join("|"));
    assert.equal(entry.store.state.dispatches.length, 1, "nothing of it was applied");
    assert.match(entry.store.publicState.executor_live.error, /Reabra o monitor com start/);
    feed.reset = false;
    const recovered = await manager.action({ operation: "status", execution_id: started.execution_id }, "session");
    assert.equal(entry.store.state.dispatches.length, 2);
    assert.deepEqual(recovered.warnings, []);
    assert.equal(entry.store.publicState.executor_live.error, null);
});

test("a reader that fails is a warning, not the end of the monitor, and it is forgotten when the reader works again", async t => {
    const f = await executorFixture(t);
    await writeExecutorJournal(f.root);
    const feed = executorFeed();
    feed.failure = "o Python não respondeu";
    feed.events.push(inner(1, "phase", { phase: "authors", cycle: 1 }), asked(2, "author-01"));
    const warnings = [];
    const manager = new MonitorManager({ getSession: () => quietSession, reader: async () => f.evidence, executorReader: feed.reader, log: message => warnings.push(message), browser: async () => {}, heartbeat: 3600000 });
    t.after(() => manager.shutdown());
    const started = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    assert.ok(started.warnings.includes("o Python não respondeu"));
    assert.ok(warnings.some(message => message.includes("o Python não respondeu")));
    assert.equal(started.health.state, "unobserved", "an executor swarm whose journal cannot be read is not comfortable");
    feed.failure = null;
    const entry = manager.entry(started.execution_id);
    const changes = [];
    entry.store.on("change", state => changes.push(state.reader_error));
    await manager.pollExecutor(entry);
    assert.equal(changes.at(-1), null, "the panel is told the warning is gone, by the reading that made it go");
    assert.deepEqual(manager.info(entry).warnings, []);
    const recovered = await manager.action({ operation: "status", execution_id: started.execution_id }, "session");
    assert.deepEqual(recovered.warnings, []);
    assert.equal(recovered.health.state, "active");
    // A warning that is not the journal's is not the journal's to clear.
    feed.failure = "o Python não respondeu de novo";
    await manager.pollExecutor(entry).catch(() => {});
    entry.store.readerError = "os artefatos não puderam ser lidos";
    feed.failure = null;
    await manager.pollExecutor(entry);
    assert.equal(entry.store.readerError, "os artefatos não puderam ser lidos");
});

test("the health follows the executor's own classification at every reading, not only at every heartbeat", async t => {
    const f = await executorFixture(t);
    await writeExecutorJournal(f.root);
    const feed = executorFeed();
    feed.events.push(inner(1, "phase", { phase: "authors", cycle: 1 }), asked(2, "author-01"));
    const manager = new MonitorManager({ getSession: () => quietSession, reader: async () => f.evidence, executorReader: feed.reader, log: () => {}, browser: async () => {}, heartbeat: 3600000 });
    t.after(() => manager.shutdown());
    const started = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    const entry = manager.entry(started.execution_id);
    assert.equal(started.health.state, "active");
    const reason = "o executor foi interrompido; o mesmo comando retoma de onde parou";
    feed.health = { state: "stalled", reason, threshold_seconds: 180 };
    const changes = [];
    entry.store.on("change", state => changes.push(state.health?.state));
    await manager.pollExecutor(entry);
    assert.deepEqual([entry.store.health.state, entry.store.health.reason], ["stalled", reason]);
    assert.equal(JSON.parse(await fs.readFile(path.join(entry.store.directory, "health.json"), "utf8")).state, "stalled", "and it reaches the file the watchdog reads");
    assert.ok(changes.includes("stalled"), "the panel is told");
});

test("an execution whose journal already ends is closed at once, and one reading is not many", async t => {
    const f = await executorFixture(t);
    await writeExecutorJournal(f.root);
    const feed = executorFeed();
    feed.events.push(inner(1, "phase", { phase: "authors", cycle: 1 }), asked(2, "author-01"), inner(3, "finish", { status: "escalated" }));
    feed.summary = { finished: true, outcome: "escalated", cycle: 1, counts: {}, running: [], driver: null };
    feed.health = { state: "closed", reason: "o executor encerrou a execução: escalated", threshold_seconds: 180 };
    const manager = new MonitorManager({ getSession: () => quietSession, reader: async () => f.evidence, executorReader: feed.reader, log: () => {}, browser: async () => {}, heartbeat: 3600000 });
    t.after(() => manager.shutdown());
    const started = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    const entry = manager.entry(started.execution_id);
    assert.deepEqual([started.status, started.historical, started.health.state, entry.healthTimer], ["escalated", true, "closed", null], "the heartbeat of a run that ended is gone");
    assert.ok(feed.calls.length < 10, "a reading that says there is no more is the last of its kind");
    const calls = feed.calls.length;
    await manager.pollExecutor(entry);
    await manager.refresh(entry);
    assert.equal(feed.calls.length, calls, "an execution that ended is not read again, not even when asked");
});

test("a refresh that finds the run has ended closes the execution too", async t => {
    const f = await executorFixture(t);
    await writeExecutorJournal(f.root);
    const feed = executorFeed();
    feed.events.push(inner(1, "phase", { phase: "authors", cycle: 1 }), asked(2, "author-01"));
    const manager = new MonitorManager({ getSession: () => quietSession, reader: async () => f.evidence, executorReader: feed.reader, log: () => {}, browser: async () => {}, heartbeat: 3600000 });
    t.after(() => manager.shutdown());
    const started = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    const entry = manager.entry(started.execution_id);
    assert.notEqual(entry.healthTimer, null);
    feed.events.push(inner(3, "finish", { status: "completed" }));
    feed.health = { state: "closed", reason: "o executor encerrou a execução: approved", threshold_seconds: 180 };
    await manager.refresh(entry);
    assert.deepEqual([entry.store.state.status, entry.healthTimer, entry.store.health.state], ["completed", null, "closed"], "stopped looking, and said so once");
});

test("a swarm with no executor journal is never read for one, and keeps the coordinator's operations", async t => {
    const f = await executorFixture(t);
    const feed = executorFeed();
    const manager = new MonitorManager({ getSession: () => quietSession, reader: async () => f.evidence, executorReader: feed.reader, log: () => {}, browser: async () => {}, heartbeat: 20 });
    t.after(() => manager.shutdown());
    const started = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    await manager.action({ operation: "phase", execution_id: started.execution_id, phase: "authors", cycle: 1 }, "session");
    const dispatch = await manager.action({ operation: "dispatch", execution_id: started.execution_id, agent_id: "author-01", cycle: 1 }, "session");
    assert.ok(dispatch.task_name);
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(feed.calls.length, 0, "no journal, no Python process");
    assert.equal(started.executor, null);
});

test("an execution an executor drives refuses what a coordinator records by hand, and still answers the rest", async t => {
    const f = await executorFixture(t);
    await writeExecutorJournal(f.root);
    const feed = executorFeed();
    feed.events.push(inner(1, "phase", { phase: "authors", cycle: 1 }), asked(2, "author-01"));
    const manager = new MonitorManager({ getSession: () => quietSession, reader: async () => f.evidence, executorReader: feed.reader, log: () => {}, browser: async () => {}, heartbeat: 3600000 });
    t.after(() => manager.shutdown());
    const started = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    const id = started.execution_id;
    const entry = manager.entry(id);
    const before = entry.store.state.sequence;
    for (const args of [{ operation: "dispatch", agent_id: "author-01", cycle: 1 }, { operation: "phase", phase: "gate", cycle: 1 },
        { operation: "handoff", from: "author-01", to: "coordinator", label: "x" }, { operation: "finish", status: "completed" }]) {
        await assert.rejects(manager.action({ ...args, execution_id: id }, "session"), /conduzido pelo executor determinístico/, args.operation);
    }
    assert.equal(entry.store.state.sequence, before, "none of them wrote anything");
    await fs.rm(path.join(f.root, "reports", "execution", "journal.jsonl"));
    await assert.rejects(manager.action({ operation: "phase", execution_id: id, phase: "gate", cycle: 1 }, "session"), /conduzido pelo executor determinístico/,
        "an execution that has followed an executor's journal is still one when the file is gone");
    for (const operation of ["status", "refresh", "open"]) {
        assert.equal((await manager.action({ operation, execution_id: id, surface: "browser" }, "session")).execution_id, id, operation);
    }
    const recovered = await manager.action({ operation: "recovery", execution_id: id, rule: "R4", detail: "o mesmo comando retomou a execução" }, "session");
    assert.equal(recovered.recoveries.length, 1, "a resumed run is still never shown as a clean one");
});

test("the Python projection and the panel agree on a journal written the way the executor writes it", { timeout: 30000 }, async t => {
    const f = await executorFixture(t);
    const identity = "abcdef0123456789";
    const entries = [
        { seq: 1, at: moment(0), event: "run_started", plan_sha256: "p", agents: 3 },
        { seq: 2, at: moment(1), event: "task_issued", task_id: "c01.r0.authors.author-01", attempt: 1, stage: "authors", kind: "author", agent: "author-01", cycle: 1, round: 0, inputs_sha256: identity },
        { seq: 3, at: moment(2), event: "task_started", task_id: "c01.r0.authors.author-01", attempt: 1, agent: "author-01", inputs_sha256: identity },
        { seq: 4, at: moment(62), event: "task_recorded", task_id: "c01.r0.authors.author-01", attempt: 1, stage: "authors", kind: "author", agent: "author-01", cycle: 1, round: 0, outcome: "accepted", errors: [], seconds: 541, runtime: { seconds: 60.2, models_seen: "gpt-5.5" }, inputs_sha256: identity },
        { seq: 5, at: moment(63), event: "task_issued", task_id: "c01.r0.consolidation.coordinator", attempt: 1, stage: "consolidation", kind: "coordinator", agent: "coordinator", cycle: 1, round: 0, inputs_sha256: identity },
    ];
    await writeExecutorJournal(f.root, entries);
    const whole = await readExecutor(f.root);
    assert.deepEqual([whole.present, whole.epoch, whole.cursor, whole.reset, whole.more], [true, moment(0), 5, false, false]);
    assert.deepEqual(whole.summary.running.map(item => [item.agent, item.state]), [["coordinator", "queued"]]);
    const resumed = await readExecutor(f.root, { after: 3, epoch: whole.epoch });
    assert.deepEqual(resumed.events.map(event => [event.seq, event.type]), [[4, "runtime"], [5, "phase"], [5, "dispatch"], [5, "handoff"]]);
    assert.equal((await readExecutor(f.root, { after: 3, epoch: "2000-01-01T00:00:00.000Z" })).reset, true);
    await assert.rejects(readExecutor(f.root, { after: -1 }), /Cursor do executor inválido/);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    await store.observeExecutor({ epoch: whole.epoch, cursor: whole.cursor, events: whole.events });
    assert.equal(store.state.executor.skipped, 0, "everything the projection says is something the panel can hold");
    const [author, coordinator] = store.state.dispatches;
    assert.deepEqual([author.status, author.seconds, author.observed_model, author.started_at, author.ended_at], ["completed", 60.2, "gpt-5.5", moment(2), moment(62)]);
    assert.deepEqual([coordinator.agent_id, coordinator.status, coordinator.stage], ["coordinator", "queued", "consolidation"]);
    assert.deepEqual(store.state.edges.map(edge => [edge.from, edge.to, edge.label]), [["author-01", "coordinator", "seções"]]);
    const manager = new MonitorManager({ getSession: () => quietSession, reader: async () => f.evidence, log: () => {}, browser: async () => {}, heartbeat: 3600000 });
    t.after(() => manager.shutdown());
    const started = await manager.start(f.root, { sessionId: "another-session", autoOpen: false });
    assert.deepEqual([started.executor.journal_cursor, started.executor.events_not_shown, started.executor.running.map(item => item.agent)], [5, 0, ["coordinator"]]);
    assert.equal(manager.entry(started.execution_id).store.state.dispatches.length, 2);
});

test("the panel reads an executor run as one: its coordinator is a task, its clocks come from the journal, its label is the executor's", () => {
    const now = Date.parse(moment(400));
    const live = (running, driver = { state: "running" }) => ({ running, driver, health: { state: "active" } });
    const base = {
        status: "active", connection: "connected", cycle: 1, phase: "authors", evidence: { cycles: [] }, dispatches: [], agents: [],
        executor: { epoch: EPOCH, cursor: 5, skipped: 0 }, session_activity: { status: "processing", observed_at: new Date().toISOString(), stale: false },
        executor_live: live([{ agent: "author-01", state: "running" }, { agent: "author-02", state: "queued" }]),
    };
    const coordinator = { id: "coordinator", kind: "coordinator" };
    assert.equal(agentStatus(base, coordinator, 1, true), "declared", "the session is not the coordinator of an executor run");
    assert.equal(agentStatus({ ...base, executor: undefined }, coordinator, 1, true), "running", "while it is, in a run the coordinator drives");
    assert.equal(sessionLabel(base, 1, true), "Executor: 1 agente em execução · 1 na fila");
    assert.equal(sessionLabel({ ...base, executor_live: live([{ state: "running" }, { state: "running" }]) }, 1, true), "Executor: 2 agentes em execução");
    assert.equal(sessionLabel({ ...base, executor_live: live([{ state: "queued" }, { state: "queued" }]) }, 1, true), "Executor: 2 na fila");
    assert.equal(sessionLabel({ ...base, executor_live: live([]) }, 1, true), "Executor entre etapas");
    assert.equal(sessionLabel({ ...base, executor_live: live([], null) }, 1, true), "Executor sem batimento");
    assert.equal(sessionLabel({ ...base, executor_live: live([], { state: "blocked" }) }, 1, true), "Executor parado: precisa de uma pessoa");
    assert.equal(sessionLabel({ ...base, executor_live: live([], { state: "failed" }) }, 1, true), "Executor parado: precisa de uma pessoa");
    assert.equal(sessionLabel({ ...base, executor_live: undefined }, 1, true), "Executor não observado");
    assert.equal(sessionLabel({ ...base, executor_live: { running: "x" } }, 1, true), "Executor sem batimento", "a reading that is not a list is no work");
    assert.equal(sessionLabel(base, 1, false), "Executor não observado");
    assert.equal(sessionLabel({ ...base, connection: "disconnected" }, 1, true), "Executor não observado");
    assert.equal(sessionLabel({ ...base, status: "completed" }, 1, true), "Registro encerrado");
    assert.equal(sessionLabel(base, 2, true), "Rodada histórica");
    assert.equal(executorLabel(base, true), sessionLabel(base, 1, true));
    assert.equal(sessionWorking(base, 1, true), true);
    for (const quiet of [{ ...base, executor_live: live([{ state: "queued" }]) }, { ...base, executor_live: undefined }, { ...base, executor_live: { running: 5 } },
        { ...base, status: "completed" }, { ...base, connection: "disconnected" }]) assert.equal(sessionWorking(quiet, 1, true), false);
    assert.equal(sessionWorking(base, 2, true), false);
    assert.equal(sessionWorking(base, 1, false), false);
    assert.equal(sessionWorking({ ...base, executor: undefined }, 1, true), true, "the coordinator's session is working when it is processing");
    assert.equal(sessionWorking({ ...base, executor: undefined, session_activity: { status: "waiting" } }, 1, true), false);
    const dispatch = extra => ({ source: "executor", status: "running", registered_at: moment(0), started_at: moment(100), ended_at: null, seconds: 99999, ...extra });
    assert.deepEqual(dispatchClock(dispatch(), "running", now), { kind: "running", seconds: 300 });
    assert.deepEqual(dispatchClock(dispatch({ status: "completed", ended_at: moment(160) }), "completed", now), { kind: "took", seconds: 60 }, "from the journal's times, not from the seconds a record states");
    assert.deepEqual(dispatchClock(dispatch({ status: "failed", ended_at: moment(160) }), "failed", now), { kind: "took", seconds: 60 });
    assert.deepEqual(dispatchClock(dispatch({ status: "queued", started_at: null }), "queued", now), { kind: "waiting", seconds: 400 });
    assert.equal(dispatchClock(dispatch(), "unknown", now), null, "a record seen from a closed panel is not made to run on");
    assert.equal(dispatchClock(dispatch(), "idle", now), null);
    assert.equal(dispatchClock(dispatch({ started_at: null }), "running", now), null);
    assert.equal(dispatchClock(dispatch({ status: "queued", registered_at: null }), "queued", now), null);
    assert.equal(dispatchClock(dispatch({ status: "failed", ended_at: moment(50) }), "failed", now), null, "an end before the start is not a duration");
    assert.equal(dispatchClock(dispatch({ status: "completed", ended_at: null }), "completed", now), null);
    assert.equal(dispatchClock(dispatch({ started_at: moment(500) }), "running", now).seconds, 0, "a start in the future is not negative");
    assert.equal(dispatchClock({ ...dispatch(), source: "runtime" }, "running", now), null, "only an executor's dispatch is measured this way");
    assert.equal(dispatchClock(undefined, "declared", now), null);
    assert.equal(agentStateText(dispatch(), "running", now), "Executando · 5 min 00 s");
    assert.equal(agentStateText(dispatch({ status: "completed", ended_at: moment(160) }), "completed", now), "Concluído · 1 min 00 s");
    assert.equal(agentStateText(dispatch({ status: "queued", started_at: null }), "queued", now), "Aguardando execução · 6 min 40 s");
    assert.equal(agentStateText(undefined, "declared", now), AGENT_STATUS.declared);
    assert.equal(agentStateText({ ...dispatch(), source: "runtime" }, "running", now), AGENT_STATUS.running);
    assert.deepEqual(["0", 0], ["0", formatDuration(0) === "0 s" ? 0 : 1]);
    for (const [seconds, text] of [[0, "0 s"], [59.4, "59 s"], [59.6, "1 min 00 s"], [60, "1 min 00 s"], [65, "1 min 05 s"], [3599, "59 min 59 s"],
        [3600, "1 h 00 min"], [3725, "1 h 02 min"], [90000, "25 h 00 min"], [-1, ""], [Number.NaN, ""], [Infinity, ""], [undefined, ""], ["9", ""]]) {
        assert.equal(formatDuration(seconds), text, String(seconds));
    }
    assert.equal(runStatusText({ executor: {}, status: "active" }), "Execução em andamento pelo executor determinístico");
    assert.equal(runStatusText({ executor: {}, status: "observing" }), "Aguardando o journal do executor");
    assert.equal(runStatusText({ executor: {}, status: "completed" }), "Encerrada pelo executor: aprovada");
    assert.equal(runStatusText({ executor: {}, status: "escalated" }), "Encerrada pelo executor: escalada ao usuário");
    assert.equal(runStatusText({ status: "active" }), "Execução em andamento");
    assert.equal(runStatusText({ executor: {}, status: "closing" }), "Encerramento solicitado; aguardando confirmação do runtime");
    assert.equal(runStatusText({ status: "weird" }), "Estado não registrado");
    assert.deepEqual(OUTCOME_LABELS, { accepted: "Aceito", rejected: "Recusado", null: "Sem resultado", superseded: "Substituído" });
    assert.deepEqual(executorWarnings(base), []);
    assert.deepEqual(executorWarnings({ executor: { skipped: 2 }, executor_live: { error: "o journal mudou", skipped_lines: 3 } }), [
        "o journal mudou",
        "2 evento(s) do journal do executor não puderam ser exibidos; o journal continua íntegro.",
        "3 linha(s) do journal do executor estão ilegíveis e foram ignoradas na leitura.",
    ]);
});

test("the panel measures an executor's agents from the journal's times, keeps the clocks running and names the executor in the history", async () => {
    const source = await fs.readFile(new URL("../ui/app.mjs", import.meta.url), "utf8");
    assert.doesNotMatch(source, /dispatch\??\.seconds/, "the seconds of a record run from the issue and include every stop");
    assert.ok((source.match(/agentStateText\(/g) ?? []).length >= 4, "the card, its tooltip and the details all show the clock");
    assert.doesNotMatch(source, /class: "agent-state" \}, STATUS\[|\["Estado", STATUS\[/, "neither the card nor the details show the bare state of an agent that has a clock");
    assert.match(source, /setInterval\([\s\S]{0,400}renderGraph\(\)[\s\S]{0,200}5000\)/, "the clocks move between the states the server pushes");
    assert.match(source, /clearInterval\(clock\)/);
    assert.match(source, /executor: "EXECUTOR"/);
    assert.match(source, /event\.type === "executor" \? data\.label/);
    assert.match(source, /runStatusText\(data\)/);
    assert.match(source, /sessionWorking\(data, selectedCycle, connected\)/);
    assert.match(source, /executorWarnings\(shownState\)/);
    assert.doesNotMatch(source, /: `Conexão: \$\{data\.connection\}`;/, "an event of a kind the panel does not know is not described as a connection");
    assert.match(source, /`Evento: \$\{event\.type\}`/);
    assert.match(source, /\["Tentativa", dispatch\.attempt\]/);
    assert.match(source, /\["Resultado", OUTCOME_LABELS\[dispatch\.outcome\]/);
    assert.match(source, /\["Motivo", dispatch\.error/);
    assert.match(source, /"Registro do executor"/);
    assert.match(source, /pedido emitido pelo executor/);
    assert.match(source, /Execução encerrada pelo executor/);
});

test("the reducer alone ignores a cursor it has already passed and refuses a count of dropped events that is not a count", async t => {
    const f = await executorFixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    const event = (id, data) => ({ id, sequence: store.state.sequence + 1, at: moment(0), source: "executor", type: "executor_batch", data });
    const note = label => inner(3, "executor", { kind: "run", label });
    const first = reduce(store.state, event("one", { epoch: EPOCH, cursor: 3, events: [note("x")] }));
    assert.equal(first.executor.cursor, 3);
    assert.equal(reduce(first, event("two", { epoch: EPOCH, cursor: 3, events: [note("y")] })), first, "the same cursor under another name is the same reading");
    assert.equal(reduce(first, event("three", { epoch: EPOCH, cursor: 2, events: [] })), first);
    for (const dropped of [-1, 1.5, "2", 20001]) {
        assert.throws(() => reduce(first, event(`bad-${dropped}`, { epoch: EPOCH, cursor: 9, events: [], dropped })), /Invalid executor batch/, String(dropped));
    }
    assert.equal(reduce(first, event("four", { epoch: EPOCH, cursor: 9, events: [], dropped: 5 })).executor.skipped, 5);
    assert.equal(reduce(first, event("five", { epoch: EPOCH, cursor: 9, events: [], dropped: 20000 })).executor.skipped, 20000);
    assert.throws(() => reduce(first, event("six", { epoch: EPOCH, cursor: 9, events: undefined })), /Invalid executor batch/);
    assert.throws(() => reduce(first, event("seven", undefined)), /Invalid executor batch/);
});

test("the history keeps the last thousand events however many a reading brings, and a batch has a size limit", async t => {
    const f = await executorFixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    const notes = count => Array.from({ length: count }, (_, index) => inner(index + 1, "executor", { kind: "run", label: `nota ${index + 1}` }, { seconds: index + 1 }));
    await store.observeExecutor({ epoch: EPOCH, cursor: 1100, events: notes(1100) });
    assert.equal(store.state.events.length, 1000);
    assert.deepEqual([store.state.events[0].data.label, store.state.events.at(-1).data.label], ["nota 101", "nota 1100"]);
    await assert.rejects(store.observeExecutor({ epoch: EPOCH, cursor: 30000, events: notes(20001) }), /Invalid executor batch/);
    assert.equal(store.state.executor.cursor, 1100, "a batch that is too large is refused whole");
    await store.observeExecutor({ epoch: EPOCH, cursor: 30000, events: notes(20000) });
    assert.equal(store.state.executor.cursor, 30000, "and the largest that is allowed is applied");
    assert.equal(store.state.events.length, 1000);
});

test("the panel is told when who is running changes, and only then", async t => {
    const f = await executorFixture(t);
    const store = await RunStore.create(f.root, { sessionId: "session", evidence: f.evidence });
    f.resources.push(store);
    let changes = 0;
    store.on("change", () => { changes += 1; });
    const reading = (running, driver = "running", stage = "authors") => ({ summary: { running, driver: { state: driver, stage } }, health: { state: "active", reason: "ok" } });
    const one = [{ agent: "author-01", state: "running" }];
    const two = [...one, { agent: "author-02", state: "queued" }];
    store.setExecutorLive(reading(one));
    assert.equal(changes, 1);
    store.setExecutorLive(reading(one));
    assert.equal(changes, 1, "the same reading says nothing new");
    store.setExecutorLive(reading(two));
    assert.equal(changes, 2);
    store.setExecutorLive(reading(two, "waiting"));
    assert.equal(changes, 3, "the driver changed state");
    store.setExecutorLive(reading(two, "waiting", "reviewers"));
    assert.equal(changes, 4, "the driver changed stage");
    store.setExecutorLive(reading(two, "waiting", "reviewers"), { error: "o journal mudou" });
    assert.equal(changes, 5, "an error appeared");
    store.setExecutorLive({ summary: { ...reading(two, "waiting", "reviewers").summary, finished: true }, health: { state: "closed", reason: "ok" } }, { error: "o journal mudou" });
    assert.equal(changes, 6, "the run finished");
    store.setExecutorLive(null);
    assert.equal(changes, 7);
    store.setExecutorLive(null);
    assert.equal(changes, 7, "and nothing is nothing");
    await store.close();
    const closed = changes;
    store.setExecutorLive(reading(one));
    assert.equal(changes, closed, "a closed store says nothing more");
});

test("readings never overlap, and one asked for while another is under way runs once more afterwards", { timeout: 20000 }, async t => {
    const f = await executorFixture(t);
    await writeExecutorJournal(f.root);
    let active = 0;
    let peak = 0;
    let calls = 0;
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    const events = [inner(1, "phase", { phase: "authors", cycle: 1 })];
    const reader = async (_root, args) => {
        calls += 1;
        active += 1;
        peak = Math.max(peak, active);
        // What a reading finds is decided when it looks, not when it finishes, and the first one is slow.
        const answer = {
            schema_version: 1, present: true, epoch: EPOCH, reset: false, more: false, skipped_lines: 0, cursor: events.length,
            events: events.filter(item => item.seq > args.after), summary: {}, health: { state: "active", reason: "ok" },
        };
        if (calls === 1) await gate;
        active -= 1;
        return answer;
    };
    const manager = new MonitorManager({ getSession: () => quietSession, reader: async () => f.evidence, executorReader: reader, log: () => {}, browser: async () => {}, heartbeat: 3600000 });
    t.after(() => manager.shutdown());
    const starting = manager.start(f.root, { sessionId: "session", autoOpen: false });
    await until(() => calls === 1);
    const entry = [...manager.runs.values()][0];
    const first = manager.pollExecutor(entry);
    const second = manager.pollExecutor(entry);
    assert.equal(first, second, "asking again while one is under way is waiting for that one");
    events.push(inner(2, "executor", { kind: "run", label: "chegou durante a leitura" }));
    release();
    await Promise.all([starting, first]);
    assert.equal(entry.store.state.executor.cursor, 2, "what arrived during the slow reading was read by the one asked for in the meantime");
    assert.ok(calls >= 2);
    assert.equal(peak, 1, "and never at the same time");
});

test("a reading with nothing to apply applies nothing, and a reader that never stops saying there is more is stopped", async t => {
    const f = await executorFixture(t);
    await writeExecutorJournal(f.root);
    let mode = "absent";
    let calls = 0;
    const reader = async () => {
        calls += 1;
        if (mode === "absent") return { schema_version: 1, present: false };
        const common = { schema_version: 1, present: true, reset: false, skipped_lines: 0, summary: {}, health: { state: "waiting", reason: "o journal não tem eventos datados" } };
        if (mode === "empty") return { ...common, epoch: "", cursor: 0, more: false, events: [] };
        return { ...common, epoch: EPOCH, cursor: 1, more: true, events: [inner(1, "executor", { kind: "run", label: "x" })] };
    };
    const manager = new MonitorManager({ getSession: () => quietSession, reader: async () => f.evidence, executorReader: reader, log: () => {}, browser: async () => {}, heartbeat: 3600000 });
    t.after(() => manager.shutdown());
    const started = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    const entry = manager.entry(started.execution_id);
    assert.deepEqual([started.executor, entry.store.executorLive, entry.store.state.executor], [null, null, undefined], "a journal the reader says is not there is nothing");
    mode = "empty";
    const empty = await manager.action({ operation: "status", execution_id: started.execution_id }, "session");
    assert.deepEqual([empty.executor, entry.store.state.executor, typeof entry.store.executorLive.observed_at], [null, undefined, "string"], "an empty journal is read and has nothing to apply");
    assert.equal(empty.health.state, "waiting", "but the swarm is known to be an executor's, and the health is the executor's own");
    assert.deepEqual(empty.warnings, [], "and nothing about it is a failure");
    // What is counted next is the one reading this status asks for. The folder's own file events would join it (a reading
    // already under way, or the refresh a finished one queues again), so that path is switched off and what it had
    // started is waited for: no pause is long enough to promise the same on a loaded machine.
    manager.refresh = async () => {};
    await until(() => !entry.polling && !entry.refreshing);
    mode = "endless";
    const before = calls;
    await manager.action({ operation: "status", execution_id: started.execution_id }, "session");
    assert.equal(calls - before, 50, "fifty readings in a row, then it stops");
    assert.equal(entry.store.state.executor.cursor, 1);
});

test("a refresh reads the journal too, and a journal that cannot be read does not keep the artifacts from being read", async t => {
    const f = await executorFixture(t);
    await writeExecutorJournal(f.root);
    const feed = executorFeed();
    feed.events.push(inner(1, "phase", { phase: "authors", cycle: 1 }), asked(2, "author-01"));
    let title = f.evidence.title;
    const manager = new MonitorManager({ getSession: () => quietSession, reader: async () => ({ ...f.evidence, title }), executorReader: feed.reader, log: () => {}, browser: async () => {}, heartbeat: 3600000 });
    t.after(() => manager.shutdown());
    const started = await manager.start(f.root, { sessionId: "session", autoOpen: false });
    const entry = manager.entry(started.execution_id);
    feed.events.push(inner(3, "runtime", { dispatch_id: "x2", status: "running", started_at: moment(3) }));
    await manager.refresh(entry);
    assert.equal(entry.store.state.dispatches[0].status, "running");
    feed.failure = "o Python não respondeu";
    title = "Artefatos atualizados";
    await assert.rejects(manager.refresh(entry), /o Python não respondeu/);
    assert.equal(entry.store.state.title, "Artefatos atualizados", "the artifacts were read though the journal was not");
    feed.failure = null;
    await manager.refresh(entry);
    assert.equal(entry.store.readerError, null, "and a refresh that works clears the warning");
});

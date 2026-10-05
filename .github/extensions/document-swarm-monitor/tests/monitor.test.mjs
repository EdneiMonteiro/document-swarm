import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { RunStore, history, readHistorical } from "../state.mjs";
import { createMonitorServer, readArtifact } from "../server.mjs";
import { MonitorManager, readEvidence } from "../manager.mjs";
import { yieldsToProject } from "../ownership.mjs";
import { canvasWindowTitle, createCanvasWindow } from "../window.mjs";
import { AGENT_STATUS, archived, agentStatus, dispatchStatus, sessionLabel } from "../ui/app.mjs";

const digest = value => createHash("sha256").update(value).digest("hex");
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
    assert.ok(store.healthError);
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

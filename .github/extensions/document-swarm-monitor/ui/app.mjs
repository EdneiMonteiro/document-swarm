export const AGENT_STATUS = {
    declared: "Declarado", queued: "Aguardando execução", running: "Executando",
    idle: "Disponível", completed: "Concluído", failed: "Falhou",
    cancelled: "Cancelado", unknown: "Não observado",
};
const TERMINAL = new Set(["completed", "escalated", "aborted"]);

export const APPROVAL_GRADES = ["A-", "A"];

// The grade a cycle was judged against: the one its own review declares, then the swarm's, and the original A when
// neither says (evidence recorded before the field existed).  Hard-coding A would paint a gate-approved A- as failing.
export function approvalGrade(cycle, evidence) {
    for (const candidate of [cycle?.approval_grade, evidence?.approval_grade]) {
        if (APPROVAL_GRADES.includes(candidate)) return candidate;
    }
    return "A";
}

export function meetsApproval(scale, grade, bar) {
    return scale.includes(grade) && scale.indexOf(grade) >= scale.indexOf(bar);
}

export function gradeKind(value, scale, bar) {
    if (!value || !scale.includes(value)) return "missing";
    return meetsApproval(scale, value, bar) ? "good" : "bad";
}

export function gradeView(value, cycle, evidence) {
    const kind = gradeKind(value, evidence.grade_scale, approvalGrade(cycle, evidence));
    return { text: kind === "missing" ? "N/D" : value, kind };
}

export function gradeCount(cycle, evidence) {
    if (cycle?.consistent === false) return "Divergente";
    if (!cycle?.topics.length) return "Pendente";
    const bar = approvalGrade(cycle, evidence);
    const passing = cycle.topics.filter(topic => meetsApproval(evidence.grade_scale, topic.grade, bar)).length;
    return `${passing} / ${cycle.topics.length}`;
}

export function approvalLegend(cycle, evidence) {
    return `Nota de aprovação: ${approvalGrade(cycle, evidence)} · abaixo bloqueia · achado crítico veta`;
}

export function gradeCaption(cycle, evidence) {
    const bar = approvalGrade(cycle, evidence);
    return { label: `≥ ${bar}`, title: `Tópicos com nota mínima ${bar} ou acima; isso não substitui o gate` };
}

export function archived(state, cycle) {
    return state.connection === "historical" || TERMINAL.has(state.status) || cycle !== state.cycle;
}

export function dispatchStatus(state, dispatch, cycle, connected) {
    if (!dispatch) return "declared";
    const status = Object.hasOwn(AGENT_STATUS, dispatch.status) ? dispatch.status : "unknown";
    if (["completed", "failed", "cancelled"].includes(status)) return status;
    if (status === "idle" && archived(state, cycle)) return "idle";
    if (archived(state, cycle) || !connected || state.connection !== "connected" || dispatch.observation_stale) return "unknown";
    return status;
}

export function agentStatus(state, agent, cycle, connected) {
    const dispatch = state.dispatches.filter(item => item.agent_id === agent.id && item.cycle === cycle).at(-1);
    if (dispatch || agent.kind !== "coordinator") return dispatchStatus(state, dispatch, cycle, connected);
    // Under the executor the coordinator is a task like any other, not the session the panel is open in.
    if (state.executor) return "declared";
    if (cycle === state.cycle && state.closure?.confirmation === "session.idle" && TERMINAL.has(state.status)) {
        return state.status === "aborted" ? "cancelled" : "completed";
    }
    const activity = state.session_activity;
    if (archived(state, cycle) || !connected || state.connection !== "connected" || !activity || activity.stale) return "declared";
    if (activity.status === "processing") return "running";
    if (["waiting", "idle"].includes(activity.status)) return "idle";
    return "unknown";
}

export function sessionLabel(state, cycle, connected) {
    if (archived(state, cycle)) return TERMINAL.has(state.status) ? "Registro encerrado" : "Rodada histórica";
    if (state.executor) return executorLabel(state, connected);
    if (!connected || state.connection !== "connected" || !state.session_activity || state.session_activity.stale) return "Sessão não observada";
    return {
        processing: state.status === "closing" ? "Sessão finalizando" : "Sessão processando",
        waiting: "Principal entre etapas",
        idle: "Sessão sem tarefas em curso",
        completion_declared: "Fim da sessão não confirmado",
    }[state.session_activity.status] ?? "Sessão não observada";
}

const pluralAgents = count => `${count} ${count === 1 ? "agente" : "agentes"}`;

/** What the executor is doing, from the last reading of its journal and heartbeat. */
export function executorLabel(state, connected) {
    const live = state.executor_live;
    if (!connected || state.connection !== "connected" || !live) return "Executor não observado";
    const rows = Array.isArray(live.running) ? live.running : [];
    const running = rows.filter(item => item.state === "running").length;
    const queued = rows.length - running;
    if (running) return `Executor: ${pluralAgents(running)} em execução${queued ? ` · ${queued} na fila` : ""}`;
    if (queued) return `Executor: ${queued} na fila`;
    if (["blocked", "failed"].includes(live.driver?.state)) return "Executor parado: precisa de uma pessoa";
    return live.driver ? "Executor entre etapas" : "Executor sem batimento";
}

/** Whether the label above describes work going on now, which the panel paints differently from waiting. */
export function sessionWorking(state, cycle, connected) {
    if (archived(state, cycle) || !connected || state.connection !== "connected") return false;
    if (state.executor) return Array.isArray(state.executor_live?.running) && state.executor_live.running.some(item => item.state === "running");
    return state.session_activity?.status === "processing";
}

export const OUTCOME_LABELS = { accepted: "Aceito", rejected: "Recusado", null: "Sem resultado", superseded: "Substituído" };
const ENDED = ["completed", "failed", "cancelled"];

export function formatDuration(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "";
    const total = Math.round(seconds);
    if (total < 60) return `${total} s`;
    const minutes = Math.floor(total / 60);
    if (minutes < 60) return `${minutes} min ${String(total % 60).padStart(2, "0")} s`;
    return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
}

/**
 * How long an executor's dispatch has been waiting, running or took, from the times its journal recorded and never from
 * the seconds a record states: those run from the issue and take in every stop of the executor in between.  Only the
 * state the panel shows decides which of the three it is, so an old record is never made to run on.
 */
export function dispatchClock(dispatch, status, now) {
    if (dispatch?.source !== "executor") return null;
    const started = Date.parse(dispatch.started_at ?? "");
    const ended = Date.parse(dispatch.ended_at ?? "");
    const issued = Date.parse(dispatch.registered_at ?? "");
    if (ENDED.includes(status)) {
        return Number.isFinite(started) && Number.isFinite(ended) && ended >= started ? { kind: "took", seconds: (ended - started) / 1000 } : null;
    }
    if (status === "running" && Number.isFinite(started)) return { kind: "running", seconds: Math.max(0, (now - started) / 1000) };
    if (status === "queued" && Number.isFinite(issued)) return { kind: "waiting", seconds: Math.max(0, (now - issued) / 1000) };
    return null;
}

export function agentStateText(dispatch, status, now) {
    const clock = dispatchClock(dispatch, status, now);
    return clock ? `${AGENT_STATUS[status]} · ${formatDuration(clock.seconds)}` : AGENT_STATUS[status];
}

const RUN_STATUS = {
    observing: "Observação iniciada", active: "Execução em andamento",
    closing: "Encerramento solicitado; aguardando confirmação do runtime",
    completed: "Encerramento registrado", escalated: "Escalação registrada", aborted: "Interrupção registrada",
};
const EXECUTOR_RUN_STATUS = {
    observing: "Aguardando o journal do executor", active: "Execução em andamento pelo executor determinístico",
    completed: "Encerrada pelo executor: aprovada", escalated: "Encerrada pelo executor: escalada ao usuário",
};

export function runStatusText(state) {
    return (state.executor ? EXECUTOR_RUN_STATUS[state.status] : null) ?? RUN_STATUS[state.status] ?? "Estado não registrado";
}

/** The warnings the panel owes the person looking at it: what could not be read, and what was read but cannot be shown. */
export function executorWarnings(state) {
    const warnings = [];
    if (state.executor_live?.error) warnings.push(state.executor_live.error);
    if (state.executor?.skipped > 0) {
        warnings.push(`${state.executor.skipped} evento(s) do journal do executor não puderam ser exibidos; o journal continua íntegro.`);
    }
    if (state.executor_live?.skipped_lines > 0) {
        warnings.push(`${state.executor_live.skipped_lines} linha(s) do journal do executor estão ilegíveis e foram ignoradas na leitura.`);
    }
    return warnings;
}

export function healthLabel(state, cycle, connected) {
    if (archived(state, cycle) || !connected) return null;
    const health = state.health;
    if (!health || !["stalled", "unobserved"].includes(health.state)) return null;
    const title = health.state === "stalled" ? "Execução parada" : "Observação perdida";
    return { state: health.state, title, detail: `${title}: ${health.reason}` };
}

async function startInterface() {
    const $ = id => document.getElementById(id);
    const token = new URLSearchParams(location.hash.slice(1)).get("token");
    const windowKey = new URLSearchParams(location.hash.slice(1)).get("window");
    const SVG = "http://www.w3.org/2000/svg";
    const STATUS = AGENT_STATUS;
    const PHASES = { setup: "Preparação", agents: "Agentes", authors: "Autoria", consolidation: "Consolidação", sources: "Fontes", tables: "Tabelas", reviews: "Revisão", "rubber-duck": "Auditoria", gate: "Gate", delivery: "Entrega", done: "Encerramento" };
    const ROLES = { coordinator: "COORDENAÇÃO", author: "AUTORES", reviewer: "REVISORES", "rubber-duck": "RUBBER DUCK" };
    const ICONS = {
        coordinator: ["M12 2 21 7v10l-9 5-9-5V7Z", "M12 2v20M3 7l9 5 9-5"],
        author: ["m4 16 11-11 4 4L8 20H4Z", "m13 7 4 4M4 20h16"],
        reviewer: ["M10 17a7 7 0 1 0 0-14 7 7 0 0 0 0 14Z", "m15 15 6 6M7 10l2 2 4-4"],
        "rubber-duck": ["M12 2 21 6v6c0 5-4 8-9 10-5-2-9-5-9-10V6Z", "m7 12 3 3 7-7"],
    };
    const segmenter = typeof Intl.Segmenter === "function" ? new Intl.Segmenter("pt-BR", { granularity: "grapheme" }) : null;
    let liveState;
    let shownState;
    let currentId;
    let selectedId = null;
    let selectedCycle = null;
    let follow = true;
    let connected = false;
    let selection = null;
    let streamController;
    let stopped = false;
    let previousFocus;
    let seenEdges = new Set();
    let graphView = "";
    let historyItems = [];
    let failure = "";
    let activeTab = "flow";
    let detailOrigin = "flow";
    let cycleOptionsSignature = "";
    let detailSelectionKey = "";
    let resizeFrame;
    let windowFailure = "";
    let fittingWindow = false;

    function element(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = String(text);
        if (className) node.className = className;
        return node;
    }
    function svg(tag, attributes = {}, text) {
        const node = document.createElementNS(SVG, tag);
        for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
        if (text !== undefined) node.textContent = String(text);
        return node;
    }
    const short = (value, length = 25) => {
        const text = String(value ?? "");
        const characters = segmenter ? [...segmenter.segment(text)].map(item => item.segment) : [...text];
        return characters.length > length ? `${characters.slice(0, length - 1).join("")}…` : text;
    };
    function date(value) {
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? "Não registrado" : parsed.toLocaleString("pt-BR");
    }
    function validate(data) {
        if (!data || data.schema_version !== 1 || !Number.isSafeInteger(data.cycle)
            || !Array.isArray(data.agents) || !Array.isArray(data.dispatches) || !Array.isArray(data.events)
            || !data.evidence || !Array.isArray(data.evidence.cycles) || !Array.isArray(data.evidence.artifacts)
            || !Array.isArray(data.evidence.grade_scale) || !data.evidence.grade_scale.includes("A")) {
            throw new Error("Dados do monitor incompatíveis. Reabra pela skill.");
        }
        return data;
    }
    async function api(route, parameters = {}) {
        const url = new URL(`api/${route}`, location.href);
        for (const [key, value] of Object.entries(parameters)) if (value !== null && value !== undefined) url.searchParams.set(key, value);
        const response = await fetch(url, { headers: { Authorization: `Bearer ${token ?? ""}` }, cache: "no-store" });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error ?? `HTTP ${response.status}`);
        return data;
    }
    function alertState() {
        const warnings = [failure, windowFailure, shownState?.reader_error, ...(shownState ? executorWarnings(shownState) : []), ...(shownState?.evidence.warnings ?? [])].filter(Boolean);
        if (shownState && !connected && selectedId === currentId) warnings.unshift("Conexão interrompida. Os dados exibidos podem estar desatualizados.");
        $("alert").hidden = !warnings.length;
        $("alert").textContent = warnings.join(" ");
        const historical = shownState && archived(shownState, selectedCycle);
        $("connection").textContent = historical ? "Registro · histórico" : connected ? "Conectado · local" : "Desconectado";
        $("connection").className = `connection ${historical ? "" : connected ? "live" : "disconnected"}`;
    }
    function activeCycle() {
        return shownState.evidence.cycles.find(cycle => cycle.cycle === selectedCycle);
    }
    function activateTab(name, focus = false) {
        if (!["flow", "scores", "history", "details"].includes(name)) return;
        activeTab = name;
        $("context-menu").open = false;
        for (const tab of document.querySelectorAll("[role=tab]")) {
            const selected = tab.dataset.tab === name;
            tab.setAttribute("aria-selected", String(selected));
            tab.tabIndex = selected ? 0 : -1;
            $(tab.getAttribute("aria-controls")).hidden = !selected;
        }
        if (focus) $(`tab-${name}`).focus();
        if (name === "flow" && shownState) renderGraph();
    }
    async function fitWindow(mode) {
        if (!windowKey || fittingWindow) return;
        fittingWindow = true;
        $("dashboard").dataset.windowFit = "pending";
        $("expand").disabled = true;
        $("expand").setAttribute("aria-busy", "true");
        try {
            const response = await fetch(new URL("api/window", location.href), {
                method: "POST",
                headers: { Authorization: `Bearer ${token ?? ""}`, "Content-Type": "application/json" },
                body: JSON.stringify({ mode, window_key: windowKey }),
            });
            const result = await response.json();
            if (!response.ok || result.fitted !== true) throw new Error(result.error ?? "Dimensão da janela não confirmada.");
            windowFailure = "";
            $("dashboard").dataset.windowFit = "fitted";
        } catch (error) {
            windowFailure = `Não foi possível ajustar a janela: ${error.message} Você pode redimensioná-la manualmente.`;
            $("dashboard").dataset.windowFit = "failed";
        } finally {
            fittingWindow = false;
            $("expand").disabled = false;
            $("expand").removeAttribute("aria-busy");
            alertState();
        }
    }
    function dispatchFor(id) {
        return shownState.dispatches.filter(item => item.agent_id === id && item.cycle === selectedCycle).at(-1);
    }
    function visibleAgentStatus(agent) {
        return agentStatus(shownState, agent, selectedCycle, connected);
    }
    function setCycleChoices() {
        const numbers = [...new Set([shownState.cycle, ...shownState.evidence.cycles.map(item => item.cycle), ...shownState.dispatches.map(item => item.cycle)])].sort((a, b) => a - b);
        const choices = numbers.map(number => [number, number ? `Rodada ${number}${number === shownState.cycle ? " · atual" : " · histórico"}` : "Preparação"]);
        const signature = JSON.stringify(choices);
        if (signature !== cycleOptionsSignature) {
            $("cycle").replaceChildren(...choices.map(([number, label]) => {
                const option = element("option", label);
                option.value = number;
                return option;
            }));
            cycleOptionsSignature = signature;
        }
        if (!numbers.includes(selectedCycle)) selectedCycle = shownState.cycle;
        $("cycle").value = selectedCycle;
    }
    async function loadHistory() {
        historyItems = await api("history");
        $("execution").replaceChildren(...historyItems.map(item => {
            const option = element("option", `${item.execution_id === currentId ? "Atual · " : ""}${date(item.created_at)} · ${item.execution_id.slice(0, 8)}`);
            option.value = item.execution_id;
            return option;
        }));
        $("execution").value = selectedId;
    }
    function render(data) {
        shownState = validate(data);
        if (follow) selectedCycle = data.cycle;
        setCycleChoices();
        const cycle = activeCycle();
        $("title").textContent = data.title;
        $("title").title = data.title;
        $("execution-label").textContent = `${data.evidence.demo ? "DEMONSTRAÇÃO · " : ""}${data.swarm_id} · ${data.execution_id.slice(0, 8)}`;
        $("execution-label").title = $("execution-label").textContent;
        $("subtitle").textContent = archived(data, selectedCycle)
            ? "Registro histórico. Estes estados não representam a atividade atual da sessão."
            : "Eventos observados, evidências registradas e decisões do portão.";
        $("agent-count").textContent = data.agents.filter(agent => !agent.declaration_missing).length;
        $("agent-note").textContent = data.agents.length ? "Papéis declarados nesta execução" : "Aguardando criação dos agentes";
        $("running-count").textContent = data.agents.filter(agent => visibleAgentStatus(agent) === "running").length;
        $("cycle-count").textContent = selectedCycle ? `${selectedCycle} / ${data.evidence.max_cycles}` : "Pré-ciclo";
        $("run-status").textContent = runStatusText(data);
        const stall = healthLabel(data, selectedCycle, connected);
        $("session-status").textContent = stall ? stall.title : sessionLabel(data, selectedCycle, connected);
        $("session-status").className = `session-state${stall ? ` ${stall.state}` : sessionWorking(data, selectedCycle, connected) ? " processing" : ""}`;
        $("session-status").title = stall ? stall.detail : archived(data, selectedCycle)
            ? "O registro encerrado não informa se a sessão iniciou outro trabalho."
            : data.executor
                ? "O que o executor determinístico registrou no journal e no batimento do processo; não é a atividade do agente principal."
                : "Atividade do agente principal observada no runtime, separada da disponibilidade dos subagentes.";
        const caption = gradeCaption(cycle, data.evidence);
        $("grade-label").textContent = caption.label;
        $("grade-card").title = caption.title;
        $("grade-count").textContent = gradeCount(cycle, data.evidence);
        for (const id of ["agent-count", "running-count", "cycle-count", "grade-count"]) $(id).title = $(id).textContent;
        $("phases").replaceChildren(...Object.entries(PHASES).map(([key, label]) => element("span", label, `phase${data.phase === key ? " active" : ""}`)));
        if (activeTab !== "flow") seenEdges = new Set(data.edges.map(edge => edge.id));
        renderGraph();
        renderScores(cycle);
        renderDetails();
        renderTimeline();
        alertState();
        $("footer").textContent = `Última observação: ${date(data.updated_at)}. ${archived(data, selectedCycle) ? "Registro histórico, não atividade atual." : "Disponível = sem execução neste momento; não implica ação sua."}`;
        $("footer").title = $("footer").textContent;
    }

    function renderGraph() {
        if (!shownState || activeTab !== "flow") return;
        const graph = $("graph");
        const focused = document.activeElement?.dataset?.agent;
        const compact = $("dashboard").clientWidth < 1000;
        const layout = compact
            ? { width: 704, left: 14, column: 174, top: 28, row: 70, cardWidth: 154, cardHeight: 58, labelY: 16, margin: 34 }
            : { width: 1080, left: 26, column: 268, top: 48, row: 128, cardWidth: 222, cardHeight: 103, labelY: 27, margin: 75 };
        graph.classList.toggle("compact-graph", compact);
        const columns = Object.keys(ROLES);
        const roster = shownState.agents.filter(agent => !agent.declaration_missing || dispatchFor(agent.id));
        const groups = columns.map(kind => roster.filter(agent => (dispatchFor(agent.id)?.agent_kind ?? agent.kind) === kind));
        const rows = Math.max(1, ...groups.map(group => group.length));
        const height = Math.max(compact ? 180 : 300, rows * layout.row + layout.margin);
        const positions = new Map();
        const labels = [];
        graph.setAttribute("viewBox", `0 0 ${layout.width} ${height}`);
        const label = (attributes, value, maxWidth) => {
            const node = svg("text", attributes, short(value, 256));
            labels.push({ node, maxWidth });
            return node;
        };
        const children = [];
        const defs = svg("defs");
        const marker = svg("marker", { id: "arrow", markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: "auto", markerUnits: "userSpaceOnUse" });
        marker.append(svg("path", { d: "M0 0 8 4 0 8Z", fill: "currentColor" }));
        defs.append(marker);
        children.push(defs);
        groups.forEach((agents, column) => {
            const x = layout.left + column * layout.column;
            children.push(svg("text", { x, y: layout.labelY, class: "lane-label" }, ROLES[columns[column]]));
            agents.forEach((agent, index) => {
                const y = layout.top + index * layout.row + (rows - agents.length) * layout.row / 2;
                positions.set(agent.id, { x, y, right: x + layout.cardWidth, center: y + layout.cardHeight / 2 });
            });
        });
        const curve = (from, to) => {
            const a = positions.get(from);
            const b = positions.get(to);
            if (!a || !b) return null;
            if (a.x === b.x) return `M${a.right} ${a.center} C${a.right + 24} ${a.center},${b.right + 24} ${b.center},${b.right} ${b.center}`;
            const forward = b.x > a.x;
            const x1 = forward ? a.right : a.x;
            const x2 = forward ? b.x : b.right;
            const offset = (x2 - x1) * .5;
            return `M${x1} ${a.center} C${x1 + offset} ${a.center},${x2 - offset} ${b.center},${x2} ${b.center}`;
        };
        const planned = [];
        for (let column = 0; column < groups.length - 1; column++) {
            for (const from of groups[column]) for (const to of groups[column + 1]) planned.push([from.id, to.id]);
        }
        for (const [from, to] of planned) children.push(svg("path", { d: curve(from, to), class: "dependency" }));
        const edges = shownState.edges.filter(edge => edge.cycle === selectedCycle);
        for (const edge of edges) {
            const d = curve(edge.from, edge.to);
            if (!d) continue;
            const fresh = graphView === `${shownState.execution_id}/${selectedCycle}` && !seenEdges.has(edge.id)
                && selectedId === currentId && connected && shownState.connection === "connected";
            const line = svg("path", { d, class: `handoff${fresh ? " fresh" : ""}`, "marker-end": "url(#arrow)" });
            line.append(svg("title", {}, `${edge.from} → ${edge.to}: ${edge.label}`));
            children.push(line);
        }
        seenEdges = new Set(shownState.edges.map(edge => edge.id));
        graphView = `${shownState.execution_id}/${selectedCycle}`;
        for (const agent of shownState.agents) {
            const position = positions.get(agent.id);
            if (!position) continue;
            const dispatch = dispatchFor(agent.id);
            const status = visibleAgentStatus(agent);
            const group = svg("g", {
                transform: `translate(${position.x} ${position.y})`, role: "button", tabindex: 0,
                "aria-label": `${agent.id}: ${STATUS[status]}`, "data-agent": agent.id,
                class: `agent ${status}${selection?.type === "agent" && selection.id === agent.id ? " selected" : ""}`,
            });
            group.append(svg("rect", { width: layout.cardWidth, height: layout.cardHeight, rx: compact ? 8 : 12, class: "card" }));
            const icon = svg("g", { transform: compact ? "translate(8 6) scale(.7)" : "translate(13 13)", class: "role-icon" });
            for (const d of ICONS[agent.kind] ?? ICONS.author) icon.append(svg("path", { d }));
            const titleX = compact ? 31 : 45;
            group.append(icon, label({ x: titleX, y: compact ? 19 : 29, class: "agent-title" }, agent.id, layout.cardWidth - titleX - 8));
            const role = dispatch?.role ?? agent.role;
            group.append(label({ x: compact ? 9 : 14, y: compact ? 35 : 52, class: "agent-role" }, role, layout.cardWidth - (compact ? 18 : 28)));
            if (!compact) group.append(label({ x: 14, y: 68, class: "agent-role" }, dispatch?.observed_model ?? dispatch?.declared_model ?? agent.declared_model, layout.cardWidth - 28));
            group.append(svg("circle", { cx: compact ? 12 : 18, cy: compact ? 48 : 87, r: compact ? 3 : 3.5, class: "status-dot" }));
            const stateX = compact ? 20 : 28;
            group.append(label({ x: stateX, y: compact ? 52 : 91, class: "agent-state" }, agentStateText(dispatch, status, Date.now()), layout.cardWidth - stateX - 8));
            group.append(svg("title", {}, `${agent.id}\n${role}\n${agentStateText(dispatch, status, Date.now())}`));
            children.push(group);
        }
        if (!shownState.agents.length) children.push(svg("text", { x: 20, y: 100, class: "agent-role" }, "Aguardando a criação dos agentes."));
        graph.replaceChildren(...children);
        for (const { node, maxWidth } of labels) {
            if (node.getComputedTextLength() <= maxWidth) continue;
            const characters = segmenter ? [...segmenter.segment(node.textContent)].map(item => item.segment) : [...node.textContent];
            let low = 0;
            let high = characters.length;
            while (low < high) {
                const middle = Math.ceil((low + high) / 2);
                node.textContent = `${characters.slice(0, middle).join("")}…`;
                if (node.getComputedTextLength() <= maxWidth) low = middle;
                else high = middle - 1;
            }
            node.textContent = `${characters.slice(0, low).join("")}…`;
        }
        if (focused) [...graph.querySelectorAll("[data-agent]")].find(node => node.dataset.agent === focused)?.focus();
    }

    function grade(value, cycle) {
        const view = gradeView(value, cycle, shownState.evidence);
        return element("span", view.text, `grade ${view.kind}`);
    }
    function renderScores(cycle) {
        const scroll = { top: $("score-container").scrollTop, left: $("score-container").scrollLeft };
        const focusedTopic = document.activeElement?.dataset?.topic;
        const gate = cycle?.gate;
        let label = "Gate não registrado";
        let kind = "";
        if (gate?.status === "verified") {
            label = ({ approved: "Gate: aprovado", rejected: "Gate: reprovado", escalate: "Gate: escalar" })[gate.outcome] ?? "Gate inválido";
            kind = gate.outcome === "approved" ? "good" : "bad";
        } else if (gate?.status === "stale") { label = "Gate desatualizado"; kind = "warn"; }
        else if (gate?.status === "invalid") { label = "Gate inválido"; kind = "bad"; }
        if (cycle?.consistent === false) { label = "Evidências inconsistentes"; kind = "bad"; }
        $("gate-badge").textContent = label;
        $("gate-badge").title = label;
        $("gate-badge").className = `badge ${kind}`;
        $("gate-summary").textContent = label;
        const counts = shownState.evidence.sources.counts;
        const sourceStatus = shownState.evidence.sources.status;
        const checks = [
            `Fontes (última checagem): ${sourceStatus === "pending" ? "aguardando" : sourceStatus === "invalid" ? "dados inválidos" : sourceStatus === "stale" ? "desatualizadas" : `${counts.ok + counts.redirect} válidas · ${counts.warn} avisos · ${counts.fail} falhas`}`,
            `Tabelas: ${!cycle || cycle.tables.status === "pending" ? "aguardando" : `${cycle.tables.failures} falhas`}`,
            approvalLegend(cycle, shownState.evidence),
        ];
        $("checks").replaceChildren(...checks.map(value => element("span", value)));
        $("score-note").textContent = cycle?.individual_reviews === "not_recorded"
            ? "Notas individuais não registradas; a avaliação consolidada não permite reconstruí-las."
            : "A nota efetiva é o mínimo, nunca a média. Células sem registro não são estimadas.";
        const issues = [...(cycle?.issues ?? []), ...(gate?.error ? [gate.error] : []),
            ...(gate?.blocked ?? []).map(item => `${item.kind}: ${item.name}${item.grade ? ` (${item.grade})` : ""}`)];
        $("issues").replaceChildren(...issues.map(issue => element("p", issue)));
        if (!cycle || (!cycle.topics.length && !cycle.reviews.length)) {
            $("score-container").replaceChildren(element("p", "Aguardando avaliações desta rodada. Notas de rodadas anteriores não são reaproveitadas como aprovação.", "empty"));
            return;
        }
        const reviewers = [...new Set([...shownState.agents.filter(item => item.kind === "reviewer").map(item => item.id), ...cycle.reviews.map(item => item.reviewer)])];
        const topics = new Map(cycle.topics.map(topic => [topic.id, topic]));
        for (const row of cycle.reviews) if (!topics.has(row.topic)) topics.set(row.topic, { id: row.topic, title: row.topic });
        const table = element("table");
        const head = element("thead");
        const heading = element("tr");
        for (const name of ["Tópico", ...reviewers, "Efetiva"]) heading.append(element("th", name));
        head.append(heading);
        const body = element("tbody");
        for (const topic of topics.values()) {
            const row = element("tr");
            const label = element("td");
            const button = element("button", topic.title);
            button.type = "button";
            button.dataset.topic = topic.id;
            label.append(button);
            row.append(label);
            for (const reviewer of reviewers) {
                const cell = element("td");
                const entry = cycle.reviews.find(item => item.topic === topic.id && item.reviewer === reviewer);
                cell.append(grade(entry?.grade, cycle));
                cell.title = entry ? entry.justification : "Avaliação individual não registrada";
                row.append(cell);
            }
            const effective = element("td");
            effective.append(grade(topic.grade, cycle));
            effective.title = topic.reviewer ? `Mínimo registrado por ${topic.reviewer}` : "Consolidação pendente";
            row.append(effective);
            body.append(row);
        }
        table.append(head, body);
        $("score-container").replaceChildren(table);
        $("score-container").scrollTop = scroll.top;
        $("score-container").scrollLeft = scroll.left;
        if (focusedTopic) [...$("score-container").querySelectorAll("[data-topic]")].find(node => node.dataset.topic === focusedTopic)?.focus({ preventScroll: true });
    }

    function artifactButtons(container, paths) {
        for (const artifact of shownState.evidence.artifacts.filter(item => paths(item))) {
            const button = element("button", artifact.path, "artifact-link");
            button.type = "button";
            button.dataset.artifact = artifact.id;
            container.append(button);
        }
    }
    function renderDetails() {
        const container = $("details");
        const key = `${selectedId}/${selectedCycle}/${selection?.type}/${selection?.id}`;
        const scroll = detailSelectionKey === key ? container.scrollTop : 0;
        const focusedArtifact = document.activeElement?.dataset?.artifact;
        detailSelectionKey = key;
        container.replaceChildren();
        if (selection?.type === "agent") {
            const agent = shownState.agents.find(item => item.id === selection.id);
            if (!agent) { selection = null; return renderDetails(); }
            const dispatch = dispatchFor(agent.id);
            const principal = !dispatch && agent.kind === "coordinator";
            container.append(element("h3", agent.id), element("p", dispatch?.role ?? agent.role));
            const list = element("dl");
            for (const [label, value] of [
                ["Estado", agentStateText(dispatch, visibleAgentStatus(agent), Date.now())], ["Rodada", selectedCycle || "Pré-ciclo"],
                ["Modelo declarado", dispatch?.declared_model ?? agent.declared_model],
                ["Modelo observado", dispatch?.observed_model ?? "Não informado pelo runtime"],
                ["Origem do modelo", dispatch?.model_source === "executor_journal" ? "Registro do executor"
                    : dispatch?.model_source === "first_dispatched" ? "Primeiro modelo despachado" : dispatch?.observed_model ? "Seleção do runtime" : "Não registrada"],
                ["Tarefa", principal ? "Agente principal da sessão" : dispatch?.executor_task ?? dispatch?.task_id ?? "Não correlacionada"],
                ["Última observação", principal && shownState.session_activity?.observed_at ? date(shownState.session_activity.observed_at) : dispatch?.observed_at ? date(dispatch.observed_at) : "Não registrada"],
                ["Disponibilidade", STATUS[dispatch?.task_status] ?? "Não registrada"],
                ...(dispatch?.source === "executor" ? [
                    ["Etapa", `${dispatch.stage}${dispatch.round ? ` · rodada de reparo ${dispatch.round}` : ""}`],
                    ["Tentativa", dispatch.attempt],
                    ["Resultado", OUTCOME_LABELS[dispatch.outcome] ?? "Ainda sem resultado"],
                    ["Motivo", dispatch.error ? `${dispatch.error}${dispatch.cause ? ` (${dispatch.cause})` : ""}` : "Não registrado"],
                ] : []),
            ]) list.append(element("dt", label), element("dd", value));
            if (dispatch?.status === "idle" || dispatch?.task_status === "idle") {
                container.append(element("p", "Disponível para outro turno. Esse estado, sozinho, não indica que você precise responder."));
            }
            container.append(list, element("h4", "Declaração"));
            artifactButtons(container, artifact => artifact.path === agent.path);
        } else if (selection?.type === "topic") {
            const cycle = activeCycle();
            const topic = cycle?.topics.find(item => item.id === selection.id);
            container.append(element("h3", topic?.title ?? selection.id));
            const reviews = cycle?.reviews.filter(item => item.topic === selection.id) ?? [];
            if (!reviews.length) container.append(element("p", "As avaliações individuais deste tópico não foram registradas."));
            for (const review of reviews) {
                container.append(element("h4", `${review.reviewer} · ${review.grade}`), element("p", review.justification));
                if (review.action) container.append(element("p", `Correção: ${review.action}`));
            }
        } else {
            container.append(element("p", "Selecione um agente ou tópico para ver evidências. A interface não coleta prompts ou raciocínios internos.", "empty"));
        }
        container.append(element("h4", "Artefatos da rodada"));
        const prefix = `cycle-${String(selectedCycle).padStart(2, "0")}-`;
        artifactButtons(container, artifact => artifact.name.startsWith(prefix) || artifact.name === "final-report.md");
        container.append(element("h4", "Saídas do documento"));
        artifactButtons(container, artifact => artifact.path.startsWith("output/"));
        container.scrollTop = scroll;
        if (focusedArtifact) [...container.querySelectorAll("[data-artifact]")].find(node => node.dataset.artifact === focusedArtifact)?.focus({ preventScroll: true });
    }

    function renderTimeline() {
        const scroll = $("timeline").scrollTop;
        const events = shownState.events.filter(event => (event.cycle ?? event.data.cycle ?? 0) === selectedCycle).slice(-100).reverse();
        $("timeline").replaceChildren(...events.map(event => {
            const item = element("li");
            const time = element("time", new Date(event.at).toLocaleTimeString("pt-BR"));
            time.title = date(event.at);
            const source = element("span", ({ runtime: "RUNTIME", coordinator: "COORDENADOR", artifacts: "ARTEFATOS", observer: "OBSERVADOR", executor: "EXECUTOR" })[event.source] ?? "REGISTRO", "event-source");
            const data = event.data;
            const dispatch = shownState.dispatches.find(value => value.id === data.dispatch_id || value.id === data.id);
            const byExecutor = event.source === "executor";
            const description = event.type === "phase" ? `Fase: ${PHASES[data.phase] ?? data.phase}`
                : event.type === "dispatch" ? (byExecutor ? `${data.agent_id}: pedido emitido pelo executor (tentativa ${data.attempt})` : `${data.agent_id}: despacho registrado; aguardando observação`)
                : event.type === "binding" ? `${dispatch?.agent_id ?? "Agente"}: chamada correlacionada`
                : event.type === "runtime" ? `${dispatch?.agent_id ?? "Agente"}: ${STATUS[data.status] ?? "metadados atualizados"}${byExecutor && data.outcome ? ` · ${OUTCOME_LABELS[data.outcome] ?? data.outcome}` : ""}${byExecutor && data.error ? ` · ${data.error}` : ""}`
                : event.type === "executor" ? data.label
                : event.type === "handoff" ? `${data.from} → ${data.to}: ${data.label}`
                : event.type === "evidence" ? `Artefatos atualizados: ${data.agents} agentes, ${data.cycles} rodadas`
                : event.type === "finish" ? (byExecutor ? `Execução encerrada pelo executor: ${data.status}` : `Encerramento solicitado: ${data.status}`)
                : event.type === "session" ? `Sessão: ${{ processing: "processando", waiting: "principal entre etapas", idle: "sem tarefas em curso", completion_declared: "conclusão declarada" }[data.status] ?? data.status}`
                : event.type === "connection" ? `Conexão: ${data.connection}`
                : `Evento: ${event.type}`;
            item.append(time, source, element("p", description));
            return item;
        }));
        if (!events.length) $("timeline").append(element("li", "Nenhum evento registrado nesta rodada.", "muted"));
        $("timeline").scrollTop = scroll;
    }

    async function selectExecution(id) {
        selectedId = id;
        follow = id === currentId;
        selectedCycle = null;
        selection = null;
        seenEdges = new Set();
        render(id === currentId ? liveState : await api("state", { execution: id }));
    }
    async function openArtifact(id, opener) {
        previousFocus = opener;
        $("artifact-title").textContent = "Carregando artefato";
        $("artifact-text").textContent = "";
        $("artifact-view").hidden = false;
        $("artifact-close").focus();
        try {
            const artifact = await api("artifact", { id, execution: selectedId });
            $("artifact-title").textContent = artifact.path;
            $("artifact-text").textContent = artifact.text;
        } catch (error) {
            $("artifact-text").textContent = error.message;
        }
    }
    function closeArtifact() {
        $("artifact-view").hidden = true;
        if (previousFocus?.isConnected) previousFocus.focus();
        else [...$("details").querySelectorAll("[data-artifact]")].find(node => node.dataset.artifact === previousFocus?.dataset?.artifact)?.focus();
    }
    async function stream() {
        streamController?.abort();
        const controller = new AbortController();
        streamController = controller;
        let retry = 1000;
        while (!stopped && !controller.signal.aborted) {
            try {
                const response = await fetch(new URL("api/events", location.href), {
                    headers: { Authorization: `Bearer ${token ?? ""}` }, signal: controller.signal, cache: "no-store",
                });
                if (!response.ok) throw new Error(`Não foi possível conectar ao fluxo: HTTP ${response.status}`);
                connected = true;
                failure = "";
                retry = 1000;
                alertState();
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = "";
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) throw new Error("Fluxo do monitor encerrado.");
                    buffer += decoder.decode(value, { stream: true });
                    let boundary;
                    while ((boundary = buffer.indexOf("\n\n")) >= 0) {
                        const block = buffer.slice(0, boundary);
                        buffer = buffer.slice(boundary + 2);
                        const line = block.split("\n").find(item => item.startsWith("data: "));
                        if (!line) continue;
                        const state = validate(JSON.parse(line.slice(6)));
                        if (liveState && state.sequence < liveState.sequence) continue;
                        liveState = state;
                        if (selectedId === currentId) render(state);
                    }
                }
            } catch (error) {
                if (controller.signal.aborted) return;
                connected = false;
                failure = error.message;
                if (shownState) render(shownState); else alertState();
                await new Promise(resolve => setTimeout(resolve, retry));
                retry = Math.min(retry * 2, 15000);
            }
        }
    }

    $("graph").addEventListener("click", event => {
        const agent = event.target.closest("[data-agent]");
        if (agent) {
            selection = { type: "agent", id: agent.dataset.agent };
            detailOrigin = "flow";
            $("details-back").textContent = "Voltar ao fluxo";
            renderGraph();
            renderDetails();
            activateTab("details", true);
        }
    });
    $("graph").addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); event.target.dispatchEvent(new MouseEvent("click", { bubbles: true })); }
    });
    $("score-container").addEventListener("click", event => {
        const topic = event.target.closest("[data-topic]");
        if (topic) {
            selection = { type: "topic", id: topic.dataset.topic };
            detailOrigin = "scores";
            $("details-back").textContent = "Voltar às notas";
            renderDetails();
            activateTab("details", true);
        }
    });
    $("details").addEventListener("click", event => {
        const artifact = event.target.closest("[data-artifact]");
        if (artifact) openArtifact(artifact.dataset.artifact, artifact);
    });
    $("execution").addEventListener("change", () => selectExecution($("execution").value).then(() => { $("context-menu").open = false; }).catch(error => { failure = error.message; alertState(); }));
    $("cycle").addEventListener("change", () => { selectedCycle = Number($("cycle").value); follow = false; selection = null; $("context-menu").open = false; render(shownState); });
    $("follow").addEventListener("click", () => { follow = true; selectedId = currentId; selectedCycle = liveState.cycle; $("execution").value = currentId; $("context-menu").open = false; render(liveState); });
    $("zoom").addEventListener("input", () => { $("graph").style.width = `${$("zoom").value}%`; });
    $("artifact-close").addEventListener("click", closeArtifact);
    $("details-back").addEventListener("click", () => {
        activateTab(detailOrigin, true);
        const selector = detailOrigin === "flow" ? "[data-agent]" : "[data-topic]";
        const field = detailOrigin === "flow" ? "agent" : "topic";
        [...document.querySelectorAll(selector)].find(node => node.dataset[field] === selection?.id)?.focus();
    });
    for (const tab of document.querySelectorAll("[role=tab]")) {
        tab.addEventListener("click", () => activateTab(tab.dataset.tab));
        tab.addEventListener("keydown", event => {
            const tabs = [...document.querySelectorAll("[role=tab]")];
            let index = tabs.indexOf(tab);
            if (event.key === "ArrowRight") index = (index + 1) % tabs.length;
            else if (event.key === "ArrowLeft") index = (index + tabs.length - 1) % tabs.length;
            else if (event.key === "Home") index = 0;
            else if (event.key === "End") index = tabs.length - 1;
            else return;
            event.preventDefault();
            activateTab(tabs[index].dataset.tab, true);
        });
    }
    $("expand").addEventListener("click", () => {
        const expanded = $("dashboard").classList.toggle("expanded");
        $("expand").textContent = expanded ? "Compactar" : "Expandir";
        $("expand").setAttribute("aria-pressed", String(expanded));
        $("expand").title = expanded ? "Voltar ao painel compacto" : "Usar toda a área disponível";
        if (shownState) renderGraph();
        fitWindow(expanded ? "expanded" : "compact");
    });
    document.addEventListener("pointerdown", event => {
        if ($("context-menu").open && !$("context-menu").contains(event.target)) $("context-menu").open = false;
    });
    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && $("context-menu").open) {
            $("context-menu").open = false;
            $("context-menu").querySelector("summary").focus();
        }
    });
    new ResizeObserver(() => {
        cancelAnimationFrame(resizeFrame);
        resizeFrame = requestAnimationFrame(() => { if (shownState) renderGraph(); });
    }).observe($("dashboard"));
    $("artifact-view").addEventListener("keydown", event => {
        if (event.key === "Escape") closeArtifact();
        if (event.key === "Tab") {
            event.preventDefault();
            (document.activeElement === $("artifact-close") ? $("artifact-text") : $("artifact-close")).focus();
        }
    });
    $("reconnect").addEventListener("click", async () => {
        try {
            $("context-menu").open = false;
            liveState = validate(await api("state"));
            await loadHistory();
            await selectExecution(selectedId ?? liveState.execution_id);
            stream();
        } catch (error) { failure = error.message; alertState(); }
    });
    // The clocks of the agents that are running move between the states the server pushes.
    const clock = setInterval(() => {
        if (stopped || !shownState || document.hidden) return;
        if (!shownState.dispatches.some(item => item.source === "executor" && ["queued", "running"].includes(item.status))) return;
        renderGraph();
        if (activeTab === "details" && selection?.type === "agent") renderDetails();
    }, 5000);
    window.addEventListener("beforeunload", () => { stopped = true; clearInterval(clock); streamController?.abort(); });

    $("dashboard").dataset.windowFit = windowKey ? "pending" : "host-managed";
    fitWindow("compact");
    try {
        liveState = validate(await api("state"));
        currentId = liveState.execution_id;
        selectedId = currentId;
        render(liveState);
        await loadHistory();
        stream();
    } catch (error) {
        failure = error.message;
        alertState();
    }
}

if (typeof document !== "undefined") {
    startInterface().catch(error => {
        console.error("Monitor UI:", error);
        const alert = document.getElementById("alert");
        if (alert) {
            alert.hidden = false;
            alert.textContent = `Não foi possível iniciar a interface: ${error.message}`;
        }
    });
}

import { createServer } from "node:http";
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { history, inside, readHistorical } from "./state.mjs";

const ASSETS = new Map([
    ["/", ["index.html", "text/html; charset=utf-8"]],
    ["/app.mjs", ["app.mjs", "text/javascript; charset=utf-8"]],
    ["/style.css", ["style.css", "text/css; charset=utf-8"]],
]);
const UI = fileURLToPath(new URL("./ui/", import.meta.url));

export async function readArtifact(root, snapshot, id) {
    const artifact = snapshot.evidence.artifacts.find(item => item.id === id);
    if (!artifact) throw Object.assign(new Error("Artefato não autorizado."), { status: 404 });
    const requested = path.resolve(root, artifact.path);
    const real = await fs.realpath(requested);
    if (!inside(root, real)) throw Object.assign(new Error("Artefato fora do swarm."), { status: 403 });
    const file = await fs.open(real, "r");
    let raw;
    try {
        const buffer = Buffer.alloc(2 * 1024 * 1024 + 1);
        let length = 0;
        while (length < buffer.length) {
            const { bytesRead } = await file.read(buffer, length, buffer.length - length, null);
            if (!bytesRead) break;
            length += bytesRead;
        }
        if (length > 2 * 1024 * 1024) throw Object.assign(new Error("Artefato excede o limite de leitura."), { status: 413 });
        raw = buffer.subarray(0, length);
    } finally {
        await file.close();
    }
    if (createHash("sha256").update(raw).digest("hex") !== artifact.sha256) {
        throw Object.assign(new Error("Artefato alterado desde esta observação. Atualize os dados ou consulte a execução atual."), { status: 409 });
    }
    return { name: artifact.name, path: artifact.path, text: raw.toString("utf8") };
}

export async function createMonitorServer(store, { log = () => {} } = {}) {
    const token = randomBytes(32).toString("hex");
    const peers = new Set();
    const waiters = new Set();
    let origin;
    let allowOpaqueOrigin = false;
    let closed = false;
    let windowController = null;
    let windowKey = null;

    function json(response, status, value) {
        response.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
        response.end(JSON.stringify(value));
    }

    function authorized(request) {
        const match = /^Bearer ([a-f0-9]{64})$/.exec(request.headers.authorization ?? "");
        return match !== null && timingSafeEqual(Buffer.from(match[1]), Buffer.from(token));
    }

    function signalClient() {
        for (const resolve of waiters) resolve(true);
        waiters.clear();
    }

    async function snapshotFor(url) {
        const id = url.searchParams.get("execution");
        if (!id || id === store.state.execution_id) return store.publicState;
        const item = await readHistorical(store.root, id);
        if (item.swarm_id !== store.state.swarm_id) throw new Error("Historical execution belongs to a different swarm");
        return item;
    }

    async function route(request, response) {
        response.setHeader("Cache-Control", "no-store");
        response.setHeader("X-Content-Type-Options", "nosniff");
        response.setHeader("Referrer-Policy", "no-referrer");
        response.setHeader("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'");
        const host = new URL(origin).host;
        if (request.headers.host !== host) return json(response, 403, { error: "Host não autorizado." });
        const requestOrigin = request.headers.origin;
        if (requestOrigin && requestOrigin !== origin && !(allowOpaqueOrigin && requestOrigin === "null")) {
            return json(response, 403, { error: "Origem não autorizada." });
        }
        if (requestOrigin) {
            response.setHeader("Access-Control-Allow-Origin", requestOrigin);
            response.setHeader("Vary", "Origin");
        }
        const url = new URL(request.url, origin);
        if (request.method === "OPTIONS") {
            response.setHeader("Access-Control-Allow-Methods", url.pathname === "/api/window" ? "POST" : "GET");
            response.setHeader("Access-Control-Allow-Headers", "Authorization, Content-Type");
            response.writeHead(204);
            return response.end();
        }
        if (url.pathname === "/api/window" && request.method === "POST") {
            if (!authorized(request)) return json(response, 401, { error: "Acesso ao monitor não autorizado." });
            if (!windowController) return json(response, 409, { error: "Este host não permite ajustar a janela pelo monitor." });
            if (request.headers["content-type"]?.split(";")[0] !== "application/json") {
                return json(response, 415, { error: "JSON obrigatório." });
            }
            const chunks = [];
            let bytes = 0;
            for await (const chunk of request) {
                bytes += chunk.length;
                if (bytes > 256) return json(response, 413, { error: "Solicitação de janela excede o limite." });
                chunks.push(chunk);
            }
            const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
            if (!body || typeof body !== "object" || Array.isArray(body)
                || !["compact", "expanded"].includes(body.mode)
                || Object.keys(body).some(key => !["mode", "window_key"].includes(key))) {
                return json(response, 400, { error: "Somente os modos compacto e expandido são permitidos." });
            }
            if (typeof body.window_key !== "string" || !/^[a-f0-9]{32}$/.test(body.window_key)
                || !timingSafeEqual(Buffer.from(body.window_key), Buffer.from(windowKey))) {
                return json(response, 403, { error: "Janela não autorizada." });
            }
            return json(response, 200, await windowController.resize(body.mode));
        }
        if (request.method !== "GET") return json(response, 405, { error: "Interface somente de leitura." });
        if (url.pathname === "/favicon.ico") {
            response.writeHead(204);
            return response.end();
        }
        if (ASSETS.has(url.pathname)) {
            const [file, contentType] = ASSETS.get(url.pathname);
            response.writeHead(200, { "Content-Type": contentType });
            return response.end(await fs.readFile(path.join(UI, file)));
        }
        if (!url.pathname.startsWith("/api/")) return json(response, 404, { error: "Recurso não encontrado." });
        if (!authorized(request)) return json(response, 401, { error: "Acesso ao monitor não autorizado. Reabra pela skill." });
        if (url.pathname === "/api/state") {
            const snapshot = await snapshotFor(url);
            return json(response, 200, snapshot);
        }
        if (url.pathname === "/api/history") {
            const items = await history(store.root);
            return json(response, 200, items.map(({ session_id, ...item }) => item));
        }
        if (url.pathname === "/api/artifact") {
            return json(response, 200, await readArtifact(store.root, await snapshotFor(url), url.searchParams.get("id")));
        }
        if (url.pathname === "/api/events") {
            if (peers.size >= 12) return json(response, 429, { error: "Limite de conexões atingido." });
            response.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8", Connection: "keep-alive" });
            response.write(`event: state\ndata: ${JSON.stringify(store.publicState)}\n\n`);
            peers.add(response);
            signalClient();
            const keepalive = setInterval(() => response.write(": connected\n\n"), 15000);
            request.on("close", () => { clearInterval(keepalive); peers.delete(response); });
            return;
        }
        return json(response, 404, { error: "Recurso não encontrado." });
    }

    const server = createServer((request, response) => {
        route(request, response).catch(error => {
            log(`Monitor HTTP: ${error.message}`);
            if (!response.headersSent) json(response, error.status ?? (error.code === "ENOENT" ? 404 : 400), { error: error.message });
            else response.destroy();
        });
    });
    server.requestTimeout = 20000;
    server.headersTimeout = 10000;
    await new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(0, "127.0.0.1", resolve);
    });
    origin = `http://127.0.0.1:${server.address().port}`;
    function publish() {
        const message = `event: state\ndata: ${JSON.stringify(store.publicState)}\n\n`;
        for (const peer of peers) {
            if (peer.writableLength > 2 * 1024 * 1024) {
                peer.destroy();
                peers.delete(peer);
            } else peer.write(message);
        }
    }
    store.on("change", publish);
    store.on("reader", publish);
    return {
        url: `${origin}/#token=${token}`,
        origin,
        attachWindow(controller) {
            windowController = controller;
            windowKey ??= randomBytes(16).toString("hex");
            return `${origin}/#token=${token}&window=${windowKey}`;
        },
        enableCanvas() { allowOpaqueOrigin = true; },
        get connected() { return peers.size > 0; },
        waitForClient(timeout = 8000) {
            if (peers.size > 0) return Promise.resolve(true);
            return new Promise(resolve => {
                const finish = value => { clearTimeout(timer); waiters.delete(finish); resolve(value); };
                const timer = setTimeout(() => finish(false), timeout);
                waiters.add(finish);
            });
        },
        async close() {
            if (closed) return;
            closed = true;
            for (const resolve of waiters) resolve(false);
            waiters.clear();
            store.off("change", publish);
            store.off("reader", publish);
            for (const peer of peers) peer.end();
            peers.clear();
            await new Promise(resolve => {
                server.close(resolve);
                server.closeAllConnections();
            });
        },
    };
}

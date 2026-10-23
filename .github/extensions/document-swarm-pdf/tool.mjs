import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execute = promisify(execFile);
const directory = path.dirname(await fs.realpath(fileURLToPath(import.meta.url)));
const repository = path.resolve(directory, "..", "..", "..");

export const parameters = {
    type: "object", additionalProperties: false, required: ["action", "source", "destination"],
    properties: {
        action: { type: "string", enum: ["render", "inspect"], description: "Renderizar ou inspecionar, sem escrever ou reformular conteúdo." },
        source: { type: "string", description: "Caminho absoluto do documento Markdown autoral." },
        destination: { type: "string", description: "Pasta nova para render; bundle existente para inspect. Nunca sobrescreve uma entrega existente no render." },
        profile: { type: "string", enum: ["textbook", "technical-report"] },
        language: { type: "string", enum: ["pt-BR", "pt-PT", "en-US", "en-GB", "es-ES"] },
    },
};

export async function pdfAction(args, { run = execute, root = repository } = {}) {
    if (!args || !["render", "inspect"].includes(args.action)) throw new Error("Ação PDF inválida.");
    for (const field of ["source", "destination"]) {
        if (typeof args[field] !== "string" || !path.isAbsolute(args[field]) || args[field].includes("\0")) {
            throw new Error(`${field} deve ser um caminho absoluto explícito.`);
        }
    }
    if (args.profile !== undefined && !["textbook", "technical-report"].includes(args.profile)) throw new Error("Perfil PDF inválido.");
    if (args.language !== undefined && !["pt-BR", "pt-PT", "en-US", "en-GB", "es-ES"].includes(args.language)) throw new Error("Idioma PDF inválido.");
    const venv = path.join(root, ".venv-pdf", process.platform === "win32" ? "Scripts" : "bin", process.platform === "win32" ? "python.exe" : "python");
    const candidates = [];
    try { if ((await fs.stat(venv)).isFile()) candidates.push(venv); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
    candidates.push("python3", "python");
    let interpreter;
    for (const candidate of candidates) {
        try {
            await run(candidate, ["-X", "utf8", "-c", "import reportlab, markdown_it, pypdf, pypdfium2, PIL"], { cwd: root, timeout: 15000, windowsHide: true, encoding: "utf8" });
            interpreter = candidate;
            break;
        } catch (error) {
            if (error.code !== "ENOENT" && typeof error.code !== "number") throw error;
        }
    }
    if (!interpreter) throw new Error("Dependências PDF indisponíveis. Instale requirements-pdf.txt opcionalmente, de preferência em .venv-pdf; os checks stdlib continuam independentes.");
    const command = ["-X", "utf8", "-m", "scripts.pdf", args.action, "--source", args.source, "--destination", args.destination];
    if (args.profile) command.push("--profile", args.profile);
    if (args.language) command.push("--language", args.language);
    try {
        const { stdout } = await run(interpreter, command, {
            cwd: root, timeout: 180000, maxBuffer: 4 * 1024 * 1024, windowsHide: true, encoding: "utf8",
            env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
        });
        const result = JSON.parse(stdout);
        if (result.status !== "pass") throw new Error("O motor não confirmou inspeção mecânica aprovada.");
        return result;
    } catch (error) {
        if (error.code === 1 && error.stdout) {
            const result = JSON.parse(error.stdout);
            return { ...result, status: "fail" };
        }
        throw new Error(`Falha no motor PDF: ${error.stderr?.trim() || error.message}`);
    }
}

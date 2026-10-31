import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execute = promisify(execFile);
const helper = fileURLToPath(new URL("./window.ps1", import.meta.url));

export function canvasWindowTitle(title, executionId) {
    if (typeof executionId !== "string" || !/^[a-zA-Z0-9_-]{1,100}$/.test(executionId)) {
        throw new Error("Invalid canvas execution identity");
    }
    const budget = Math.min(135, 240 - executionId.length - 12);
    let label = "";
    for (const character of String(title).replace(/[\u0000-\u001f\u007f]/g, " ")) {
        if (label.length + character.length > budget) break;
        label += character;
    }
    return `Swarm · ${label} [${executionId}]`;
}

export function createCanvasWindow({ title, ownerPid, platform = process.platform, run = execute }) {
    if (platform !== "win32" || !Number.isSafeInteger(ownerPid) || ownerPid < 1 || ownerPid > 2147483647) return null;
    let queue = Promise.resolve();
    return {
        resize(mode) {
            if (!["compact", "expanded"].includes(mode)) return Promise.reject(new Error("Unsupported window mode"));
            const action = queue.then(async () => {
                const { stdout } = await run("powershell.exe", [
                    "-NoLogo", "-NoProfile", "-NonInteractive", "-File", helper,
                    "-OwnerPid", String(ownerPid), "-WindowTitle", title, "-Mode", mode,
                ], { encoding: "utf8", timeout: 18000, windowsHide: true });
                const result = JSON.parse(stdout);
                if (result.fitted !== true || result.mode !== mode || result.owner_pid !== ownerPid
                    || !(result.client_width > 0) || !(result.client_height > 0)) {
                    throw new Error("Native canvas size could not be verified");
                }
                return result;
            });
            // The returned action reports failures; a later user retry must still run.
            queue = action.catch(() => {});
            return action;
        },
    };
}

import { promises as fs } from "node:fs";
import path from "node:path";

async function exists(file) {
    try { await fs.stat(file); return true; }
    catch (error) { if (error.code === "ENOENT") return false; throw error; }
}

export async function yieldsToProject(launchPath, workingDirectory) {
    if (!launchPath) return false;
    const extensions = path.dirname(path.dirname(path.resolve(launchPath)));
    if (path.basename(extensions) !== "extensions" || path.basename(path.dirname(extensions)) === ".github") return false;
    const name = path.basename(path.dirname(launchPath));
    let root = path.resolve(workingDirectory);
    let cursor = root;
    while (true) {
        if (await exists(path.join(cursor, ".git"))) { root = cursor; break; }
        const parent = path.dirname(cursor);
        if (parent === cursor) break;
        cursor = parent;
    }
    // Source-qualified providers can coexist even when their global tool names cannot.
    return exists(path.join(root, ".github", "extensions", name, "extension.mjs"));
}

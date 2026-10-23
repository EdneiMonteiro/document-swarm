import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { pdfAction } from "../tool.mjs";

const source = path.resolve("source.md");
const destination = path.resolve("new-pdf-bundle");

test("PDF tool delegates explicit paths without a shell or a content prompt", async () => {
    const calls = [];
    const result = await pdfAction({ action: "render", source, destination, profile: "textbook", language: "pt-BR" }, {
        run: async (program, args, options) => {
            calls.push({ program, args, options });
            return { stdout: args.includes("-c") ? "" : '{"status":"pass","editorial_approval":"not_evaluated","page_count":3}' };
        },
    });
    assert.equal(result.editorial_approval, "not_evaluated");
    const command = calls.at(-1);
    assert.ok(command.args.includes("scripts.pdf"));
    assert.equal(command.args[command.args.indexOf("--source") + 1], source);
    assert.equal(command.options.shell, undefined);
});

test("PDF inspection failure is not converted to success", async () => {
    const result = await pdfAction({ action: "inspect", source, destination }, {
        run: async (_program, args) => {
            if (args.includes("-c")) return { stdout: "" };
            throw Object.assign(new Error("failed inspection"), { code: 1, stdout: '{"status":"fail","errors":[{"code":"invisible_text"}]}' });
        },
    });
    assert.equal(result.status, "fail");
    assert.equal(result.errors[0].code, "invisible_text");
});

test("unsupported inputs and absent optional libraries fail explicitly", async () => {
    await assert.rejects(pdfAction({ action: "render", source: "relative.md", destination }), /absoluto/);
    await assert.rejects(pdfAction({ action: "render", source, destination, profile: "unknown" }), /Perfil/);
    await assert.rejects(pdfAction({ action: "render", source, destination }, {
        run: async () => { throw Object.assign(new Error("not installed"), { code: 1 }); },
    }), /Dependências PDF/);
});

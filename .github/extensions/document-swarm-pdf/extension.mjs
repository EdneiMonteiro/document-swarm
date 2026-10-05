import { joinSession } from "@github/copilot-sdk/extension";
import { yieldsToProject } from "../document-swarm-monitor/ownership.mjs";
import { parameters, pdfAction } from "./tool.mjs";

const active = !await yieldsToProject(process.env.EXTENSION_PATH, process.cwd());
await joinSession({
    tools: active ? [{
        name: "docswarm_pdf",
        description: "Render or inspect professional PDFs from authored Markdown using optional local Python dependencies. Profiles: textbook and technical-report. Outputs PDF, PNG previews, hash manifest and mechanical inspection JSON. Never writes/rephrases source content or gives editorial grades. Use a new destination for each render, inspect before human/agent editorial and visual review, and bind approval to the resulting hashes.",
        parameters,
        handler: async args => {
            try {
                const result = await pdfAction(args);
                return { resultType: result.status === "pass" ? "success" : "failure", textResultForLlm: JSON.stringify(result) };
            } catch (error) {
                return { resultType: "failure", textResultForLlm: error.message };
            }
        },
    }] : [],
});

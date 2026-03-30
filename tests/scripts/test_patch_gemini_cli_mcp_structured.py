from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_patch_gemini_cli_mcp_structured_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "mcp-tool.js"
    target.write_text(
        """function transformResourceLinkBlock(block) {
    return {
        text: `Resource Link: ${block.title || block.name} at ${block.uri}`,
    };
}
/**
 * Transforms the raw MCP content blocks from the SDK response into a
 * standard GenAI Part array.
 * @param sdkResponse The raw Part[] array from `mcpTool.callTool()`.
 * @returns A clean Part[] array ready for the scheduler.
 */
function transformMcpContentToParts(sdkResponse) {
    const funcResponse = sdkResponse?.[0]?.functionResponse;
    // eslint-disable-next-line @typescript-eslint/no-unsafe-type-assertion
    const mcpContent = funcResponse?.response?.['content'];
    const toolName = funcResponse?.name || 'unknown tool';
    if (!Array.isArray(mcpContent)) {
        return [{ text: '[Error: Could not parse tool response]' }];
    }
    const transformed = mcpContent.flatMap((block) => {
        switch (block.type) {
            case 'text':
                return transformTextBlock(block);
            case 'image':
            case 'audio':
                return transformImageAudioBlock(block, toolName);
            case 'resource':
                return transformResourceBlock(block, toolName);
            case 'resource_link':
                return transformResourceLinkBlock(block);
            default:
                return null;
        }
    });
    return transformed.filter((part) => part !== null);
}
/**
 * Processes the raw response from the MCP tool to generate a clean,
 * human-readable string for display in the CLI. It summarizes non-text
 * content and presents text directly.
 *
 * @param rawResponse The raw Part[] array from the GenAI SDK.
 * @returns A formatted string representing the tool's output.
 */
function getStringifiedResultForDisplay(rawResponse) {
    // eslint-disable-next-line @typescript-eslint/no-unsafe-type-assertion
    const mcpContent = rawResponse?.[0]?.functionResponse?.response?.['content'];
    if (!Array.isArray(mcpContent)) {
        return '```json\\n' + JSON.stringify(rawResponse, null, 2) + '\\n```';
    }
    const displayParts = mcpContent.map((block) => {
        switch (block.type) {
            case 'text':
                return transformTextBlock(block);
        }
    });
    return displayParts.join('\\n');
}
""",
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(ROOT / "scripts" / "patch_gemini_cli_mcp_structured.py"),
        "--target-file",
        str(target),
    ]
    first = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
    assert first.returncode == 0
    patched = target.read_text(encoding="utf-8")
    assert "GEMINI_CLI_PREFER_MCP_STRUCTURED_CONTENT" in patched
    assert "functionResponse" in patched

    second = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
    assert second.returncode == 0
    assert target.read_text(encoding="utf-8") == patched

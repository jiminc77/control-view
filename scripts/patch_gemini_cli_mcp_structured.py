#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PATCH_MARKER = "function getStructuredMcpResponse(sdkResponse) {"
BACKUP_SUFFIX = ".codex-orig"

HELPER_INSERT = """function getStructuredMcpResponse(sdkResponse) {
    const response = sdkResponse?.[0]?.functionResponse?.response;
    if (!response || typeof response !== 'object') {
        return undefined;
    }
    return response['structuredContent'];
}
function shouldPreferStructuredMcpResponse(sdkResponse) {
    if (process.env['GEMINI_CLI_PREFER_MCP_STRUCTURED_CONTENT'] === 'true') {
        return true;
    }
    const mcpContent = sdkResponse?.[0]?.functionResponse?.response?.['content'];
    if (!Array.isArray(mcpContent)) {
        return getStructuredMcpResponse(sdkResponse) !== undefined;
    }
    return mcpContent.length === 0 && getStructuredMcpResponse(sdkResponse) !== undefined;
}
"""

ORIGINAL_TRANSFORM = """function transformMcpContentToParts(sdkResponse) {
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
}"""

PATCHED_TRANSFORM = """function transformMcpContentToParts(sdkResponse) {
    const funcResponse = sdkResponse?.[0]?.functionResponse;
    // eslint-disable-next-line @typescript-eslint/no-unsafe-type-assertion
    const mcpContent = funcResponse?.response?.['content'];
    const toolName = funcResponse?.name || 'unknown tool';
    const structuredContent = getStructuredMcpResponse(sdkResponse);
    if (shouldPreferStructuredMcpResponse(sdkResponse) &&
        structuredContent !== undefined) {
        return [
            {
                functionResponse: {
                    name: toolName,
                    response: structuredContent,
                },
            },
        ];
    }
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
}"""

ORIGINAL_DISPLAY = """function getStringifiedResultForDisplay(rawResponse) {
    // eslint-disable-next-line @typescript-eslint/no-unsafe-type-assertion
    const mcpContent = rawResponse?.[0]?.functionResponse?.response?.['content'];
    if (!Array.isArray(mcpContent)) {
        return '```json\\n' + JSON.stringify(rawResponse, null, 2) + '\\n```';
    }
    const displayParts = mcpContent.map((block) => {
        switch (block.type) {
            case 'text':"""

PATCHED_DISPLAY = """function getStringifiedResultForDisplay(rawResponse) {
    // eslint-disable-next-line @typescript-eslint/no-unsafe-type-assertion
    const mcpContent = rawResponse?.[0]?.functionResponse?.response?.['content'];
    const structuredContent = getStructuredMcpResponse(rawResponse);
    if (structuredContent !== undefined &&
        (!Array.isArray(mcpContent) || mcpContent.length === 0)) {
        return '```json\\n' + JSON.stringify(structuredContent, null, 2) + '\\n```';
    }
    if (!Array.isArray(mcpContent)) {
        return '```json\\n' + JSON.stringify(rawResponse, null, 2) + '\\n```';
    }
    const displayParts = mcpContent.map((block) => {
        switch (block.type) {
            case 'text':"""


def _default_target() -> Path:
    gemini_path = shutil.which("gemini")
    if not gemini_path:
        raise FileNotFoundError("could not find `gemini` on PATH")
    resolved = Path(gemini_path).resolve()
    if resolved.parent.name == "dist" and resolved.parent.parent.name == "gemini-cli":
        gemini_cli_root = resolved.parent.parent
    else:
        gemini_cli_root = (
            resolved.parent.parent
            / "lib"
            / "node_modules"
            / "@google"
            / "gemini-cli"
        )
    return (
        gemini_cli_root
        / "node_modules"
        / "@google"
        / "gemini-cli-core"
        / "dist"
        / "src"
        / "tools"
        / "mcp-tool.js"
    )


def _apply_patch_text(source: str) -> str:
    if PATCH_MARKER in source:
        return source
    anchor = "function transformMcpContentToParts(sdkResponse) {"
    insert_at = source.find(anchor)
    if insert_at == -1:
        raise ValueError("failed to find transformMcpContentToParts in target file")
    source = source[:insert_at] + HELPER_INSERT + source[insert_at:]
    if ORIGINAL_TRANSFORM not in source:
        raise ValueError("failed to locate original transformMcpContentToParts block")
    source = source.replace(ORIGINAL_TRANSFORM, PATCHED_TRANSFORM, 1)
    if ORIGINAL_DISPLAY not in source:
        raise ValueError("failed to locate original getStringifiedResultForDisplay block")
    source = source.replace(ORIGINAL_DISPLAY, PATCHED_DISPLAY, 1)
    return source


def _restore_backup(target: Path) -> int:
    backup = target.with_name(target.name + BACKUP_SUFFIX)
    if not backup.exists():
        print(f"backup not found: {backup}", file=sys.stderr)
        return 1
    target.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"restored {target} from {backup}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-file", type=Path, default=None)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--restore", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = (args.target_file or _default_target()).resolve()
    if args.restore:
        return _restore_backup(target)
    if not target.exists():
        print(f"target file not found: {target}", file=sys.stderr)
        return 1
    source = target.read_text(encoding="utf-8")
    patched = _apply_patch_text(source)
    if args.check:
        print("patched" if patched != source or PATCH_MARKER in source else "not_patched")
        return 0
    if patched == source:
        print(f"already patched: {target}")
        return 0
    backup = target.with_name(target.name + BACKUP_SUFFIX)
    if not backup.exists():
        backup.write_text(source, encoding="utf-8")
    target.write_text(patched, encoding="utf-8")
    print(f"patched {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

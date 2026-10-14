/**
 * Kobits VS Code Extension — Autonomous Multi-Agent Software Engineer.
 * Connects directly to Kobits Extension Backend (/api/v1/extension).
 */

import * as vscode from 'vscode';

let statusBarItem: vscode.StatusBarItem;
let activeMissionId: string | null = null;

export async function activate(context: vscode.ExtensionContext) {
    // 1. Initialize persistent status bar item
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.command = 'kobits.showStatus';
    context.subscriptions.push(statusBarItem);
    updateStatusBar('idle', 'Kobits: Ready');
    statusBarItem.show();

    // 2. Register Commands
    context.subscriptions.push(
        vscode.commands.registerCommand('kobits.login', () => promptLogin(context)),
        vscode.commands.registerCommand('kobits.dispatchPrompt', () => promptDispatch(context)),
        vscode.commands.registerCommand('kobits.dispatchSelection', () => promptSelectionDispatch(context)),
        vscode.commands.registerCommand('kobits.viewDiff', (missionId?: string) => viewMissionDiff(context, missionId)),
        vscode.commands.registerCommand('kobits.applyDiff', (missionId?: string) => applyMissionDiff(context, missionId)),
        vscode.commands.registerCommand('kobits.showStatus', () => showSwarmStatus(context))
    );

    // Initial background health check
    checkSwarmStatus(context).catch(() => {});
}

export function deactivate() {
    if (statusBarItem) {
        statusBarItem.dispose();
    }
}

// ── HELPERS & CLIENT ──────────────────────────────────────────────────

function getApiUrl(): string {
    const config = vscode.workspace.getConfiguration('kobits');
    return (config.get<string>('apiUrl') || 'http://127.0.0.1:8000').replace(/\/+$/, '');
}

async function getApiKey(context: vscode.ExtensionContext): Promise<string | undefined> {
    return await context.secrets.get('kobits.apiKey');
}

async function fetchFromKobits(context: vscode.ExtensionContext, path: string, options: RequestInit = {}): Promise<any> {
    const apiKey = await getApiKey(context);
    const baseUrl = getApiUrl();
    const url = `${baseUrl}${path}`;

    const headers: Record<string, string> = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        ...(options.headers as Record<string, string> || {}),
    };

    if (apiKey) {
        headers['X-API-Key'] = apiKey;
        headers['Authorization'] = `Bearer ${apiKey}`;
    }

    const response = await fetch(url, { ...options, headers });
    if (response.status === 401) {
        promptLogin(context, 'Authentication required. Please enter a valid Kobits API Key.');
        throw new Error('Unauthorized');
    }
    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Kobits API error (${response.status}): ${errorText}`);
    }
    return await response.json();
}

function updateStatusBar(state: 'idle' | 'running' | 'completed' | 'failed', text: string, tooltip?: string) {
    if (!statusBarItem) return;
    if (state === 'running') {
        statusBarItem.text = `$(sync~spin) ${text}`;
        statusBarItem.backgroundColor = undefined;
    } else if (state === 'completed') {
        statusBarItem.text = `$(check) ${text}`;
        statusBarItem.backgroundColor = undefined;
    } else if (state === 'failed') {
        statusBarItem.text = `$(error) ${text}`;
        statusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');
    } else {
        statusBarItem.text = `$(sparkle) ${text}`;
        statusBarItem.backgroundColor = undefined;
    }
    if (tooltip) {
        statusBarItem.tooltip = tooltip;
    }
}

// ── COMMAND IMPLEMENTATIONS ───────────────────────────────────────────

async function promptLogin(context: vscode.ExtensionContext, customMessage?: string) {
    const apiKey = await vscode.window.showInputBox({
        title: 'Kobits Developer Authentication',
        prompt: customMessage || 'Enter your Cryptographic API Key (starts with kb_live_)',
        password: true,
        ignoreFocusOut: true,
        placeHolder: 'kb_live_...',
        validateInput: (text) => {
            if (!text || !text.trim().startsWith('kb_live_')) {
                return 'Invalid API key format. Expected prefix "kb_live_".';
            }
            return null;
        }
    });

    if (!apiKey) return;

    try {
        await context.secrets.store('kobits.apiKey', apiKey.trim());
        const status = await checkSwarmStatus(context);
        vscode.window.showInformationMessage(
            `✓ Connected to Kobits as ${status.user?.full_name} (${status.organization?.name})`
        );
    } catch (err: any) {
        vscode.window.showErrorMessage(`Login failed: ${err.message}`);
    }
}

async function checkSwarmStatus(context: vscode.ExtensionContext): Promise<any> {
    try {
        const status = await fetchFromKobits(context, '/api/v1/extension/status');
        const orgName = status.organization?.name || 'Workspace';
        const credits = status.organization?.credit_balance ?? 0;
        const activeCount = (status.active_missions || []).length;

        if (activeCount > 0) {
            const first = status.active_missions[0];
            activeMissionId = first.id;
            updateStatusBar(
                'running',
                `Kobits: [${first.phase || 'ACTIVE'}] ${Math.round((first.progress || 0) * 100)}%`,
                `Workspace: ${orgName} | Balance: ${credits} credits | Active: ${first.title}`
            );
        } else {
            updateStatusBar(
                'idle',
                `Kobits: Ready`,
                `Workspace: ${orgName} | Balance: ${credits} credits | Swarm Idle`
            );
        }
        return status;
    } catch (e) {
        updateStatusBar('idle', 'Kobits: Disconnected', 'Click to authenticate with Kobits API Key');
        throw e;
    }
}

async function showSwarmStatus(context: vscode.ExtensionContext) {
    const apiKey = await getApiKey(context);
    if (!apiKey) {
        return promptLogin(context);
    }
    try {
        const status = await checkSwarmStatus(context);
        const org = status.organization || {};
        const user = status.user || {};
        const action = await vscode.window.showInformationMessage(
            `Kobits Swarm: Connected as ${user.full_name} in ${org.name} (${org.credit_balance} credits remaining).`,
            'New Mission',
            'View Active Diff',
            'Change API Key'
        );

        if (action === 'New Mission') {
            vscode.commands.executeCommand('kobits.dispatchPrompt');
        } else if (action === 'View Active Diff' && activeMissionId) {
            viewMissionDiff(context, activeMissionId);
        } else if (action === 'Change API Key') {
            promptLogin(context);
        }
    } catch (err: any) {
        vscode.window.showErrorMessage(`Failed to connect to Kobits: ${err.message}`);
    }
}

async function promptDispatch(context: vscode.ExtensionContext) {
    const prompt = await vscode.window.showInputBox({
        title: 'Kobits Autonomous Swarm',
        prompt: 'Describe the engineering feature, bugfix, or refactoring outcome you want Kobits to execute:',
        placeHolder: 'e.g. Implement rate limiting on API endpoints and add comprehensive pytest suite',
        ignoreFocusOut: true,
    });

    if (!prompt || !prompt.trim()) return;

    // Resolve active editor and workspace context
    const editor = vscode.window.activeTextEditor;
    let filePath: string | undefined;
    let branch: string | undefined;

    if (editor) {
        filePath = vscode.workspace.asRelativePath(editor.document.uri);
    }

    try {
        const result = await fetchFromKobits(context, '/api/v1/extension/dispatch', {
            method: 'POST',
            body: JSON.stringify({
                prompt: prompt.trim(),
                file_path: filePath,
                branch: branch,
                execution_mode: 'autopilot',
            })
        });

        activeMissionId = result.mission_id;
        monitorMissionProgress(context, result.mission_id, result.title || prompt.trim());
    } catch (err: any) {
        vscode.window.showErrorMessage(`Failed to launch mission: ${err.message}`);
    }
}

async function promptSelectionDispatch(context: vscode.ExtensionContext) {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.selection.isEmpty) {
        vscode.window.showWarningMessage('Please select a block of code to fix or refactor.');
        return;
    }

    const selection = editor.selection;
    const selectedText = editor.document.getText(selection);
    const rangeText = `L${selection.start.line + 1}-L${selection.end.line + 1}`;
    const filePath = vscode.workspace.asRelativePath(editor.document.uri);

    const prompt = await vscode.window.showInputBox({
        title: `Kobits Inline Refactor (${rangeText})`,
        prompt: `What should the autonomous swarm do with this code in ${filePath}?`,
        placeHolder: 'e.g. Optimize algorithm, handle null checks, convert to async',
        ignoreFocusOut: true,
    });

    if (!prompt || !prompt.trim()) return;

    try {
        const result = await fetchFromKobits(context, '/api/v1/extension/dispatch', {
            method: 'POST',
            body: JSON.stringify({
                prompt: prompt.trim(),
                file_path: filePath,
                selection_range: rangeText,
                code_context: selectedText,
                execution_mode: 'autopilot',
            })
        });

        activeMissionId = result.mission_id;
        monitorMissionProgress(context, result.mission_id, `Inline: ${prompt.trim()}`);
    } catch (err: any) {
        vscode.window.showErrorMessage(`Failed to dispatch inline refactor: ${err.message}`);
    }
}

async function monitorMissionProgress(context: vscode.ExtensionContext, missionId: string, title: string) {
    vscode.window.withProgress(
        {
            location: vscode.ProgressLocation.Notification,
            title: `Kobits: ${title.slice(0, 40)}...`,
            cancellable: false,
        },
        async (progress) => {
            let completed = false;
            let lastProgress = 0;

            for (let i = 0; i < 60; i++) {
                await new Promise((r) => setTimeout(r, 3000));
                try {
                    const status = await fetchFromKobits(context, `/api/v1/extension/missions/${missionId}/status`);
                    const progVal = Math.round((status.progress || 0) * 100);
                    const phase = status.phase || 'ACTIVE';
                    const stage = status.current_stage || '';

                    const delta = Math.max(0, progVal - lastProgress);
                    progress.report({
                        increment: delta,
                        message: `[${phase}] ${stage || status.status}`,
                    });
                    lastProgress = progVal;

                    updateStatusBar('running', `Kobits: [${phase}] ${progVal}%`);

                    if (['COMPLETED', 'FAILED', 'CANCELLED', 'AWAITING_APPROVAL'].includes(status.status)) {
                        completed = true;
                        if (status.status === 'COMPLETED') {
                            updateStatusBar('completed', `Kobits: Completed`);
                            const choice = await vscode.window.showInformationMessage(
                                `✓ Kobits completed mission: ${title}`,
                                'View Diff',
                                'Apply Solution'
                            );
                            if (choice === 'View Diff') {
                                viewMissionDiff(context, missionId);
                            } else if (choice === 'Apply Solution') {
                                applyMissionDiff(context, missionId);
                            }
                        } else {
                            updateStatusBar('failed', `Kobits: ${status.status}`);
                            vscode.window.showWarningMessage(`Mission ended with status: ${status.status}`);
                        }
                        break;
                    }
                } catch (_) {}
            }
        }
    );
}

async function viewMissionDiff(context: vscode.ExtensionContext, missionId?: string) {
    const targetId = missionId || activeMissionId;
    if (!targetId) {
        vscode.window.showWarningMessage('No active mission found to view diff.');
        return;
    }

    try {
        const diffData = await fetchFromKobits(context, `/api/v1/extension/missions/${targetId}/diff`);
        const doc = await vscode.workspace.openTextDocument({
            content: diffData.diff_text || '(No file changes generated)',
            language: 'diff',
        });
        await vscode.window.showTextDocument(doc, { preview: true });
    } catch (err: any) {
        vscode.window.showErrorMessage(`Failed to fetch mission diff: ${err.message}`);
    }
}

async function applyMissionDiff(context: vscode.ExtensionContext, missionId?: string) {
    const targetId = missionId || activeMissionId;
    if (!targetId) {
        vscode.window.showWarningMessage('No active mission found to apply.');
        return;
    }

    try {
        const result = await fetchFromKobits(context, `/api/v1/extension/missions/${targetId}/apply`, {
            method: 'POST',
            body: JSON.stringify({ dry_run: false }),
        });
        vscode.window.showInformationMessage(`✓ ${result.message || 'Solution applied successfully.'}`);
    } catch (err: any) {
        vscode.window.showErrorMessage(`Failed to apply diff: ${err.message}`);
    }
}

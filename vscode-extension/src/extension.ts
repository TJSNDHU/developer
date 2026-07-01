/**
 * AUREM CTO — VS Code extension (Iter 212m-174)
 *
 * Uses `sk-aurem-` API key stored in VS Code SecretStorage.  All ORA
 * calls route through the MCP server (JSON-RPC 2.0 at
 * `/api/aurem-dev/mcp`) so this extension shares the same auth story
 * as Cursor / Claude Desktop / Claude Code CLI.
 */
import * as vscode from 'vscode';

const SECRET_KEY = 'aurem.apiKey';
const MCP_PATH   = '/api/aurem-dev/mcp';

async function getApiKey(context: vscode.ExtensionContext): Promise<string | undefined> {
  return context.secrets.get(SECRET_KEY);
}

async function setApiKey(context: vscode.ExtensionContext, key: string | undefined) {
  if (key) {
    await context.secrets.store(SECRET_KEY, key);
  } else {
    await context.secrets.delete(SECRET_KEY);
  }
}

/**
 * Call an MCP tool via JSON-RPC 2.0.
 * Throws on transport / RPC errors so the caller can render a toast.
 */
async function callMcpTool(
  serverUrl: string,
  apiKey: string,
  toolName: string,
  args: Record<string, unknown>,
): Promise<unknown> {
  const url = `${serverUrl}${MCP_PATH}`;
  const body = {
    jsonrpc: '2.0',
    id: Date.now(),
    method: 'tools/call',
    params: { name: toolName, arguments: args },
  };
  const r = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type':  'application/json',
      'Accept':        'application/json, text/event-stream',
      'Authorization': `Bearer ${apiKey}`,
    },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const json = await r.json() as { result?: unknown; error?: { message?: string } };
  if (json.error) throw new Error(json.error.message || 'RPC error');
  return json.result;
}

export function activate(context: vscode.ExtensionContext) {
  const serverUrl = () => vscode.workspace.getConfiguration('aurem')
    .get<string>('serverUrl', 'https://auremcto.com');

  const bar = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right, 100);
  bar.command = 'aurem.openChat';
  bar.show();
  context.subscriptions.push(bar);

  const refreshBar = async () => {
    const key = await getApiKey(context);
    bar.text = key ? '$(rocket) ORA' : '$(plug) AUREM';
    bar.tooltip = key ? 'ORA connected via MCP' : 'Click to connect ORA';
  };
  refreshBar();

  context.subscriptions.push(
    vscode.commands.registerCommand('aurem.openChat', () => {
      vscode.commands.executeCommand('aurem.chatView.focus');
    }),

    /*
     * aurem.login — Prompt for an ORA API key.  The key is generated at
     * https://auremcto.com/integrations (or user's own preview URL).  A
     * "Get your key" button opens the browser to that page.
     */
    vscode.commands.registerCommand('aurem.login', async () => {
      const action = await vscode.window.showInformationMessage(
        'Paste your ORA API key (starts with sk-aurem-…). Get one at ' +
        `${serverUrl()}/integrations`,
        'Paste API Key', 'Open Integrations Page');
      if (action === 'Open Integrations Page') {
        vscode.env.openExternal(
          vscode.Uri.parse(`${serverUrl()}/integrations`));
        return;
      }
      if (action !== 'Paste API Key') return;

      const key = await vscode.window.showInputBox({
        prompt: 'Paste your ORA API key',
        placeHolder: 'sk-aurem-…',
        ignoreFocusOut: true,
        password: true,
        validateInput: v =>
          (!v || !v.startsWith('sk-aurem-'))
            ? 'Key must start with "sk-aurem-"'
            : undefined,
      });
      if (!key) return;

      // Verify the key by calling `initialize` on the MCP server.
      try {
        await callMcpTool(serverUrl(), key, /* dummy — replaced by direct RPC */ '', {});
      } catch {
        // The tool call will fail (empty name) — but the transport/auth check
        // is what we care about.  Try again via the manifest endpoint.
        try {
          const r = await fetch(`${serverUrl()}${MCP_PATH}`, {
            headers: { 'Authorization': `Bearer ${key}` },
          });
          if (!r.ok) {
            vscode.window.showErrorMessage(
              `AUREM: Key rejected (HTTP ${r.status}).`);
            return;
          }
        } catch (e) {
          vscode.window.showErrorMessage(
            `AUREM: Could not verify key — ${e instanceof Error ? e.message : String(e)}`);
          return;
        }
      }
      await setApiKey(context, key);
      await refreshBar();
      vscode.window.showInformationMessage('AUREM: Connected via MCP.');
    }),

    vscode.commands.registerCommand('aurem.logout', async () => {
      await setApiKey(context, undefined);
      await refreshBar();
      vscode.window.showInformationMessage('AUREM: Disconnected.');
    }),

    /*
     * aurem.shipSelection — Route through the `ship_code` MCP tool.
     * Requires the user to pick which project to ship to (calls
     * `list_projects` first).
     */
    vscode.commands.registerCommand('aurem.shipSelection', async () => {
      const apiKey = await getApiKey(context);
      if (!apiKey) {
        const a = await vscode.window.showWarningMessage(
          'AUREM: Not connected.', 'Connect Now');
        if (a === 'Connect Now') vscode.commands.executeCommand('aurem.login');
        return;
      }
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const code = editor.document.getText(editor.selection);
      const file = editor.document.fileName;
      const desc = await vscode.window.showInputBox({
        prompt: 'What should ORA do with this code?',
        placeHolder: 'e.g. add error handling, refactor, add tests',
        ignoreFocusOut: true,
      });
      if (!desc) return;

      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'AUREM: Sending to ORA…' },
        async () => {
          try {
            // 1) Pick a project via MCP list_projects
            const list = await callMcpTool(serverUrl(), apiKey, 'list_projects', {}) as {
              content?: { text?: string }[];
            };
            let projects: { project_id: string; name?: string; github_repo?: string }[] = [];
            try {
              const raw = list?.content?.[0]?.text || '{}';
              const parsed = JSON.parse(raw);
              projects = parsed.projects || [];
            } catch { /* ignore */ }
            if (projects.length === 0) {
              vscode.window.showErrorMessage(
                'AUREM: No projects found. Add a repo at ' +
                `${serverUrl()}/projects`);
              return;
            }
            const pick = await vscode.window.showQuickPick(
              projects.map(p => ({
                label: p.name || p.github_repo || p.project_id,
                description: p.project_id,
                p,
              })),
              { placeHolder: 'Which project should ORA ship to?' }
            );
            if (!pick) return;

            // 2) Ship the code via MCP ship_code
            const message = `${desc}\n\nContext from VS Code (${file.split('/').pop()}):\n\`\`\`\n${code.slice(0, 3000)}\n\`\`\``;
            const result = await callMcpTool(serverUrl(), apiKey, 'ship_code', {
              project_id: pick.p.project_id,
              message,
            });
            const summary = JSON.stringify(result).slice(0, 200);
            const action = await vscode.window.showInformationMessage(
              `ORA queued the change. ${summary}`, 'Open AUREM');
            if (action === 'Open AUREM') {
              vscode.env.openExternal(vscode.Uri.parse(`${serverUrl()}/dashboard`));
            }
          } catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            vscode.window.showErrorMessage(`AUREM: ${msg}`);
          }
        }
      );
    })
  );

  const provider: vscode.WebviewViewProvider = {
    async resolveWebviewView(view) {
      view.webview.options = { enableScripts: true };
      const apiKey = await getApiKey(context);
      view.webview.html = getSidebarHtml(serverUrl(), apiKey);
      view.webview.onDidReceiveMessage(async (msg: { type?: string }) => {
        if (msg.type === 'login')  vscode.commands.executeCommand('aurem.login');
        if (msg.type === 'logout') vscode.commands.executeCommand('aurem.logout');
      });
    },
  };
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider('aurem.chatView', provider));
}

function getSidebarHtml(serverUrl: string, apiKey: string | undefined): string {
  if (apiKey) {
    // Iframe the dashboard so the user sees the same chat as web.
    return `<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>body{margin:0;padding:0;height:100vh;display:flex}iframe{flex:1;border:none;width:100%}</style>
</head><body><iframe src="${serverUrl}/dashboard?vscode=1" allow="clipboard-write"></iframe></body></html>`;
  }
  return `<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
body{margin:0;padding:0;font-family:system-ui;background:var(--vscode-sideBar-background);color:var(--vscode-foreground);height:100vh;display:flex;flex-direction:column}
.connect{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:12px;padding:20px;text-align:center}
button{padding:8px 16px;border-radius:5px;border:none;cursor:pointer;font-size:12px;background:var(--vscode-button-background);color:var(--vscode-button-foreground)}
.note{font-size:10px;opacity:.6;margin-top:14px;line-height:1.4;max-width:220px}
</style></head><body>
<div class="connect">
  <div style="font-size:24px">◈</div>
  <div style="font-size:13px;font-weight:500">AUREM ORA</div>
  <p style="font-size:11px;opacity:.7">Paste your ORA API key to ship code from VS Code via MCP.</p>
  <button onclick="(function(){const v=acquireVsCodeApi();v.postMessage({type:'login'})})()">Paste API Key</button>
  <div class="note">
    Get your key at <br/>
    <code style="font-size:10px">${serverUrl}/integrations</code>
  </div>
</div>
</body></html>`;
}

export function deactivate() {}

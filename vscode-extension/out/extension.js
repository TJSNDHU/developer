"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
let token;
function activate(context) {
    token = context.globalState.get('aurem.token');
    const serverUrl = () => vscode.workspace.getConfiguration('aurem')
        .get('serverUrl', 'https://auremcto.com');
    const bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    bar.command = 'aurem.openChat';
    bar.text = token ? '$(rocket) ORA' : '$(plug) AUREM';
    bar.tooltip = token ? 'AUREM connected' : 'Click to connect AUREM';
    bar.show();
    context.subscriptions.push(bar);
    context.subscriptions.push(vscode.commands.registerCommand('aurem.openChat', () => {
        vscode.commands.executeCommand('aurem.chatView.focus');
    }), vscode.commands.registerCommand('aurem.login', async () => {
        const { createServer } = await Promise.resolve().then(() => __importStar(require('http')));
        const srv = createServer();
        await new Promise(r => srv.listen(0, 'localhost', () => r()));
        const addr = srv.address();
        const port = typeof addr === 'object' && addr ? addr.port : 0;
        const callbackUrl = `http://localhost:${port}/callback`;
        const loginUrl = `${serverUrl()}/api/auth/github/login?vscode_callback=${encodeURIComponent(callbackUrl)}`;
        const gotToken = new Promise((resolve, reject) => {
            srv.on('request', (req, res) => {
                const u = new URL(req.url || '/', `http://localhost:${port}`);
                const t = u.searchParams.get('token');
                res.writeHead(200, { 'Content-Type': 'text/html' });
                if (t) {
                    res.end('<html><body style="font-family:sans-serif;padding:2rem"><h2 style="color:#10b981">Connected to AUREM!</h2><p>Return to VS Code.</p><script>window.close()</script></body></html>');
                    srv.close();
                    resolve(t);
                }
                else {
                    res.end('<html><body>Login failed. Return to VS Code.</body></html>');
                    srv.close();
                    reject(new Error('login failed'));
                }
            });
        });
        await vscode.env.openExternal(vscode.Uri.parse(loginUrl));
        vscode.window.showInformationMessage('AUREM: Browser opened — authorize on GitHub');
        try {
            token = await Promise.race([
                gotToken,
                new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 120000)),
            ]);
            await context.globalState.update('aurem.token', token);
            bar.text = '$(rocket) ORA';
            bar.tooltip = 'AUREM connected';
            vscode.window.showInformationMessage('AUREM: Connected! ORA is ready.');
        }
        catch {
            vscode.window.showWarningMessage('AUREM: Login timed out or failed.');
        }
    }), vscode.commands.registerCommand('aurem.logout', async () => {
        token = undefined;
        await context.globalState.update('aurem.token', undefined);
        bar.text = '$(plug) AUREM';
        vscode.window.showInformationMessage('AUREM: Disconnected.');
    }), vscode.commands.registerCommand('aurem.shipSelection', async () => {
        if (!token) {
            const a = await vscode.window.showWarningMessage('AUREM: Not connected.', 'Connect Now');
            if (a === 'Connect Now')
                vscode.commands.executeCommand('aurem.login');
            return;
        }
        const editor = vscode.window.activeTextEditor;
        if (!editor)
            return;
        const code = editor.document.getText(editor.selection);
        const file = editor.document.fileName;
        const desc = await vscode.window.showInputBox({
            prompt: 'What should ORA do with this code?',
            placeHolder: 'e.g. add error handling, refactor, add tests',
        });
        if (!desc)
            return;
        await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: 'AUREM: Sending to ORA…' }, async () => {
            try {
                const r = await fetch(`${serverUrl()}/api/aurem-dev/cto/tasks/submit`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}`,
                    },
                    body: JSON.stringify({
                        description: `${desc}\n\nContext from VS Code (${file.split('/').pop()}):\n\`\`\`\n${code.slice(0, 3000)}\n\`\`\``,
                        source: 'vscode_extension',
                    }),
                });
                if (!r.ok)
                    throw new Error(`HTTP ${r.status}`);
                const result = await r.json();
                const action = await vscode.window.showInformationMessage(`ORA is working! Task ${result.task_id || ''}`, 'Open AUREM');
                if (action === 'Open AUREM') {
                    vscode.env.openExternal(vscode.Uri.parse(`${serverUrl()}/dashboard`));
                }
            }
            catch (e) {
                const msg = e instanceof Error ? e.message : String(e);
                vscode.window.showErrorMessage(`AUREM: ${msg}`);
            }
        });
    }));
    const provider = {
        resolveWebviewView(view) {
            view.webview.options = { enableScripts: true };
            view.webview.html = getSidebarHtml(serverUrl(), token);
            view.webview.onDidReceiveMessage(async (msg) => {
                if (msg.type === 'login')
                    vscode.commands.executeCommand('aurem.login');
            });
        },
    };
    context.subscriptions.push(vscode.window.registerWebviewViewProvider('aurem.chatView', provider));
}
function getSidebarHtml(serverUrl, tk) {
    if (tk) {
        return `<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>body{margin:0;padding:0;height:100vh;display:flex}iframe{flex:1;border:none;width:100%}</style>
</head><body><iframe src="${serverUrl}/dashboard?vscode=1&token=${tk}" allow="clipboard-write"></iframe></body></html>`;
    }
    return `<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
body{margin:0;padding:0;font-family:system-ui;background:var(--vscode-sideBar-background);color:var(--vscode-foreground);height:100vh;display:flex;flex-direction:column}
.connect{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:12px;padding:20px;text-align:center}
button{padding:8px 16px;border-radius:5px;border:none;cursor:pointer;font-size:12px;background:var(--vscode-button-background);color:var(--vscode-button-foreground)}
</style></head><body>
<div class="connect">
  <div style="font-size:24px">◈</div>
  <div style="font-size:13px;font-weight:500">AUREM ORA</div>
  <p style="font-size:11px;opacity:.7">Connect your GitHub account to ship code from VS Code</p>
  <button onclick="(function(){const v=acquireVsCodeApi();v.postMessage({type:'login'})})()">Connect GitHub</button>
</div>
</body></html>`;
}
function deactivate() { }
//# sourceMappingURL=extension.js.map
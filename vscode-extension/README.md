# AUREM CTO — VS Code Extension

ORA AI chat sidebar + "Ship via AUREM" right-click action for any selection.

## Install (local .vsix)
```
code --install-extension aurem-cto-0.1.0.vsix
```

## Usage
1. Run command palette → `AUREM: Connect GitHub`
2. Authorize in browser; the extension picks up the token automatically
3. Right-click any selection → `Ship via AUREM CTO`
4. Click the AUREM sidebar icon for the full ORA chat

## Publish (maintainers)
```
npm install
npm run compile
npx vsce package --no-dependencies
npx vsce login auremcto
npx vsce publish
```

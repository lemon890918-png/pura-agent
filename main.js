const { app, BrowserWindow, Menu, Tray, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

// Crash early visibility - LOG_FILE initialized lazily after whenReady
let LOG_FILE = null;
function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  try {
    if (LOG_FILE) fs.appendFileSync(LOG_FILE, line);
  } catch {}
  try {
    process.stdout.write(line);
  } catch {}
}

process.on('uncaughtException', (e) => {
  log(`UNCAUGHT: ${e.stack || e.message}`);
});
process.on('unhandledRejection', (e) => {
  log(`UNHANDLED REJECTION: ${e && (e.stack || e.message) || e}`);
});

// Early bootstrap log — write to /tmp first (always writable)
log(`=== pure-agent launching (bootstrap) ===`);
log(`__dirname = ${__dirname}`);
log(`process.execPath = ${process.execPath}`);

let mainWindow;
let tray;
let gatewayProcess;
// Start pure-agent gateway backend.
//
// In packaged app, __dirname is INSIDE app.asar, so the local .venv/ path
// is unreachable (spawn ENOTDIR). Resolve the project root from
// ~/.pure-agent/PURE_AGENT_PROJECT_ROOT or PURE_AGENT_REPO env, falling
// back to known dev paths.
function resolveProjectRoot() {
  if (process.env.PURE_AGENT_REPO && fs.existsSync(process.env.PURE_AGENT_REPO)) {
    return process.env.PURE_AGENT_REPO;
  }
  if (process.env.PURE_AGENT_PROJECT_ROOT && fs.existsSync(process.env.PURE_AGENT_PROJECT_ROOT)) {
    return process.env.PURE_AGENT_PROJECT_ROOT;
  }
  // Common dev locations to probe
  const candidates = [
    path.join(process.env.HOME || '', 'work', 'pure-agent'),
    path.join(process.env.HOME || '', 'src', 'pure-agent'),
    '/Users/wenxin/work/pure-agent',
  ];
  for (const c of candidates) {
    try {
      if (fs.existsSync(path.join(c, '.venv', 'bin', 'python3'))) return c;
      if (fs.existsSync(path.join(c, 'pyproject.toml'))) return c;
    } catch {}
  }
  return null;
}

function startGateway() {
  const repoRoot = resolveProjectRoot();
  if (!repoRoot) {
    log('Gateway: no project root found; chat will be unavailable until PURE_AGENT_REPO is set');
    return;
  }
  log(`Using repo root: ${repoRoot}`);
  const venvPython = path.join(repoRoot, '.venv', 'bin', 'python3');
  const gatewayModule = 'pure_agent.server.gateway';
  log(`Starting gateway: ${venvPython} -m ${gatewayModule}`);
  if (!fs.existsSync(venvPython)) {
    log(`Gateway: python3 not found at ${venvPython}; run \`uv sync --extra dev\` in ${repoRoot}`);
    return;
  }
  const env = {
    ...process.env,
    PURE_AGENT_PROJECT_ROOT: repoRoot,
  };
  try {
    gatewayProcess = spawn(venvPython, ['-m', gatewayModule], {
      env,
      cwd: repoRoot,
    });
    gatewayProcess.stdout.on('data', (data) => log(`Gateway: ${data}`));
    gatewayProcess.stderr.on('data', (data) => log(`Gateway err: ${data}`));
    gatewayProcess.on('close', (code) => log(`Gateway exit: ${code}`));
    gatewayProcess.on('error', (e) => log(`Gateway spawn err: ${e.message}`));
  } catch (e) {
    log(`Gateway spawn failed: ${e.message}`);
  }
}

function getAppIcon() {
  const customPath = path.join(__dirname, 'ui', 'icon.png');
  if (fs.existsSync(customPath)) return customPath;
  return undefined;
}

function createWindow() {
  log(`createWindow() called`);
  const iconPath = getAppIcon();
  try {
    mainWindow = new BrowserWindow({
      width: 1280,
      height: 820,
      minWidth: 800,
      minHeight: 600,
      titleBarStyle: 'hiddenInset',
      vibrancy: 'under-window',
      visualEffectState: 'active',
      backgroundColor: '#0e0e10',
      show: true,
      webPreferences: {
        preload: path.join(__dirname, 'preload.js'),
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: false,
      },
    });
    if (iconPath) mainWindow.setIcon(iconPath);
    log(`BrowserWindow created`);

    const indexPath = path.join(__dirname, 'ui', 'index.html');
    log(`Loading file: ${indexPath}`);
    log(`  exists? ${fs.existsSync(indexPath)}`);

    mainWindow.webContents.on('did-fail-load', (e, code, desc, url) => {
      log(`did-fail-load: code=${code} desc=${desc} url=${url}`);
    });
    mainWindow.webContents.on('crashed', () => log(`Renderer crashed`));
    mainWindow.webContents.on('render-process-gone', (e, details) => {
      log(`render-process-gone: ${JSON.stringify(details)}`);
    });
    mainWindow.webContents.on('console-message', (e, level, message) => {
      log(`renderer console[${level}]: ${message}`);
    });
    mainWindow.on('show', () => log(`window shown`));
    mainWindow.on('ready-to-show', () => log(`ready-to-show`));

    mainWindow.loadFile(indexPath)
      .then(() => log(`loadFile resolved`))
      .catch((e) => log(`loadFile rejected: ${e.message}`));
  } catch (e) {
    log(`createWindow EXCEPTION: ${e.stack || e.message}`);
  }

  if (process.env.PURE_AGENT_DEV) {
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  }
  mainWindow.on('closed', () => { mainWindow = null; });
}

function createTray() {
  const iconPath = getAppIcon();
  if (!iconPath) return;
  try {
    const icon = nativeImage.createFromPath(iconPath).resize({ width: 16, height: 16 });
    tray = new Tray(icon);
    const contextMenu = Menu.buildFromTemplate([
      { label: 'Open pure-agent', click: () => { if (mainWindow) mainWindow.show(); else createWindow(); } },
      { type: 'separator' },
      { label: 'Quit', click: () => { app.quit(); } },
    ]);
    tray.setToolTip('pure-agent');
    tray.setContextMenu(contextMenu);
    tray.on('click', () => {
      if (mainWindow) mainWindow.show();
      else createWindow();
    });
  } catch (e) {
    log(`tray err: ${e.message}`);
  }
}

log(`registering app.whenReady()`);
app.whenReady().then(() => {
  // NOW we can safely call app.getPath
  try {
    LOG_FILE = path.join(app.getPath('userData'), 'pure-agent-launch.log');
    fs.mkdirSync(path.dirname(LOG_FILE), {recursive: true});
    log(`LOG_FILE set to ${LOG_FILE}`);
  } catch (e) {
    log(`Failed to init LOG_FILE: ${e.message}`);
  }

  log(`app ready event fired`);
  log(`app.getAppPath() = ${app.getAppPath()}`);

  startGateway();
  setTimeout(() => {
    log(`2s elapsed, creating window`);
    createWindow();
    createTray();
    log(`window/tray creation done`);
  }, 1500);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
}).catch((e) => log(`whenReady err: ${e.message}`));

app.on('window-all-closed', () => {
  log(`window-all-closed`);
});

app.on('before-quit', () => {
  log(`before-quit, killing gateway`);
  if (gatewayProcess) gatewayProcess.kill();
});
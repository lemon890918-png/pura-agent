const { app, BrowserWindow, Menu, Tray, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

let mainWindow;
let tray;
let gatewayProcess;

// Start pure-agent gateway backend
function startGateway() {
  const venvPython = path.join(__dirname, '.venv', 'bin', 'python3');
  const gatewayModule = 'pure_agent.server.gateway';
  
  gatewayProcess = spawn(venvPython, ['-m', gatewayModule], {
    env: {
      ...process.env,
      PURE_AGENT_PROJECT_ROOT: process.env.HOME || '/Users/wenxin',
    },
    cwd: __dirname,
  });

  gatewayProcess.stdout.on('data', (data) => {
    console.log(`Gateway: ${data}`);
  });

  gatewayProcess.stderr.on('data', (data) => {
    console.error(`Gateway error: ${data}`);
  });

  gatewayProcess.on('close', (code) => {
    console.log(`Gateway exited with code ${code}`);
  });
}

// Use a built-in icon if no custom icon.png exists
function getAppIcon() {
  const customPath = path.join(__dirname, 'ui', 'icon.png');
  if (fs.existsSync(customPath)) {
    return customPath;
  }
  // Fall back to electron's default
  return undefined;
}

function createWindow() {
  const iconPath = getAppIcon();
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 800,
    minHeight: 600,
    titleBarStyle: 'hiddenInset',
    vibrancy: 'under-window',
    visualEffectState: 'active',
    backgroundColor: '#0e0e10',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  });
  if (iconPath) mainWindow.setIcon(iconPath);

  // Load the local UI file
  mainWindow.loadFile(path.join(__dirname, 'ui', 'index.html'));

  // Open devtools in development
  if (process.env.PURE_AGENT_DEV) {
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function createTray() {
  const iconPath = getAppIcon();
  if (!iconPath) return; // skip tray if no custom icon (avoid crash)
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
}

app.whenReady().then(() => {
  // Start backend gateway
  startGateway();
  
  // Wait 2s for gateway to start
  setTimeout(() => {
    createWindow();
    createTray();
  }, 2000);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', (e) => {
  // Prevent app from quitting when window is closed (stay in tray)
  e.preventDefault();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (gatewayProcess) {
    gatewayProcess.kill();
  }
});

// Hide dock icon if running in background
// app.dock.hide();
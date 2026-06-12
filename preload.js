const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  getAppPath: () => ipcRenderer.invoke('get-app-path'),
  runTerminal: (command, cwd) => ipcRenderer.invoke('run-terminal', command, cwd),
  getDiff: (path) => ipcRenderer.invoke('get-diff', path),
});
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

let loaded = null;
const window = {
  __ModuleLoader__: {
    load(config) {
      loaded = config;
    },
  },
};
const sandbox = {
  window,
  console,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  Date,
  Math,
  JSON,
  Number,
  String,
  Array,
  Object,
  Symbol,
  Error,
};
sandbox.globalThis = sandbox;

const source = fs.readFileSync(path.join(__dirname, "lib", "client.js"), "utf8");
vm.runInNewContext(source, sandbox, { filename: "client.js" });

if (!loaded) throw new Error("module factory was not registered");
if (loaded.id !== "dsh-client-ui-hud") throw new Error("unexpected id: " + loaded.id);

const pluginExports = loaded.factory(() => {
  throw new Error("the desktop bridge must not require runtime modules");
});

if (typeof pluginExports.apply !== "function") throw new Error("apply export missing");
if (!Array.isArray(pluginExports.inject)) throw new Error("inject export missing");
if (!pluginExports.inject.includes("uiSession")) throw new Error("uiSession not injected");
if (!pluginExports.inject.includes("uiWorkspace")) throw new Error("uiWorkspace not injected");
if (pluginExports.inject.includes("slots")) throw new Error("the in-window HUD must not inject slots");

let effectCalled = false;
const ctx = {
  effect() {
    effectCalled = true;
    return () => {};
  },
};
pluginExports.apply(ctx);
if (!effectCalled) throw new Error("apply did not install the bridge effect");

console.log("CLIENT BUNDLE OK (desktop bridge only)");
console.log("inject:", pluginExports.inject.join(", "));

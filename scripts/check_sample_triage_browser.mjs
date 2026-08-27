#!/usr/bin/env node

import assert from "node:assert/strict";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

const target = process.env.SAMPLE_TRIAGE_URL || "http://127.0.0.1:8027/sample-triage.html";
const candidates = process.platform === "win32"
  ? [
      "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
      "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    ]
  : ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"];
const browserBinary = candidates.find(existsSync);
assert.ok(browserBinary, "Chrome or Chromium is required for the browser interaction check");

const getFreePort = () => new Promise((resolve, reject) => {
  const server = net.createServer();
  server.unref();
  server.on("error", reject);
  server.listen(0, "127.0.0.1", () => {
    const { port } = server.address();
    server.close(() => resolve(port));
  });
});

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

const waitForPage = async (port) => {
  const endpoint = `http://127.0.0.1:${port}/json/list`;
  let lastError;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(endpoint);
      if (response.ok) {
        const pages = await response.json();
        const match = pages.find((page) => page.url.includes("/sample-triage.html"));
        if (match) return match;
      }
    } catch (error) {
      lastError = error;
    }
    await delay(125);
  }
  throw new Error(`browser page did not become available: ${lastError || endpoint}`);
};

const connect = (webSocketDebuggerUrl) => new Promise((resolve, reject) => {
  const socket = new WebSocket(webSocketDebuggerUrl);
  const pending = new Map();
  let sequence = 0;

  socket.addEventListener("open", () => {
    const command = (method, params = {}) => new Promise((commandResolve, commandReject) => {
      const id = ++sequence;
      pending.set(id, { resolve: commandResolve, reject: commandReject });
      socket.send(JSON.stringify({ id, method, params }));
    });
    resolve({ socket, command });
  }, { once: true });
  socket.addEventListener("error", reject, { once: true });
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (!message.id || !pending.has(message.id)) return;
    const handlers = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) handlers.reject(new Error(JSON.stringify(message.error)));
    else handlers.resolve(message.result);
  });
});

const profile = mkdtempSync(path.join(os.tmpdir(), "sample-triage-browser-"));
const port = await getFreePort();
const browser = spawn(browserBinary, [
  "--headless=new",
  "--disable-gpu",
  "--disable-background-networking",
  "--no-first-run",
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`,
  target,
], { stdio: "ignore", windowsHide: true });

try {
  const page = await waitForPage(port);
  const { socket, command } = await connect(page.webSocketDebuggerUrl);
  await command("Runtime.enable");
  await command("Page.enable");
  await command("Page.navigate", { url: target });
  const evaluate = async (expression) => {
    const result = await command("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };

  let ready = false;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      ready = await evaluate(`document.querySelector('#model-value')?.textContent !== '—' && document.querySelector('#model-value') !== null`);
    } catch {
      ready = false;
    }
    if (ready) break;
    await delay(50);
  }
  assert.ok(ready, "page did not load the shared proof within four seconds");

  const read = `({
    budget: document.querySelector('#budget-output').textContent,
    budgetLabel: document.querySelector('#budget-label').textContent,
    context: document.querySelector('#queue-context').textContent,
    model: document.querySelector('#model-value').textContent,
    twin: document.querySelector('#twin-value').textContent,
    count: document.querySelector('#count-value').textContent,
    baseline: document.querySelector('#baseline-value').textContent,
    frame: document.querySelector('[data-frame][aria-pressed="true"]').dataset.frame
  })`;
  const initial = await evaluate(read);
  const pooled25 = await evaluate(`(() => {
    document.querySelector('[data-frame="pooled_archive"]').click();
    const slider = document.querySelector('#budget');
    slider.value = '25';
    slider.dispatchEvent(new Event('input', { bubbles: true }));
    return ${read};
  })()`);

  assert.deepEqual(initial, {
    budget: "10%",
    budgetLabel: "Target share inside each pier and year (each group rounded up)",
    context: "595 of 5,328 samples would be selected when the target was applied separately inside every pier and calendar year, with each group rounded up—an effective archive-wide share of 11.17%.",
    model: "102 of 180",
    twin: "100 of 180",
    count: "97 of 180",
    baseline: "77 of 180",
    frame: "within_pier_year",
  });
  assert.deepEqual(pooled25, {
    budget: "25%",
    budgetLabel: "Target share across one pooled archive",
    context: "1,332 of 5,328 samples would be selected when all historical samples were placed in one pooled list—an effective archive-wide share of 25.00%.",
    model: "171 of 180",
    twin: "167 of 180",
    count: "170 of 180",
    baseline: "155 of 180",
    frame: "pooled_archive",
  });

  socket.close();
  console.log(JSON.stringify({ initial, pooled25, verdict: "PASS interactive controls" }, null, 2));
} finally {
  browser.kill();
  await delay(300);
  rmSync(profile, { recursive: true, force: true });
}

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
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      ready = await evaluate(`document.body.dataset.proofStatus === 'ready' && !document.querySelector('#assess-button').disabled`);
    } catch {
      ready = false;
    }
    if (ready) break;
    await delay(50);
  }
  if (!ready) {
    const diagnostic = await evaluate(`({
      url: location.href,
      status: document.body?.dataset?.proofStatus,
      proofState: document.querySelector('#proof-state')?.textContent,
      formError: document.querySelector('#form-error')?.textContent,
      buttonDisabled: document.querySelector('#assess-button')?.disabled,
      title: document.title
    })`);
    throw new Error(`page did not load the verified proof within five seconds: ${JSON.stringify(diagnostic)}`);
  }

  const inputContract = await evaluate(`({
    sstRequired: document.querySelector('#sst').required,
    chlorophyllRequired: document.querySelector('#chlorophyll').required,
    chlorophyllPlaceholder: document.querySelector('#chlorophyll').placeholder,
    proofState: document.querySelector('#proof-state').textContent,
    initialDate: document.querySelector('#sample-date').value
  })`);
  assert.equal(inputContract.sstRequired, true);
  assert.equal(inputContract.chlorophyllRequired, false);
  assert.equal(inputContract.chlorophyllPlaceholder, "Leave blank if pending");
  assert.equal(inputContract.proofState, "Verified retrospective reference loaded");
  assert.match(inputContract.initialDate, /^\d{4}-\d{2}-\d{2}$/);

  const high = await evaluate(`(() => {
    const set = (selector, value) => {
      const element = document.querySelector(selector);
      element.value = value;
      element.dispatchEvent(new Event('input', { bubbles: true }));
      element.dispatchEvent(new Event('change', { bubbles: true }));
    };
    set('#sample-label', 'High count');
    set('#location', 'MontereyWharf');
    set('#pn-del', '100000');
    set('#pn-ser', '');
    set('#sst', '16.4');
    set('#chlorophyll', '');
    document.querySelector('#assess-button').click();
    const cells = [...document.querySelectorAll('#queue-body tr:first-child td')].map((cell) => cell.textContent.trim());
    return {
      title: document.querySelector('#priority-title').textContent,
      summary: document.querySelector('#priority-summary').textContent,
      historical: document.querySelector('#historical-band').textContent,
      station: document.querySelector('#station-band').textContent,
      sst: document.querySelector('#sst-context').textContent,
      chlorophyll: document.querySelector('#chlorophyll-context').textContent,
      note: document.querySelector('#result-note').textContent,
      queueDescription: document.querySelector('#queue-description').textContent,
      cells,
      rows: document.querySelectorAll('#queue-body tr').length
    };
  })()`);
  assert.equal(high.title, "Expedite candidate");
  assert.match(high.summary, /High count: 100,000 cells\/L across 1 reported microscope group\./);
  assert.match(high.historical, /266 archive samples; 47 above the Lab study’s strict domoic-acid comparison of greater than 0\.5 micrograms per litre \(17\.7%\)/);
  assert.equal(high.station, "top 25% of 276 historical samples");
  assert.equal(high.sst, "16.4 °C · Inside the central 90% of the archive");
  assert.equal(high.chlorophyll, "Not supplied · optional");
  assert.match(high.note, /displayed sum is partial—not a true total/);
  assert.match(high.note, /not a toxin probability or safety determination/);
  assert.equal(high.queueDescription, "One sample cannot establish a relative batch rank. Add the other samples facing the same assay decision.");
  assert.equal(high.rows, 1);
  assert.deepEqual(high.cells.slice(0, 7), ["1", `High count${inputContract.initialDate}`, "Monterey Wharf", "100,000 cells/Lone group reported", "16.4 °C", "—", "Expedite candidate"]);

  const twoSamples = await evaluate(`(() => {
    const set = (selector, value) => {
      const element = document.querySelector(selector);
      element.value = value;
      element.dispatchEvent(new Event('input', { bubbles: true }));
      element.dispatchEvent(new Event('change', { bubbles: true }));
    };
    set('#sample-label', 'Lower count');
    set('#pn-del', '1000');
    set('#pn-ser', '1000');
    set('#sst', '15.5');
    set('#chlorophyll', '2.5');
    document.querySelector('#assess-button').click();
    return {
      title: document.querySelector('#priority-title').textContent,
      chlorophyll: document.querySelector('#chlorophyll-context').textContent,
      description: document.querySelector('#queue-description').textContent,
      rows: [...document.querySelectorAll('#queue-body tr')].map((row) => ({
        sample: row.cells[1].querySelector('strong').textContent,
        count: row.cells[3].textContent.trim(),
        queue: row.cells[6].textContent.trim()
      }))
    };
  })()`);
  assert.equal(twoSamples.title, "Routine queue by microscope count");
  assert.equal(twoSamples.chlorophyll, "2.5 mg/m³ · Inside the central 90% of the archive");
  assert.equal(twoSamples.description, "2 samples ranked by reported-group sum; 1 marked for the expedited queue.");
  assert.deepEqual(twoSamples.rows, [
    { sample: "High count", count: "100,000 cells/Lone group reported", queue: "Expedite candidate" },
    { sample: "Lower count", count: "2,000 cells/L", queue: "Routine queue" },
  ]);

  const capacityTwo = await evaluate(`(() => {
    const capacity = document.querySelector('#capacity');
    capacity.value = '2';
    capacity.dispatchEvent(new Event('input', { bubbles: true }));
    return {
      description: document.querySelector('#queue-description').textContent,
      queues: [...document.querySelectorAll('#queue-body tr')].map((row) => row.cells[6].textContent.trim())
    };
  })()`);
  assert.equal(capacityTwo.description, "2 samples ranked by reported-group sum; 2 marked for the expedited queue.");
  assert.deepEqual(capacityTwo.queues, ["Expedite candidate", "Expedite candidate"]);

  const missingCounts = await evaluate(`(() => {
    document.querySelector('#sst').value = '16';
    document.querySelector('#assess-button').click();
    return {
      error: document.querySelector('#form-error').textContent,
      rows: document.querySelectorAll('#queue-body tr').length
    };
  })()`);
  assert.equal(missingCounts.error, "Enter at least one microscope-count group. Leave the other blank if it was not reported.");
  assert.equal(missingCounts.rows, 2);

  const evidence = await evaluate(`({
    raw: document.querySelector('#raw-capture').textContent,
    random: document.querySelector('#random-capture').textContent,
    ceiling: document.querySelector('#ceiling-capture').textContent,
    verdict: document.querySelector('#evidence-verdict').textContent,
    variation: document.querySelector('#eval-variation').textContent,
    hypotheses: document.querySelector('#eval-hypotheses').textContent
  })`);
  assert.equal(evidence.raw, "66 / 180");
  assert.equal(evidence.random, "20 / 180");
  assert.equal(evidence.ceiling, "116 / 180");
  assert.match(evidence.verdict, /78–81 elevated samples versus 66 for raw counts/);
  assert.match(evidence.verdict, /one required model’s difference interval \(-3 to \+28\) includes zero/);
  assert.equal(evidence.variation, "model beat raw count in 11/51 pier-years and 13/18 years");
  assert.equal(evidence.hypotheses, "1; full-refit permutation test NOT RUN");

  socket.close();
  console.log(JSON.stringify({ inputContract, high, twoSamples, capacityTwo, missingCounts, evidence, verdict: "PASS interactive microscope-count queue" }, null, 2));
} finally {
  browser.kill();
  await delay(300);
  rmSync(profile, { recursive: true, force: true });
}

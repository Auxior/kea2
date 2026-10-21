import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../kea_workbench/static/client.js", import.meta.url), "utf8");
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    textContent: "", innerHTML: "", hidden: false, disabled: false,
    value: "", checked: false, dataset: {}, elements: [], listeners: {},
    addEventListener(type, fn) { this.listeners[type] = fn; },
    setAttribute(name, value) { this[name] = value; },
    setCustomValidity() {}, scrollIntoView() {},
  });
  return elements.get(id);
}
const run = {
  id: "run-ui", status: "completed", statusLabel: "已完成", error: "",
  startedAt: "2026-10-20T11:00:00Z", elapsedSeconds: 36000, steps: 32710, findings: 0,
  configuration: { package: "com.unit.app", runningMinutes: 600, maxStep: 99999 },
  device: { model: "Unit", osVersion: "15", serial: "unit" }, recentLogs: [],
  delivery: { status: "failed", revision: 557, error: "old timeout", canRetry: true, busy: false },
};
let retryCalls = 0, resolveRetry;
const response = (value, ok = true) => ({ ok, json: async () => structuredClone(value) });
const fetch = async (url) => {
  if (url.endsWith("/retry")) {
    retryCalls += 1;
    return new Promise((resolve) => { resolveRetry = resolve; });
  }
  if (url === "/api/config") return response({ settings: { platform: { frontend: "http://unit" } }, projects: {} });
  if (url === "/api/devices") return response({ devices: [] });
  if (url === "/api/runs") return response({ runs: [run] });
  throw new Error(`Unexpected request ${url}`);
};
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const api = await new AsyncFunction("document", "localStorage", "fetch", "setInterval", source + `
return { showRun, deliveryDetail, deliveryLabel, poll };
`)(
  { getElementById: element, hidden: false },
  { getItem: () => run.id, setItem() {} }, fetch, () => {},
);
const click = element("retry").listeners.click;
assert.equal(element("retry").hidden, false);
const pending = click();
assert.equal(element("retry").disabled, true);
assert.equal(element("retry").textContent, "正在提交重试…");
assert.equal(element("delivery-error").hidden, true);
await click();
assert.equal(retryCalls, 1);
const syncing = structuredClone(run);
syncing.delivery = {
  status: "uploading", busy: true, canRetry: false, error: "", message: "正在上传报告文件",
  progress: { uploadedFiles: 3, totalFiles: 5, uploadedBytes: 1048576, totalBytes: 4194304,
    currentFileBytes: 1048576, currentFileTotal: 2097152, currentFile: "raw/large.log" },
};
resolveRetry(response(syncing));
await pending;
assert.equal(element("delivery").textContent, "上传文件中");
assert.match(element("delivery-detail").textContent, /3\/5.*2.0\/4.0 MiB.*raw\/large.log/);
assert.equal(element("retry").disabled, true);

// A failed HTTP request remains visible beside the retry control after finally.
await api.poll();
const failedRequest = click();
resolveRetry(response({ detail: "工作台正在关闭，请重启后重试" }, false));
await failedRequest;
assert.equal(element("delivery-error").hidden, false);
assert.match(element("delivery-error").textContent, /工作台正在关闭/);
assert.equal(element("retry").disabled, false);

const retrying = structuredClone(run);
retrying.delivery = { status: "retrying", canRetry: true, busy: false,
  message: "已保存上传进度", nextRetryAt: "2026-10-21T02:00:00Z" };
api.showRun(retrying);
assert.equal(element("delivery").textContent, "等待自动重试");
assert.match(element("delivery-detail").textContent, /下次自动重试/);
api.showRun({ ...run, delivery: { status: "uploaded", final: true, lastSyncedAt: "2026-10-21T02:00:00Z" } });
assert.equal(element("delivery").textContent, "最终报告已同步");
assert.equal(element("retry").hidden, true);
assert.equal(element("delivery-error").hidden, true);
console.log("Retry feedback, deduplication, progress, errors and final status passed");

const $ = (id) => document.getElementById(id);
const active = new Set(["preparing", "running", "stopping", "finalizing"]);
const deliveryLabels = {
  waiting: "待达标",
  pending: "同步中",
  queued: "已排队",
  preparing: "准备报告",
  uploading: "上传文件中",
  reporting: "等待平台确认",
  retrying: "等待自动重试",
  uploaded: "已上报",
  failed: "上报失败",
  skipped: "未达门槛",
  disabled: "已关闭",
};
let settings,
  projects,
  selected,
  runs = [],
  busy = false;
const retryRequests = new Set();
const retryErrors = new Map();
function deliveryLabel(run) {
  const d = run.delivery;
  if (d.status === "uploaded")
    return d.final === true ? "最终报告已同步" : d.final === false ? "过程报告已同步" : "已上报";
  return deliveryLabels[d.status] || d.status;
}
function deliveryDetail(run) {
  const d = run.delivery, p = d.progress;
  const parts = [d.message || ""];
  if (p && ["uploading", "retrying"].includes(d.status)) {
    const mib = (bytes) => (bytes / 1048576).toFixed(1);
    parts.push(`已同步 ${p.uploadedFiles}/${p.totalFiles} 个文件，${mib(p.uploadedBytes + p.currentFileBytes)}/${mib(p.totalBytes)} MiB`);
    if (p.currentFile) parts.push(`当前文件：${p.currentFile}`);
  }
  if (d.nextRetryAt)
    parts.push(`下次自动重试：${new Date(d.nextRetryAt).toLocaleTimeString("zh-CN", { hour12: false })}`);
  if (d.lastSyncedAt)
    parts.push(`上次同步成功：${new Date(d.lastSyncedAt).toLocaleString("zh-CN", { hour12: false })}`);
  return parts.filter(Boolean).join("；");
}
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
async function request(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const value = await response.json();
  if (!response.ok)
    throw new Error(
      typeof value.detail === "string"
        ? value.detail
        : JSON.stringify(value.detail || value),
    );
  return value;
}
const post = (url, body) =>
  request(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
function message(text = "") {
  $("message").textContent = text;
  $("message").hidden = !text;
}
function configuration() {
  const data = {};
  for (const element of $("run-form").elements) {
    if (!element.name) continue;
    data[element.name] =
      element.type === "checkbox"
        ? element.checked
        : element.type === "number"
          ? Number(element.value)
          : element.value.trim();
  }
  return data;
}
function updateForm() {
  const c = configuration(),
    project = projects?.[c.package];
  $("project").textContent = project
    ? `自动关联 · ${project.name}`
    : "未关联项目，关闭平台上报后仍可执行";
  $("duration-help").textContent =
    `${c.runningMinutes} 分钟${c.runningMinutes >= 60 ? ` = ${(c.runningMinutes / 60).toFixed(1)} 小时` : ""}`;
  for (const name of ["preFailureScreenshots", "postFailureScreenshots"])
    $(name).disabled = !c.takeScreenshots;
  $("postFailureScreenshots").setCustomValidity(
    c.takeScreenshots && c.postFailureScreenshots > c.preFailureScreenshots
      ? "失败后截图数量不能超过失败前截图数量"
      : "",
  );
  $("start").disabled =
    busy ||
    !c.serial ||
    (c.platformEnabled && !project) ||
    runs.some(
      (r) => r.configuration.serial === c.serial && active.has(r.status),
    );
  $("command").textContent =
    `kea2 run -s ${c.serial || "<设备>"} -p ${c.package}\n  --running-minutes ${c.runningMinutes} --max-step ${c.maxStep}\n  --throttle ${c.throttle} --profile-period ${c.profilePeriod}${c.takeScreenshots ? `\n  --take-screenshots --pre-failure-screenshots ${c.preFailureScreenshots}\n  --post-failure-screenshots ${c.postFailureScreenshots}` : ""}`;
}
async function refreshDevices() {
  const previous = $("serial").value;
  $("refresh-devices").disabled = true;
  try {
    const result = await request("/api/devices");
    $("serial").innerHTML =
      '<option value="">选择已连接设备</option>' +
      result.devices
        .map(
          (d) =>
            `<option value="${esc(d.serial)}" ${d.state !== "device" ? "disabled" : ""}>${esc(d.model)} · ${esc(d.serial)}${d.state !== "device" ? ` · ${esc(d.state)}` : ""}</option>`,
        )
        .join("");
    const available = result.devices.filter((d) => d.state === "device");
    $("serial").value = available.some((d) => d.serial === previous)
      ? previous
      : available.length === 1
        ? available[0].serial
        : "";
    if (!available.length)
      message("未发现可用设备，请连接 Android 手机并授权 USB 调试。");
  } catch (error) {
    message(error.message);
  } finally {
    $("refresh-devices").disabled = false;
    updateForm();
  }
}
function showRun(run) {
  selected = run.id;
  localStorage.setItem("kea2-selected-run", selected);
  $("empty").hidden = true;
  $("detail").hidden = false;
  $("status").textContent = run.statusLabel;
  $("status").dataset.state = run.status;
  $("run-package").textContent = run.configuration.package;
  $("run-device").textContent =
    `${run.device.model} · Android ${run.device.osVersion} · ${run.device.serial}`;
  $("elapsed").textContent =
    `${run.elapsedSeconds.toFixed(1)} 秒 / ${run.configuration.runningMinutes} 分钟`;
  $("progress").max = run.configuration.runningMinutes * 60;
  $("progress").value = run.elapsedSeconds;
  $("steps").textContent = run.steps.toLocaleString();
  $("findings").textContent = run.findings;
  $("delivery").textContent = deliveryLabel(run);
  $("delivery-detail").textContent = deliveryDetail(run);
  $("delivery-detail").hidden = !$("delivery-detail").textContent;
  if (run.delivery.busy || run.delivery.status === "uploaded") retryErrors.delete(run.id);
  for (const [id, text] of [
    ["run-error", run.error],
    ["delivery-error", retryRequests.has(run.id) ? "" : (retryErrors.get(run.id) || run.delivery.error)],
  ]) {
    $(id).textContent = text || "";
    $(id).hidden = !text;
  }
  if ($("follow").checked || !$("logs").textContent) {
    const text = run.recentLogs.join("\n");
    if ($("logs").textContent !== text) {
      $("logs").textContent = text;
      if ($("follow").checked) $("logs").scrollTop = $("logs").scrollHeight;
    }
  }
  $("stop").disabled = !active.has(run.status) || run.status === "stopping";
  $("stop").textContent = run.status === "stopping" ? "正在停止…" : "停止测试";
  $("report").href = `/reports/${run.id}/index.html`;
  $("report").hidden = active.has(run.status) && !run.qualifiedAt;
  $("full-log").href = `/reports/${run.id}/console.log`;
  $("platform-link").href =
    `${settings.platform.frontend}/task-center?taskId=${encodeURIComponent(run.delivery.taskId || "")}`;
  $("platform-link").hidden = !run.delivery.taskId;
  const requesting = retryRequests.has(run.id);
  const retryable = run.delivery.canRetry ?? (
    run.delivery.status === "failed" && !active.has(run.status)
  );
  const syncing = run.delivery.busy || requesting;
  $("retry").hidden = !(retryable || syncing);
  $("retry").disabled = !!syncing;
  $("retry").textContent = requesting ? "正在提交重试…" : syncing ? "正在上报…" : "立即重试上报";
  $("retry").setAttribute("aria-busy", String(!!syncing));
  $("delivery-log").href = `/reports/${run.id}/delivery.log`;
  $("delivery-log").hidden = !run.delivery.attemptStartedAt;
  $("snapshot").textContent = JSON.stringify(run.configuration, null, 2);
  $("run-id").textContent = run.id;
}
function showHistory() {
  $("history-count").textContent = `${runs.length} 次运行`;
  $("history-empty").hidden = !!runs.length;
  $("history").innerHTML = runs
    .map(
      (r) =>
        `<tr><td><strong>${esc(r.configuration.package)}</strong><small>${esc(r.device.model)}</small></td><td>${esc(new Date(r.startedAt).toLocaleString("zh-CN", { hour12: false }))}</td><td>${r.configuration.runningMinutes} 分钟 / ${r.configuration.maxStep.toLocaleString()} 步</td><td><span class="badge" data-state="${esc(r.status)}">${esc(r.statusLabel)}</span></td><td>${esc(deliveryLabel(r))}</td><td><button class="text-button" data-run="${r.id}">查看过程</button>${!active.has(r.status) || r.qualifiedAt ? `<a class="text-button" href="/reports/${r.id}/index.html" target="_blank" rel="noopener">报告 ↗</a>` : ""}</td></tr>`,
    )
    .join("");
}
async function poll() {
  try {
    const data = await request("/api/runs");
    runs = data.runs;
    showHistory();
    const run = runs.find((r) => r.id === selected) || runs[0];
    if (run) showRun(run);
    $("connection").textContent = "本机已连接";
    $("connection").dataset.state = "ok";
    updateForm();
  } catch (error) {
    $("connection").textContent = "连接中断";
    $("connection").dataset.state = "error";
    message(error.message);
  }
}
$("run-form").addEventListener("input", updateForm);
$("run-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!$("run-form").reportValidity()) return;
  busy = true;
  updateForm();
  message();
  try {
    const run = await post("/api/runs", configuration());
    showRun(run);
    await poll();
  } catch (error) {
    message(error.message);
  } finally {
    busy = false;
    updateForm();
  }
});
$("refresh-devices").addEventListener("click", refreshDevices);
$("stop").addEventListener("click", async () => {
  if (!selected) return;
  $("stop").disabled = true;
  try {
    showRun(await post(`/api/runs/${selected}/stop`));
  } catch (error) {
    message(error.message);
  }
});
$("retry").addEventListener("click", async () => {
  const runId = selected;
  if (!runId || retryRequests.has(runId)) return;
  retryRequests.add(runId);
  retryErrors.delete(runId);
  const current = runs.find((run) => run.id === runId);
  if (current) showRun(current);
  $("delivery-error").hidden = true;
  $("delivery-detail").hidden = false;
  $("delivery-detail").textContent = "正在提交重试请求…";
  try {
    const run = await post(`/api/runs/${runId}/retry`);
    runs = runs.map((r) => r.id === runId ? run : r);
    if (selected === runId) showRun(run);
  } catch (error) {
    retryErrors.set(runId, error.message);
  } finally {
    retryRequests.delete(runId);
    if (selected === runId) {
      const run = runs.find((r) => r.id === runId);
      if (run) showRun(run);
    }
  }
});
$("history").addEventListener("click", (event) => {
  const button = event.target.closest("[data-run]");
  if (button) {
    $("logs").textContent = "";
    showRun(runs.find((r) => r.id === button.dataset.run));
    $("detail").scrollIntoView({ block: "nearest" });
  }
});
$("settings-open").addEventListener("click", () => {
  for (const [section, values] of Object.entries(settings))
    for (const [key, value] of Object.entries(values)) {
      const input = $(`${section}-${key}`);
      if (input) input.value = value;
    }
  $("settings-error").hidden = true;
  $("settings-dialog").showModal();
});
$("settings-close").addEventListener("click", () =>
  $("settings-dialog").close(),
);
$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const next = structuredClone(settings);
  for (const [section, values] of Object.entries(next))
    for (const key of Object.keys(values))
      if ($(`${section}-${key}`))
        next[section][key] = $(`${section}-${key}`).value.trim();
  try {
    const saved = await request("/api/settings", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(next),
    });
    settings = saved.settings;
    $("settings-dialog").close();
    message("设置已保存，对之后新建的测试生效。");
  } catch (error) {
    $("settings-error").textContent = error.message;
    $("settings-error").hidden = false;
  }
});
try {
  const config = await request("/api/config");
  settings = config.settings;
  projects = config.projects;
  selected = localStorage.getItem("kea2-selected-run");
  await refreshDevices();
  await poll();
} catch (error) {
  message(error.message);
}
setInterval(() => {
  if (!document.hidden) poll();
}, 2000);

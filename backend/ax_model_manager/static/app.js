'use strict';

const $ = (id) => document.getElementById(id);
const tokenInput = $('token');
let timer = null;
let currentLogJob = null;
let logTimer = null;
let logLoading = false;
let controlEnabled = false;
let modelRows = [];
let launchModel = null;
let launchMode = 'start';
let launchManifest = null;
let currentDemoUrl = '';
let currentConfig = {};
const CONTEXT_OPTIONS = [4096, 8192, 16384, 32768, 65536, 131072, 262144];

tokenInput.value = localStorage.getItem('axmm.token') || '';

function applyTheme(theme) {
  const selected = theme === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = selected;
  localStorage.setItem('axmm.theme', selected);
  $('theme-toggle').textContent = selected === 'light' ? '🌙' : '☀️';
  $('theme-toggle').title = selected === 'light' ? 'Switch to dark theme' : 'Switch to light theme';
}

function authHeaders() {
  const token = tokenInput.value.trim();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function showAlert(message) {
  const alert = $('alert');
  alert.textContent = message;
  alert.classList.remove('hidden');
}

function clearAlert() {
  $('alert').classList.add('hidden');
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...authHeaders(),
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
  });
  let payload = {};
  try { payload = await response.json(); } catch (_) {}
  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === 'string' ? detail : JSON.stringify(detail ?? payload);
    throw new Error(message || `HTTP ${response.status}`);
  }
  return payload;
}

function stateClass(state) {
  if (['running', 'ready'].includes(state)) return 'running';
  if (['starting', 'loading', 'stopping'].includes(state)) return 'loading';
  if (['failed', 'invalid'].includes(state)) return 'failed';
  return '';
}

function formatSize(bytes) {
  if (!Number.isFinite(bytes)) return '—';
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  return `${Math.round(bytes / 1e3)} KB`;
}

function formatKb(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  // tempmon and /proc report KiB; dividing twice yields GiB, not GB.
  return `${(number / 1024 / 1024).toFixed(1)} GiB`;
}

function formatNumber(value, digits = 1) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : '—';
}

function formatTime(value) {
  if (!value) return '—';
  return new Date(value * 1000).toLocaleString();
}

function errorText(detail) {
  if (!detail) return '';
  if (typeof detail === 'string') return detail;
  if (detail.message) return detail.message;
  return JSON.stringify(detail);
}

function publicRuntimeOrigin(port) {
  const protocol = location.protocol === 'https:' ? 'https:' : 'http:';
  return `${protocol}//${location.hostname}:${port}`;
}

function liteWebUiUrl(activeModel) {
  const active = activeModel?.active_job;
  if (!active?.port) return '';

  const url = new URL(`${location.protocol}//${location.hostname}:18100/`);
  url.searchParams.set('api', publicRuntimeOrigin(active.port));
  const modelId = active.runtime_models?.[0] || activeModel.id;
  if (modelId) url.searchParams.set('model', modelId);
  return url.toString();
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    const original = button.textContent;
    button.textContent = 'Copied';
    setTimeout(() => { button.textContent = original; }, 1200);
  } catch (_) {
    const area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.append(area);
    area.select();
    document.execCommand('copy');
    area.remove();
    const original = button.textContent;
    button.textContent = 'Copied';
    setTimeout(() => { button.textContent = original; }, 1200);
  }
}

function copyButton(text, label = 'Copy') {
  const button = document.createElement('button');
  button.className = 'small';
  button.textContent = label;
  button.onclick = () => copyText(text, button);
  return button;
}

function apiBox(title, node, actions = []) {
  const box = document.createElement('div');
  box.className = 'api-box';
  const caption = document.createElement('span');
  caption.textContent = title;
  box.append(caption, node);
  if (actions.length) {
    const row = document.createElement('div');
    row.className = 'api-actions';
    row.append(...actions);
    box.append(row);
  }
  return box;
}

function renderActiveApi(models) {
  const container = $('active-api');
  const statePill = $('api-state');
  container.replaceChildren();

  const activeModels = models.filter(model => model.active_job?.port);
  if (!activeModels.length) {
    statePill.className = 'pill';
    statePill.textContent = 'No model running';
    const message = document.createElement('p');
    message.className = 'message';
    message.style.margin = '0';
    message.textContent = 'Start a model to see its OpenAI-compatible API base URL, model ID, and request example.';
    container.append(message);
    return;
  }

  const firstModel = activeModels[0];
  const firstActive = firstModel.active_job;
  const state = firstActive.state || 'unknown';
  statePill.className = `pill ${stateClass(state)}`;
  statePill.textContent = activeModels.length === 1
    ? `${firstModel.name} · ${state}`
    : `${activeModels.length} runtimes · first ${firstModel.name}`;

  for (const activeModel of activeModels) {
    const active = activeModel.active_job;
    const base = `${publicRuntimeOrigin(active.port)}/v1`;
    const chatUrl = `${base}/chat/completions`;
    const modelId = active.runtime_models?.[0] || activeModel.id;
    const curl = [
      `curl ${chatUrl} \\`,
      `  -H 'Content-Type: application/json' \\`,
      `  --data-binary '{"model":"${modelId}","messages":[{"role":"user","content":"Hello"}]}'`,
    ].join('\n');

    const baseUrlNode = document.createElement('div');
    const baseLink = document.createElement('a');
    baseLink.href = base;
    baseLink.target = '_blank';
    baseLink.rel = 'noopener';
    baseLink.textContent = base;
    baseUrlNode.append(baseLink);

    const modelNode = document.createElement('code');
    modelNode.textContent = modelId;
    modelNode.title = active.runtime_models?.length
      ? active.runtime_models.join('\n')
      : 'Advertised model ID will be refreshed after the runtime becomes ready';

    const requestNode = document.createElement('pre');
    requestNode.textContent = curl;

    container.append(
      apiBox(`OpenAI base URL · ${activeModel.name}`, baseUrlNode, [
        copyButton(base, 'Copy base URL'),
        copyButton(chatUrl, 'Copy chat URL'),
      ]),
      apiBox(`Model ID · ${activeModel.name}`, modelNode, [copyButton(modelId, 'Copy model ID')]),
      apiBox(`Chat request · ${activeModel.name}`, requestNode, [copyButton(curl, 'Copy curl')]),
    );
  }
}

function renderLiteWebUi(models) {
  const frame = $('lite-webui-frame');
  const statePill = $('lite-webui-state');
  const openLink = $('lite-webui-open');
  const reloadButton = $('lite-webui-reload');
  const activeModels = models.filter(model => model.active_job?.port);
  const desiredUrl = liteWebUiUrl(activeModels[0]);

  if (!desiredUrl) {
    currentDemoUrl = '';
    frame.classList.add('hidden');
    frame.removeAttribute('src');
    openLink.classList.add('hidden');
    reloadButton.disabled = true;
    statePill.className = 'pill';
    statePill.textContent = 'No model running';
    return;
  }

  const activeModel = activeModels[0];
  const active = activeModel.active_job;
  const state = active.state || 'unknown';
  statePill.className = `pill ${stateClass(state)}`;
  statePill.textContent = activeModels.length === 1
    ? `${activeModel.name} · ${state}`
    : `${activeModels.length} runtimes · first ${activeModel.name}`;

  openLink.classList.remove('hidden');
  openLink.href = desiredUrl;
  reloadButton.disabled = false;
  frame.classList.remove('hidden');
  if (currentDemoUrl !== desiredUrl) {
    currentDemoUrl = desiredUrl;
    frame.src = desiredUrl;
  }
}

function deviceCard(title, value, details = [], chips = [], className = '') {
  const card = document.createElement('article');
  card.className = `device-card ${className}`.trim();
  const heading = document.createElement('h3');
  heading.textContent = title;
  const output = document.createElement('div');
  output.className = 'device-value';
  output.textContent = value;
  card.append(heading, output);

  for (const detail of details.filter(Boolean)) {
    const element = document.createElement('div');
    element.className = 'device-detail';
    element.textContent = detail;
    card.append(element);
  }

  if (chips.length) {
    const container = document.createElement('div');
    container.className = 'device-chips';
    for (const [label, title] of chips.filter(Boolean)) {
      const pill = document.createElement('span');
      pill.className = 'pill';
      pill.textContent = label;
      if (title) pill.title = title;
      container.append(pill);
    }
    card.append(container);
  }
  return card;
}

function summaryPill(label, title = '') {
  const pill = document.createElement('span');
  pill.className = 'pill';
  pill.textContent = label;
  if (title) pill.title = title;
  return pill;
}

function renderDeviceResources(resources = {}) {
  const status = $('device-status');
  const identity = $('device-identity');
  const grid = $('device-grid');
  identity.replaceChildren();
  grid.replaceChildren();

  if (!resources.available) {
    status.className = 'pill failed';
    status.textContent = 'tempmon unavailable';
    identity.append(summaryPill('Device metrics unavailable'));
    const message = document.createElement('p');
    message.className = 'message';
    message.style.margin = '0';
    message.textContent = 'Check tempmon on port 8080.';
    grid.append(message);
    return;
  }

  status.className = 'pill running';
  status.textContent = 'tempmon online';
  const agent = resources.agent || {};
  const load = resources.load || {};
  const cpu = resources.cpu || {};
  const cmm = resources.cmm || {};
  const memory = resources.os_memory || {};
  const npu = resources.npu || {};
  const net = resources.net || {};
  const llama = resources.llama || {};
  const usedMemory = Number(memory.total_kb || 0) - Number(memory.avail_kb || 0);
  const maxTemp = Number(resources.npu_max_temp_c);

  // Only the most decision-relevant metrics are always visible. Detailed
  // identity, per-zone temperatures, clocks, and filesystem usage stay folded.
  identity.append(
    summaryPill(
      cmm.total_kb ? `CMM ${formatKb(cmm.used_kb)} / ${formatKb(cmm.total_kb)}` : 'CMM —',
      cmm.total_kb ? `free ${formatKb(Number(cmm.total_kb) - Number(cmm.used_kb || 0))}` : '',
    ),
    summaryPill(
      memory.total_kb ? `OS ${formatKb(usedMemory)} / ${formatKb(memory.total_kb)}` : 'OS —',
      memory.avail_kb ? `available ${formatKb(memory.avail_kb)}` : '',
    ),
    summaryPill(
      Number.isFinite(maxTemp) ? `NPU ${formatNumber(maxTemp)}°C` : 'NPU —',
      (resources.thermal_zones || [])
        .map(zone => `${zone.type || zone.name}: ${formatNumber(zone.c)}°C`)
        .join('\n'),
    ),
    summaryPill(
      `CPU ${formatNumber(cpu.percent)}% / load ${formatNumber(load['1'])}`,
      `load 5m ${formatNumber(load['5'])} · 15m ${formatNumber(load['15'])}`,
    ),
    summaryPill(
      net.rx_mbps == null && net.tx_mbps == null ? 'NET —' : `NET ↓${formatNumber(net.rx_mbps)}/↑${formatNumber(net.tx_mbps)}`,
      [
        net.rx_bytes != null ? `RX total ${formatSize(Number(net.rx_bytes) * 1024)}` : '',
        net.tx_bytes != null ? `TX total ${formatSize(Number(net.tx_bytes) * 1024)}` : '',
      ].filter(Boolean).join('\n'),
    ),
    summaryPill(
      `LLAMA ${llama.process_count ?? 0}`,
      [
        llama.rss_kb != null ? `RSS ${formatKb(llama.rss_kb)}` : '',
        llama.listening_ports?.length ? `ports ${llama.listening_ports.join(', ')}` : '',
        ...(llama.processes || []).map(process => `PID ${process.pid} · RSS ${formatKb(process.rss_kb)}`),
      ].filter(Boolean).join('\n'),
    ),
  );

  const temperatureClass = maxTemp >= 70 ? ' hot' : maxTemp >= 55 ? ' warn' : '';
  grid.append(
    deviceCard(
      'Board identity',
      agent.chip_type || '—',
      [
        agent.version ? `agent ${agent.version}` : '',
        agent.bsp_version || '',
        resources.sampled_at ? `sample ${new Date(resources.sampled_at * 1000).toLocaleTimeString()}` : '',
      ],
    ),
    deviceCard(
      'CPU',
      `${cpu.cores || 0} cores`,
      [
        `load ${formatNumber(load['1'])} / ${formatNumber(load['5'])} / ${formatNumber(load['15'])}`,
        cpu.frequency_min_mhz === cpu.frequency_max_mhz
          ? `${formatNumber(cpu.frequency_min_mhz / 1000, 2)} GHz`
          : `${formatNumber(cpu.frequency_min_mhz / 1000, 2)}–${formatNumber(cpu.frequency_max_mhz / 1000, 2)} GHz`,
      ],
    ),
    deviceCard(
      'NPU thermal zones',
      Number.isFinite(maxTemp) ? `${formatNumber(maxTemp)} °C` : '—',
      [Number.isFinite(maxTemp) ? (maxTemp >= 70 ? 'high' : maxTemp >= 55 ? 'warm' : 'normal') : ''],
      (resources.thermal_zones || []).map(zone => [
        `${zone.type || zone.name || 'zone'} ${formatNumber(zone.c)}°C`,
        `${zone.name || ''} ${zone.type || ''}`.trim(),
      ]),
      temperatureClass,
    ),
    deviceCard(
      'NPU clocks',
      npu.tdp_freq_mhz ? `${npu.tdp_freq_mhz} MHz TDP` : '—',
      [],
      (npu.clock_mhz || []).map(value => [`${formatNumber(value, 0)} MHz`]),
    ),
  );

  const diskCard = document.createElement('article');
  diskCard.className = 'device-card';
  const diskTitle = document.createElement('h3');
  diskTitle.textContent = 'Disks';
  const diskValue = document.createElement('div');
  diskValue.className = 'device-value';
  diskValue.textContent = `${resources.disks?.length || 0} filesystems`;
  const bars = document.createElement('div');
  bars.className = 'device-bars';
  for (const disk of resources.disks || []) {
    const row = document.createElement('div');
    row.className = 'device-bar-row';
    const label = document.createElement('span');
    const name = document.createElement('span');
    name.textContent = disk.mount || disk.fs || 'disk';
    const usage = document.createElement('b');
    const percent = disk.total_kb ? Number(disk.used_kb || 0) / Number(disk.total_kb) * 100 : 0;
    usage.textContent = `${percent.toFixed(1)}% · free ${formatKb(disk.avail_kb)}`;
    label.append(name, usage);
    const bar = document.createElement('div');
    bar.className = 'bar';
    const fill = document.createElement('div');
    fill.className = 'fill';
    fill.style.width = `${Math.min(100, Math.max(0, percent))}%`;
    bar.append(fill);
    row.append(label, bar);
    bars.append(row);
  }
  diskCard.append(diskTitle, diskValue, bars);
  if (!resources.disks?.length) {
    const empty = document.createElement('div');
    empty.className = 'device-detail';
    empty.textContent = 'No filesystem data reported';
    diskCard.append(empty);
  }
  grid.append(diskCard);
}

function renderRuntimeReconciliation(reconciliation = {}) {
  const container = $('runtime-reconciliation');
  if (!container) return;
  container.replaceChildren();
  const orphans = reconciliation.orphans || [];
  if (!orphans.length) {
    const message = document.createElement('p');
    message.className = 'message';
    message.style.margin = '0';
    message.textContent = `Runtime state consistent · ${reconciliation.active_runtime_count ?? 0} active runtime(s) · no orphan units`;
    container.append(message);
    return;
  }
  for (const orphan of orphans) {
    const row = document.createElement('div');
    row.className = 'runtime-orphan';
    const info = document.createElement('code');
    info.textContent = `${orphan.runtime_id}:${orphan.port || '?'} · PID ${orphan.pid || '?'}`;
    const actions = document.createElement('div');
    actions.className = 'runtime-orphan-actions';
    const adopt = document.createElement('button');
    adopt.className = 'small';
    adopt.textContent = 'Adopt';
    adopt.onclick = () => control(`/api/v1/runtimes/${orphan.runtime_id}/adopt`, {});
    const stop = document.createElement('button');
    stop.className = 'small danger';
    stop.textContent = 'Stop';
    stop.onclick = () => control(`/api/v1/runtimes/${orphan.runtime_id}/stop`, {});
    actions.append(adopt, stop);
    row.append(info, actions);
    container.append(row);
  }
}

function renderModels(models, config = {}) {
  modelRows = models;
  const showInvalid = $('show-invalid').checked;
  const container = $('models');
  const anyActive = models.some(model => Boolean(model.active_job));
  const activeCount = models.filter(model => model.active_job?.action === 'start').length;
  const modelLimit = Number(config.max_running_models || 1);
  container.replaceChildren();
  for (const model of models) {
    if (!showInvalid && !model.valid) continue;
    const active = model.active_job;
    const isTask = model.runtime_type === 'task';
    const isImageGen = (model.capabilities || []).includes('image_gen');
    const state = active?.state || (model.ready_to_start ? 'ready' : model.valid ? 'stopped' : 'invalid');
    const card = document.createElement('article');
    card.className = 'model-card';

    const title = document.createElement('div');
    title.className = 'model-title';
    const name = document.createElement('strong');
    name.textContent = model.name;
    name.title = model.name;
    const statePill = document.createElement('span');
    statePill.className = `pill ${stateClass(state)}`;
    statePill.textContent = state;
    title.append(name, statePill);

    const id = document.createElement('div');
    id.className = 'model-id';
    id.textContent = `${model.id} · ${model.relative_directory}`;

    const metadata = document.createElement('div');
    metadata.className = 'metadata';
    for (const item of [
      model.manifest_source,
      formatSize(model.size_bytes),
      ...(model.capabilities || []),
      model.api_type || '',
      model.runtime_type === 'task' ? 'one-shot task' : '',
      ...(model.profiles || []),
      active?.port ? `:${active.port}` : '',
    ].filter(Boolean)) {
      const pill = document.createElement('span');
      pill.className = 'pill';
      pill.textContent = item;
      metadata.append(pill);
    }

    const message = document.createElement('div');
    if (model.errors?.length) {
      message.className = 'message error';
      message.textContent = model.errors.slice(0, 2).join(' · ');
    } else {
      message.className = 'message';
      message.textContent = model.warnings?.length ? model.warnings.join(' · ') : 'Artifacts validated';
    }

    const actions = document.createElement('div');
    actions.className = 'model-actions';
    const details = document.createElement('button');
    details.className = 'small';
    details.textContent = 'Details';
    details.onclick = () => openModelDialog(model);
    actions.append(details);
    if (controlEnabled) {
      if (model.manifest_source === 'draft' && model.valid) {
        const confirm = document.createElement('button');
        confirm.className = 'small primary';
        confirm.textContent = 'Confirm manifest';
        confirm.onclick = () => control(`/api/v1/models/${model.id}/manifest/confirm`, {});
        actions.append(confirm);
      }
      if (!active && model.ready_to_start && !isTask) {
        const start = document.createElement('button');
        start.className = 'small primary';
        start.textContent = isImageGen
          ? (activeCount >= modelLimit ? 'Force start' : 'Start')
          : (activeCount >= modelLimit ? 'Force start…' : 'Start…');
        start.onclick = () => (isImageGen
          ? startImageGen(model)
          : openLaunchDialog(model, 'start'));
        actions.append(start);
      }
      if (model.valid && !isImageGen) {
        const options = document.createElement('button');
        options.className = 'small';
        options.textContent = 'Options…';
        options.title = 'Edit this model profile and launch options';
        options.onclick = () => openLaunchDialog(model, active ? 'restart' : 'start');
        actions.append(options);
      }
      if (!active && anyActive && model.ready_to_start && !isTask) {
        const switchButton = document.createElement('button');
        switchButton.className = 'small';
        switchButton.textContent = isImageGen ? 'Switch to' : 'Switch to…';
        switchButton.onclick = () => (isImageGen
          ? startImageGen(model, 'switch')
          : openLaunchDialog(model, 'switch'));
        actions.append(switchButton);
      }
      if (active) {
        const stop = document.createElement('button');
        stop.className = 'small danger';
        stop.textContent = 'Stop';
        stop.onclick = () => stopModel(model.id);
        actions.append(stop);
        const restart = document.createElement('button');
        restart.className = 'small';
        restart.textContent = 'Restart…';
        restart.onclick = () => (isImageGen
          ? control(`/api/v1/models/${model.id}/restart`, { profile: 'default' })
          : openLaunchDialog(model, 'restart'));
        actions.append(restart);
        if (isImageGen && active.port) {
          const open = document.createElement('button');
          open.className = 'small';
          open.textContent = 'Open web UI';
          open.title = `Open the image generation web page on port ${active.port}`;
          open.onclick = () => window.open(`http://${window.location.hostname}:${active.port}/`, '_blank');
          actions.append(open);
        }
      }
    }

    card.append(title, id, metadata, message, actions);
    container.append(card);
  }
  if (!container.children.length) {
    const empty = document.createElement('div');
    empty.className = 'message';
    empty.textContent = 'No models';
    container.append(empty);
  }
}

function renderJobs(jobs) {
  const container = $('jobs');
  container.replaceChildren();
  for (const job of jobs.slice(0, 40)) {
    const row = document.createElement('article');
    row.className = 'job';
    const head = document.createElement('div');
    head.className = 'job-row';
    const title = document.createElement('strong');
    title.textContent = `${job.model} · ${job.action}`;
    const state = document.createElement('span');
    state.className = `pill ${stateClass(job.state)}`;
    state.textContent = job.state;
    head.append(title, state);
    const detail = document.createElement('div');
    detail.className = 'job-detail';
    detail.textContent = `${job.job_id} · ${formatTime(job.created_at)} · ${job.port ? `port ${job.port}` : 'no port'}${job.error ? ` · ${job.error}` : ''}`;
    const actions = document.createElement('div');
    actions.className = 'model-actions';
    const log = document.createElement('button');
    log.className = 'small';
    log.textContent = 'Log';
    log.onclick = () => openLog(job.job_id);
    actions.append(log);
    row.append(head, detail, actions);
    container.append(row);
  }
  if (!container.children.length) {
    const empty = document.createElement('div');
    empty.className = 'message';
    empty.textContent = 'No jobs';
    container.append(empty);
  }
}

function renderAudit(records) {
  const body = $('audit');
  body.replaceChildren();
  for (const record of records.slice(-100).reverse()) {
    const tr = document.createElement('tr');
    const cells = [
      formatTime(record.at),
      record.action,
      record.model || '',
      record.job || '',
      JSON.stringify(Object.fromEntries(Object.entries(record).filter(([key]) => !['at','action','model','job'].includes(key)))),
    ];
    for (const value of cells) {
      const td = document.createElement('td');
      td.textContent = value;
      tr.append(td);
    }
    body.append(tr);
  }
}

async function refresh() {
  try {
    const [dashboard, models, jobs, audit] = await Promise.all([
      api('/api/v1/dashboard'),
      api('/api/v1/models'),
      api('/api/v1/jobs'),
      api('/api/v1/audit?limit=100'),
    ]);
    clearAlert();
    controlEnabled = Boolean(dashboard.config?.enable_control);
    currentConfig = dashboard.config || {};
    $('subheader').textContent = `${dashboard.version} · ${dashboard.config?.process_backend || 'none'} · ${dashboard.config?.model_root}`;
    $('metric-manager').textContent = dashboard.config?.read_only ? 'read-only' : 'control';
    $('metric-models').textContent = dashboard.model_count;
    $('metric-valid').textContent = dashboard.valid_model_count;
    $('metric-active').textContent = `${dashboard.active_job_count}/${dashboard.configured_model_limit ?? '—'}`;
    const limitSelect = $('max-running-models');
    if (document.activeElement !== limitSelect) {
      limitSelect.value = String(dashboard.configured_model_limit || dashboard.config?.max_running_models || 1);
    }
    $('save-model-limit').disabled = !controlEnabled;
    renderDeviceResources(dashboard.resources || {});
    renderRuntimeReconciliation(dashboard.runtime_reconciliation || {});
    renderModels(models.models, dashboard.config || {});
    renderActiveApi(models.models);
    renderLiteWebUi(models.models);
    renderJobs(jobs.jobs);
    renderAudit(audit.records);
  } catch (error) {
    showAlert(error.message);
  }
}

function detailRow(label, value, title = '') {
  const row = document.createElement('div');
  row.className = 'detail-row';
  const key = document.createElement('span');
  key.textContent = label;
  const output = document.createElement('b');
  output.textContent = value;
  if (title) output.title = title;
  row.append(key, output);
  return row;
}

async function openModelDialog(model) {
  try {
    const detail = await api(`/api/v1/models/${model.id}`);
    const isImageGen = (detail.capabilities || []).includes('image_gen');
    $('model-dialog-title').textContent = detail.name || detail.id;
    $('model-dialog-subtitle').textContent = `${detail.id} · ${detail.directory}`;
    const summary = $('model-detail-summary');
    summary.replaceChildren();
    const runtimePort = detail.active_job?.port;
    const apiBase = runtimePort ? `http://${window.location.hostname}:${runtimePort}` : null;
    for (const [label, value] of [
      ['State', detail.active_job?.state || (detail.ready_to_start ? 'ready' : detail.valid ? 'stopped' : 'invalid')],
      ['Runtime', detail.runtime_type === 'task' ? 'one-shot task' : 'long-running service'],
      ['Manifest', detail.manifest_source],
      ['Valid', detail.valid ? 'yes' : 'no'],
      ['Size', formatSize(detail.size_bytes)],
      ['Files', detail.file_count ?? '—'],
      ...(isImageGen ? [
        ['Engine', detail.manifest?.engine || '—'],
        ['API', detail.manifest?.api?.type || '—'],
        ['Endpoints', (detail.manifest?.api?.endpoints || []).join('  ') || '—'],
        ['API base', apiBase || '—'],
        ['Web UI', apiBase ? `${apiBase}${detail.manifest?.api?.web_ui || '/'}` : '—'],
      ] : [
        ['Context', detail.max_context?.toLocaleString() || '—'],
        ['Default output', detail.defaults?.max_output_tokens?.toLocaleString() || '—'],
        ['KV cache', detail.defaults?.kv_cache_quantization || '—'],
        ['Attention', detail.defaults?.attention_quantization || '—'],
        ['Speculative', detail.defaults?.speculative?.enabled === undefined
          ? '—'
          : detail.defaults.speculative.enabled
            ? detail.defaults.speculative.type || 'enabled'
            : 'off'],
      ]),
      ['Capabilities', (detail.capabilities || []).join(', ') || '—'],
      ['Profiles', (detail.profiles || []).join(', ') || '—'],
    ]) {
      summary.append(detailRow(label, String(value)));
    }

    const artifacts = $('model-detail-artifacts');
    artifacts.replaceChildren();
    for (const artifact of detail.artifacts || []) {
      const state = artifact.exists
        ? `${artifact.path}${artifact.executable ? ' · executable' : ''}`
        : (artifact.errors?.join('; ') || 'missing');
      artifacts.append(detailRow(artifact.key, state));
    }

    const jobs = $('model-detail-jobs');
    jobs.replaceChildren();
    for (const job of detail.recent_jobs || []) {
      jobs.append(detailRow(
        `${job.action} · ${job.state}`,
        `${job.job_id} · ${formatTime(job.created_at)}${job.failure_code ? ` · ${job.failure_code}` : job.error ? ` · ${job.error}` : ''}`,
      ));
    }
    if (!(detail.recent_jobs || []).length) jobs.append(detailRow('Recent jobs', 'none'));
    $('model-detail-manifest').textContent = JSON.stringify(detail.manifest, null, 2);
    $('model-dialog').showModal();
  } catch (error) {
    showAlert(error.message);
  }
}

async function saveModelLimit() {
  const value = Number($('max-running-models').value);
  if (!Number.isFinite(value) || value < 1) return;
  if (value > 1 && !confirm(
    `Allow up to ${value} concurrent model runtimes?\n\nConfirm OS and CMM memory are sufficient. Each runtime uses a separate port.`
  )) return;
  try {
    await api('/api/v1/config', {
      method: 'PUT',
      body: JSON.stringify({ max_running_models: value }),
    });
    clearAlert();
    await refresh();
  } catch (error) {
    showAlert(error.message);
  }
}

async function openSettings() {
  try {
    currentConfig = await api('/api/v1/config');
  } catch (_) {}
  $('model-root-input').value = currentConfig?.model_root || '';
  const result = $('settings-result');
  result.className = 'settings-result hidden';
  result.textContent = '';
  $('settings-dialog').showModal();
}

function renderSettingsResult(result) {
  const output = $('settings-result');
  const errors = Array.isArray(result?.errors) ? result.errors : [];
  const errorText = errors
    .map(item => typeof item === 'string'
      ? item
      : `${item.id || 'model'}: ${(item.errors || []).join(', ')}`)
    .join('\n');
  const failed = result?.valid === false;
  output.className = `settings-result ${failed ? 'error' : 'ok'}`;
  if (failed) {
    output.textContent = `Cannot use this model root.\n${errorText || 'validation failed'}`;
    return;
  }
  const count = result?.count ?? result?.model_count ?? '—';
  const valid = result?.valid_count ?? '—';
  const ready = result?.ready_to_start_count ?? '—';
  output.textContent = [
    `Model root: ${result?.model_root || $('model-root-input').value}`,
    `Models found: ${count}`,
    `Valid: ${valid}`,
    `Ready to start: ${ready}`,
  ].join('\n') + (errorText ? `\n\nModel issues:\n${errorText}` : '');
}

async function validateModelRoot() {
  const value = $('model-root-input').value.trim();
  if (!value) return;
  try {
    const result = await api('/api/v1/config/validate', {
      method: 'POST',
      body: JSON.stringify({ model_root: value }),
    });
    renderSettingsResult(result);
  } catch (error) {
    showAlert(error.message);
  }
}

async function saveModelRoot() {
  const value = $('model-root-input').value.trim();
  if (!value) return;
  try {
    await api('/api/v1/config', {
      method: 'PUT',
      body: JSON.stringify({ model_root: value }),
    });
    const result = await api('/api/v1/models/scan', { method: 'POST' });
    currentConfig.model_root = result.model_root;
    renderSettingsResult(result);
    clearAlert();
    await refresh();
  } catch (error) {
    showAlert(error.message);
  }
}

async function control(path, body) {
  try {
    if (body && !confirm('Confirm this model control action?')) return;
    const result = await api(path, { method: 'POST', body: JSON.stringify(body || {}) });
    clearAlert();
    // Stop jobs do not own a runtime log; opening their log dialog automatically
    // previously produced an empty page immediately after clicking Stop.
    if (result?.job && result.job.action !== "stop") await openLog(result.job.job_id);
    await refresh();
  } catch (error) {
    showAlert(error.message);
    await refresh();
  }
}

function currentSamplingPayload() {
  return {
    temperature: Number($('temperature').value) || 0,
    top_k: Number($('top-k').value) || 0,
    top_p: Number($('top-p').value) || 1,
    min_p: Number($('min-p').value) || 0,
    repeat_last_n: Number($('repeat-last-n').value) || 0,
    repeat_penalty: Number($('repeat-penalty').value) || 1,
    presence_penalty: Number($('presence-penalty').value) || 0,
    frequency_penalty: Number($('frequency-penalty').value) || 0,
  };
}

function currentLaunchPayload() {
  return {
    profile: $('profile').value || 'default',
    context: CONTEXT_OPTIONS.includes(Number($('context').value))
      ? Number($('context').value)
      : 32768,
    batch: $('batch').value === '' ? null : Number($('batch').value),
    ubatch: $('ubatch').value === '' ? null : Number($('ubatch').value),
    cache_ram: $('cache-ram').value === '' ? null : Number($('cache-ram').value),
    ctxcp: $('ctxcp').value === '' ? null : Number($('ctxcp').value),
    mtp: $('mtp').checked,
    metrics: $('metrics').checked,
    sampling: currentSamplingPayload(),
  };
}

function stopModel(id) {
  control(`/api/v1/models/${id}/stop`, { timeout_seconds: 15 });
}

// Image-generation runtimes carry a fixed launch template, so they are
// started directly with their default profile instead of the LLM dialog.
function startImageGen(model, mode = 'start') {
  const endpoint = mode === 'switch'
    ? `/api/v1/models/${model.id}/switch-to`
    : `/api/v1/models/${model.id}/start`;
  control(endpoint, { profile: 'default' });
}

function applyLaunchProfile(name) {
  const profile = launchManifest?.profiles?.[name] || {};
  const configuredContext = Number(profile.context);
  const context = Number.isFinite(configuredContext)
    ? (CONTEXT_OPTIONS.includes(configuredContext) ? configuredContext : 32768)
    : 32768;
  $('context').value = String(context);
  $('batch').value = profile.batch ?? '';
  $('ubatch').value = profile.ubatch ?? '';
  $('cache-ram').value = profile.cache_ram ?? '';
  $('ctxcp').value = profile.ctxcp ?? '';
  $('mtp').checked = Boolean(profile.mtp);
  $('metrics').checked = Boolean(profile.metrics);
  const sampling = profile.sampling || {};
  $('temperature').value = sampling.temperature ?? 0;
  $('top-k').value = sampling.top_k ?? 0;
  $('top-p').value = sampling.top_p ?? 1;
  $('min-p').value = sampling.min_p ?? 0;
  $('repeat-last-n').value = sampling.repeat_last_n ?? 0;
  $('repeat-penalty').value = sampling.repeat_penalty ?? 1;
  $('presence-penalty').value = sampling.presence_penalty ?? 0;
  $('frequency-penalty').value = sampling.frequency_penalty ?? 0;
  $('port').value = '';
  $('launch-force').checked = false;
}

async function openLaunchDialog(model, mode = 'start') {
  try {
    const detail = await api(`/api/v1/models/${model.id}`);
    launchModel = model;
    launchMode = mode;
    launchManifest = detail.manifest || {};
    $('launch-title').textContent = {
      start: 'Start model',
      restart: 'Restart model',
      switch: 'Switch to model',
    }[mode] || 'Model options';
    $('launch-model-name').textContent = `${model.name} · ${model.id}`;
    $('launch-submit').textContent = mode === 'switch' ? 'Switch' : mode === 'restart' ? 'Restart' : 'Start';
    $('launch-force').parentElement.classList.toggle('hidden', mode !== 'start');
    $('launch-submit').classList.toggle(
      'hidden',
      model.runtime_type === 'task' || launchManifest?.runtime_type === 'task',
    );
    $('launch-task-message').classList.toggle(
      'hidden',
      !(model.runtime_type === 'task' || launchManifest?.runtime_type === 'task'),
    );
    const isImageGen = (model.capabilities || []).includes('image_gen');
    document.querySelectorAll('[data-llama-only]').forEach(element => {
      element.classList.toggle('hidden', isImageGen);
    });
    $('launch-imagegen-message').classList.toggle('hidden', !isImageGen);

    const profileSelect = $('profile');
    profileSelect.replaceChildren();
    for (const name of model.profiles?.length ? model.profiles : ['default']) {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      profileSelect.append(option);
    }
    profileSelect.value = model.profiles?.includes('default') ? 'default' : profileSelect.firstElementChild?.value;
    applyLaunchProfile(profileSelect.value);
    $('launch-dialog').showModal();
  } catch (error) {
    showAlert(error.message);
  }
}

async function submitLaunchDialog() {
  if (!launchModel) return;
  const payload = { ...currentLaunchPayload() };
  const port = Number($('port').value);
  if (port) payload.port = port;
  if (launchMode === 'start') payload.force = $('launch-force').checked;
  if (launchMode !== 'start') payload.stop_timeout_seconds = 20;

  if (payload.force && !confirm(
    'Force start beyond the configured model-count limit?\n\nConfirm OS and CMM memory are sufficient. Multiple runtimes will use separate ports.'
  )) return;

  const endpoint = {
    start: `/api/v1/models/${launchModel.id}/start`,
    restart: `/api/v1/models/${launchModel.id}/restart`,
    switch: `/api/v1/models/${launchModel.id}/switch-to`,
  }[launchMode];
  $('launch-dialog').close();
  await control(endpoint, payload);
}

async function saveLaunchProfile() {
  if (!launchModel) return;
  const profileName = $('profile').value || 'default';
  if (!confirm(`Save current options to profile ${profileName} for ${launchModel.id}?`)) return;
  const payload = { ...currentLaunchPayload(), reasoning: 'off' };
  try {
    await api(`/api/v1/models/${launchModel.id}/profiles/${profileName}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
    clearAlert();
    await refresh();
  } catch (error) {
    showAlert(error.message);
  }
}

async function duplicateLaunchProfile() {
  if (!launchModel) return;
  const target = $('new-profile-name').value.trim();
  if (!target) return showAlert('Enter a new profile name first.');
  await control(
    `/api/v1/models/${launchModel.id}/profiles/${$('profile').value}/duplicate`,
    { target },
  );
  launchModel = { ...launchModel, profiles: [...new Set([...(launchModel.profiles || []), target])] };
  $('new-profile-name').value = '';
  await openLaunchDialog(launchModel, launchMode);
  $('profile').value = target;
  applyLaunchProfile(target);
}

async function resetLaunchProfile() {
  if (!launchModel) return;
  const profile = $('profile').value;
  if (!confirm(`Reset profile ${profile} to its original manifest values?`)) return;
  await control(`/api/v1/models/${launchModel.id}/profiles/${profile}/reset`, {});
  await openLaunchDialog({ ...launchModel }, launchMode);
}

async function deleteLaunchProfile() {
  if (!launchModel) return;
  const profile = $('profile').value;
  if (profile === 'default') return showAlert('The default profile cannot be deleted.');
  if (!confirm(`Delete profile ${profile}?`)) return;
  await api(`/api/v1/models/${launchModel.id}/profiles/${profile}`, { method: 'DELETE' });
  launchModel = {
    ...launchModel,
    profiles: (launchModel.profiles || []).filter(item => item !== profile),
  };
  await openLaunchDialog(launchModel, launchMode);
}

async function diffLaunchProfile() {
  if (!launchModel) return;
  const target = $('new-profile-name').value.trim();
  if (!target) return showAlert('Enter the new profile name to compare.');
  const left = $('profile').value;
  try {
    const result = await api(
      `/api/v1/models/${launchModel.id}/profiles/${left}/diff/${target}`
    );
    const output = $('profile-diff');
    output.classList.remove('hidden');
    output.textContent = result.changes?.length
      ? result.changes.map(change => `${change.change.padEnd(7)} ${change.field}: ${JSON.stringify(change.left)} → ${JSON.stringify(change.right)}`).join('\n')
      : 'No differences.';
  } catch (error) {
    showAlert(error.message);
  }
}

async function previewLaunchCommand() {
  if (!launchModel) return;
  const payload = { ...currentLaunchPayload() };
  const port = Number($('port').value);
  if (port) payload.port = port;
  try {
    const result = await api(`/api/v1/models/${launchModel.id}/preview`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    const output = $('launch-preview-output');
    output.classList.remove('hidden');
    output.textContent = [
      `cwd: ${result.cwd}`,
      `env: ${Object.entries(result.env || {}).map(([key, value]) => `${key}=${value}`).join(' ')}`,
      `port: ${result.port}`,
      'argv:',
      result.argv.map((argument, index) => `${String(index).padStart(2)} ${argument}`).join('\n'),
    ].join('\n');
  } catch (error) {
    showAlert(error.message);
  }
}

async function openLog(jobId) {
  currentLogJob = jobId;
  $('log-title').textContent = `Job ${jobId}`;
  $('log-dialog').showModal();
  await loadLog({ force: true, follow: true });
  startLogPolling();
}

async function loadLog({ force = false, follow = null } = {}) {
  if (!currentLogJob || (logLoading && !force)) return;
  const content = $('log-content');
  const nearBottom = content.scrollTop + content.clientHeight >= content.scrollHeight - 32;
  const shouldFollow = follow ?? ($('follow-log')?.checked && nearBottom);
  logLoading = true;
  try {
    const parameters = new URLSearchParams({
      lines: '400',
      search: $('log-filter').value.trim(),
      errors_only: $('log-errors-only').checked ? 'true' : 'false',
    });
    $('log-download').href = `/api/v1/jobs/${currentLogJob}/logs?${parameters.toString()}&output_format=text`;
    const result = await api(`/api/v1/jobs/${currentLogJob}/logs?${parameters.toString()}`);
    content.replaceChildren();
    if (!result.path) {
      const line = document.createElement('span');
      line.className = 'log-line warning';
      line.textContent = 'This job has no runtime log. Open the associated start job to inspect llama-server output.';
      content.append(line);
    } else if (!result.lines.length) {
      const line = document.createElement('span');
      line.className = 'log-line';
      line.textContent = '(empty log)';
      content.append(line);
    } else {
      for (const text of result.lines) {
        const line = document.createElement('span');
        line.className = `log-line${/error|failed|failure|exception|traceback|oom|out of memory/i.test(text) ? ' error' : /warn/i.test(text) ? ' warning' : ''}`;
        line.textContent = text;
        content.append(line);
      }
    }
    if (shouldFollow) content.scrollTop = content.scrollHeight;
  } catch (error) {
    content.textContent = error.message;
  } finally {
    logLoading = false;
  }
}

function stopLogPolling() {
  if (logTimer) clearInterval(logTimer);
  logTimer = null;
}

function startLogPolling() {
  stopLogPolling();
  if (!$('auto-log')?.checked) return;
  logTimer = setInterval(() => loadLog(), 1000);
}

function scheduleRefresh() {
  if (timer) clearInterval(timer);
  timer = setInterval(refresh, 2500);
}

$('connect').onclick = () => {
  localStorage.setItem('axmm.token', tokenInput.value.trim());
  refresh();
};
$('refresh').onclick = refresh;
$('settings').onclick = openSettings;
$('settings-close').onclick = () => $('settings-dialog').close();
$('validate-model-root').onclick = validateModelRoot;
$('save-model-root').onclick = saveModelRoot;
$('theme-toggle').onclick = () => {
  applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
};
$('show-invalid').onchange = () => renderModels(modelRows);
$('save-model-limit').onclick = saveModelLimit;
$('lite-webui-reload').onclick = () => {
  const frame = $('lite-webui-frame');
  if (currentDemoUrl) frame.src = currentDemoUrl;
};
$('profile').onchange = event => applyLaunchProfile(event.target.value);
$('launch-submit').onclick = submitLaunchDialog;
$('launch-save-profile').onclick = saveLaunchProfile;
$('launch-duplicate-profile').onclick = duplicateLaunchProfile;
$('launch-reset-profile').onclick = resetLaunchProfile;
$('launch-delete-profile').onclick = deleteLaunchProfile;
$('launch-diff-profile').onclick = diffLaunchProfile;
$('launch-preview').onclick = previewLaunchCommand;
$('launch-cancel').onclick = () => $('launch-dialog').close();
$('model-dialog-close').onclick = () => $('model-dialog').close();
$('launch-force').onchange = event => {
  if (event.target.checked) {
    event.target.checked = confirm(
      'Start beyond the configured model-count limit? Confirm OS/CMM memory is sufficient.'
    );
  }
};
$('reload-log').onclick = () => loadLog({ force: true, follow: Boolean($('follow-log')?.checked) });
$('log-filter').oninput = () => loadLog({ force: true });
$('log-errors-only').onchange = () => loadLog({ force: true });
$('close-log').onclick = () => $('log-dialog').close();
$('auto-log').onchange = startLogPolling;
$('log-dialog').addEventListener('close', stopLogPolling);
tokenInput.addEventListener('keydown', event => { if (event.key === 'Enter') $('connect').click(); });

applyTheme(localStorage.getItem('axmm.theme'));

refresh();
scheduleRefresh();

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
  return `${(number / 1024 / 1024).toFixed(1)} GB`;
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

function renderDeviceResources(resources = {}) {
  const status = $('device-status');
  const identity = $('device-identity');
  const grid = $('device-grid');
  identity.replaceChildren();
  grid.replaceChildren();

  if (!resources.available) {
    status.className = 'pill failed';
    status.textContent = 'tempmon unavailable';
    const message = document.createElement('p');
    message.className = 'message';
    message.style.margin = '0';
    message.textContent = 'Device metrics are unavailable. Check tempmon on port 8080.';
    grid.append(message);
    return;
  }

  status.className = 'pill running';
  status.textContent = 'tempmon online';
  const agent = resources.agent || {};
  for (const value of [
    agent.version ? `agent ${agent.version}` : '',
    agent.bsp_version || '',
    agent.chip_type || '',
    resources.sampled_at ? `sample ${new Date(resources.sampled_at * 1000).toLocaleTimeString()}` : '',
  ].filter(Boolean)) {
    const pill = document.createElement('span');
    pill.className = 'pill';
    pill.textContent = value;
    identity.append(pill);
  }

  const load = resources.load || {};
  const cpu = resources.cpu || {};
  const cmm = resources.cmm || {};
  const memory = resources.os_memory || {};
  const npu = resources.npu || {};
  const net = resources.net || {};
  const llama = resources.llama || {};
  const usedMemory = Number(memory.total_kb || 0) - Number(memory.avail_kb || 0);
  const cmmPercent = cmm.total_kb ? Number(cmm.used_kb || 0) / Number(cmm.total_kb) * 100 : null;
  const memoryPercent = memory.total_kb ? usedMemory / Number(memory.total_kb) * 100 : null;
  const maxTemp = Number(resources.npu_max_temp_c);
  const temperatureClass = maxTemp >= 70 ? ' hot' : maxTemp >= 55 ? ' warn' : '';

  grid.append(
    deviceCard(
      'CPU load',
      `${formatNumber(load['1'])} / ${formatNumber(load['5'])} / ${formatNumber(load['15'])}`,
      ['1 / 5 / 15 minute averages'],
      cpu.cores ? [[`${cpu.cores} cores`]] : [],
    ),
    deviceCard(
      'CPU frequency',
      cpu.frequency_min_mhz === cpu.frequency_max_mhz
        ? `${formatNumber(cpu.frequency_min_mhz / 1000, 2)} GHz`
        : `${formatNumber(cpu.frequency_min_mhz / 1000, 2)}–${formatNumber(cpu.frequency_max_mhz / 1000, 2)} GHz`,
      [`${cpu.frequency_mhz?.length || 0} online cores`],
    ),
    deviceCard(
      'CMM memory',
      cmm.total_kb ? `${formatKb(cmm.used_kb)} / ${formatKb(cmm.total_kb)}` : '—',
      [
        cmm.total_kb ? `free ${formatKb(Number(cmm.total_kb) - Number(cmm.used_kb || 0))}` : '',
        cmmPercent == null ? '' : `${cmmPercent.toFixed(1)}% used`,
      ],
    ),
    deviceCard(
      'OS memory',
      memory.total_kb ? `${formatKb(usedMemory)} / ${formatKb(memory.total_kb)}` : '—',
      [
        memory.avail_kb ? `available ${formatKb(memory.avail_kb)}` : '',
        memoryPercent == null ? '' : `${memoryPercent.toFixed(1)}% used`,
      ],
    ),
  );

  const temperatureCard = deviceCard(
    'NPU temperature',
    Number.isFinite(maxTemp) ? `${formatNumber(maxTemp)} °C` : '—',
    [Number.isFinite(maxTemp) ? (maxTemp >= 70 ? 'high temperature' : maxTemp >= 55 ? 'warm' : 'normal') : ''],
    (resources.thermal_zones || []).map(zone => [
      `${zone.type || zone.name || 'zone'} ${formatNumber(zone.c)}°C`,
      `${zone.name || ''} ${zone.type || ''}`.trim(),
    ]),
    `device-wide${temperatureClass}`,
  );
  grid.append(temperatureCard);

  grid.append(
    deviceCard(
      'NPU frequency',
      npu.tdp_freq_mib || npu.tdp_freq_mhz
        ? `${npu.tdp_freq_mhz || npu.tdp_freq_mib} MHz`
        : '—',
      ['TDP current frequency'],
      (npu.clock_mhz || []).map(value => [`${formatNumber(value, 0)} MHz`]),
    ),
    deviceCard(
      `Network ${net.iface || ''}`.trim(),
      net.rx_mbps == null && net.tx_mbps == null
        ? '—'
        : `↓${formatNumber(net.rx_mbps)} / ↑${formatNumber(net.tx_mbps)} Mbps`,
      [
        net.rx_bytes != null ? `RX total ${formatSize(Number(net.rx_bytes) * 1024)}` : '',
        net.tx_bytes != null ? `TX total ${formatSize(Number(net.tx_bytes) * 1024)}` : '',
      ],
    ),
    deviceCard(
      'llama-server processes',
      `${llama.process_count ?? 0} process${Number(llama.process_count) === 1 ? '' : 'es'}`,
      [
        llama.rss_kb != null ? `RSS ${formatKb(llama.rss_kb)}` : '',
        llama.listening_ports?.length ? `ports ${llama.listening_ports.join(', ')}` : 'no managed ports reported',
      ],
      (llama.processes || []).map(process => [
        `PID ${process.pid} · ${formatKb(process.rss_kb)}`,
      ]),
    ),
  );

  const diskCard = document.createElement('article');
  diskCard.className = 'device-card device-wide';
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
    if (controlEnabled) {
      if (model.manifest_source === 'draft' && model.valid) {
        const confirm = document.createElement('button');
        confirm.className = 'small primary';
        confirm.textContent = 'Confirm manifest';
        confirm.onclick = () => control(`/api/v1/models/${model.id}/manifest/confirm`, {});
        actions.append(confirm);
      }
      if (!active && model.ready_to_start) {
        const start = document.createElement('button');
        start.className = 'small primary';
        start.textContent = activeCount >= modelLimit ? 'Force start…' : 'Start…';
        start.onclick = () => openLaunchDialog(model, 'start');
        actions.append(start);
      }
      if (model.valid) {
        const options = document.createElement('button');
        options.className = 'small';
        options.textContent = 'Options…';
        options.title = 'Edit this model profile and launch options';
        options.onclick = () => openLaunchDialog(model, active ? 'restart' : 'start');
        actions.append(options);
      }
      if (!active && anyActive && model.ready_to_start) {
        const switchButton = document.createElement('button');
        switchButton.className = 'small';
        switchButton.textContent = 'Switch to…';
        switchButton.onclick = () => openLaunchDialog(model, 'switch');
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
        restart.onclick = () => openLaunchDialog(model, 'restart');
        actions.append(restart);
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
    $('subheader').textContent = `${dashboard.version} · ${dashboard.config?.process_backend || 'none'} · ${dashboard.config?.model_root}`;
    $('metric-manager').textContent = dashboard.config?.read_only ? 'read-only' : 'control';
    $('metric-models').textContent = dashboard.model_count;
    $('metric-valid').textContent = dashboard.valid_model_count;
    $('metric-active').textContent = `${dashboard.active_job_count}/${dashboard.configured_model_limit ?? '—'}`;
    const osMemory = dashboard.resources?.os_memory;
    $('metric-os-memory').textContent = osMemory?.total_kb
      ? `${((osMemory.total_kb - osMemory.avail_kb) / 1024 / 1024).toFixed(1)}/${(osMemory.total_kb / 1024 / 1024).toFixed(0)}GB`
      : '—';
    $('metric-os-available').textContent = osMemory?.avail_kb
      ? `available ${(osMemory.avail_kb / 1024 / 1024).toFixed(1)}GB`
      : '—';
    const cmm = dashboard.resources?.cmm;
    $('metric-cmm').textContent = cmm?.total_kb
      ? `${(cmm.used_kb / 1024 / 1024).toFixed(1)}/${(cmm.total_kb / 1024 / 1024).toFixed(0)}GB`
      : '—';
    $('metric-cmm-available').textContent = cmm?.total_kb
      ? `free ${((cmm.total_kb - cmm.used_kb) / 1024 / 1024).toFixed(1)}GB`
      : '—';
    const limitSelect = $('max-running-models');
    if (document.activeElement !== limitSelect) {
      limitSelect.value = String(dashboard.configured_model_limit || dashboard.config?.max_running_models || 1);
    }
    $('save-model-limit').disabled = !controlEnabled;
    renderDeviceResources(dashboard.resources || {});
    renderModels(models.models, dashboard.config || {});
    renderActiveApi(models.models);
    renderJobs(jobs.jobs);
    renderAudit(audit.records);
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
    context: Number($('context').value) || 4096,
    batch: Number($('batch').value) || 4096,
    ubatch: Number($('ubatch').value) || 4096,
    cache_ram: Number($('cache-ram').value) || 0,
    ctxcp: Number($('ctxcp').value) || 0,
    mtp: $('mtp').checked,
    sampling: currentSamplingPayload(),
  };
}

function stopModel(id) {
  control(`/api/v1/models/${id}/stop`, { timeout_seconds: 15 });
}

function applyLaunchProfile(name) {
  const profile = launchManifest?.profiles?.[name] || {};
  $('context').value = profile.context ?? 4096;
  $('batch').value = profile.batch ?? 4096;
  $('ubatch').value = profile.ubatch ?? 4096;
  $('cache-ram').value = profile.cache_ram ?? 0;
  $('ctxcp').value = profile.ctxcp ?? 0;
  $('mtp').checked = Boolean(profile.mtp);
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
    const result = await api(`/api/v1/jobs/${currentLogJob}/logs?lines=400`);
    content.textContent = result.path
      ? result.lines.join('\n') || '(empty log)'
      : 'This job has no runtime log. Open the associated start job to inspect llama-server output.';
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
$('theme-toggle').onclick = () => {
  applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
};
$('show-invalid').onchange = () => renderModels(modelRows);
$('save-model-limit').onclick = saveModelLimit;
$('profile').onchange = event => applyLaunchProfile(event.target.value);
$('launch-submit').onclick = submitLaunchDialog;
$('launch-save-profile').onclick = saveLaunchProfile;
$('launch-cancel').onclick = () => $('launch-dialog').close();
$('launch-force').onchange = event => {
  if (event.target.checked) {
    event.target.checked = confirm(
      'Start beyond the configured model-count limit? Confirm OS/CMM memory is sufficient.'
    );
  }
};
$('reload-log').onclick = () => loadLog({ force: true, follow: Boolean($('follow-log')?.checked) });
$('close-log').onclick = () => $('log-dialog').close();
$('auto-log').onchange = startLogPolling;
$('log-dialog').addEventListener('close', stopLogPolling);
tokenInput.addEventListener('keydown', event => { if (event.key === 'Enter') $('connect').click(); });

applyTheme(localStorage.getItem('axmm.theme'));

refresh();
scheduleRefresh();

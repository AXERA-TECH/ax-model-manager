'use strict';

const $ = (id) => document.getElementById(id);
const tokenInput = $('token');
let timer = null;
let currentLogJob = null;
let logTimer = null;
let logLoading = false;
let controlEnabled = false;
let modelRows = [];

tokenInput.value = localStorage.getItem('axmm.token') || '';

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

  const activeModel = models.find(model => model.active_job);
  const active = activeModel?.active_job;
  if (!active?.port) {
    statePill.className = 'pill';
    statePill.textContent = 'No model running';
    const message = document.createElement('p');
    message.className = 'message';
    message.style.margin = '0';
    message.textContent = 'Start a model to see its OpenAI-compatible API base URL, model ID, and request example.';
    container.append(message);
    return;
  }

  const state = active.state || 'unknown';
  statePill.className = `pill ${stateClass(state)}`;
  statePill.textContent = `${activeModel.name} · ${state}`;

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
    apiBox('OpenAI base URL', baseUrlNode, [
      copyButton(base, 'Copy base URL'),
      copyButton(chatUrl, 'Copy chat URL'),
    ]),
    apiBox('Model ID', modelNode, [copyButton(modelId, 'Copy model ID')]),
    apiBox('Chat request', requestNode, [copyButton(curl, 'Copy curl')]),
  );
}

function renderModels(models) {
  modelRows = models;
  const showInvalid = $('show-invalid').checked;
  const container = $('models');
  const anyActive = models.some(model => Boolean(model.active_job));
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
        start.textContent = 'Start';
        start.onclick = () => startModel(model.id);
        actions.append(start);
      }
      if (model.valid) {
        const saveProfile = document.createElement('button');
        saveProfile.className = 'small';
        saveProfile.textContent = 'Save options';
        saveProfile.title = 'Save the current start options to this model default profile';
        saveProfile.onclick = () => saveProfileForModel(model.id);
        actions.append(saveProfile);
      }
      if (!active && anyActive && model.ready_to_start) {
        const switchButton = document.createElement('button');
        switchButton.className = 'small';
        switchButton.textContent = 'Switch to';
        switchButton.onclick = () => switchModel(model.id);
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
        restart.textContent = 'Restart';
        restart.onclick = () => restartModel(model.id);
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
    $('metric-active').textContent = dashboard.active_job_count;
    renderModels(models.models);
    renderActiveApi(models.models);
    renderJobs(jobs.jobs);
    renderAudit(audit.records);
  } catch (error) {
    showAlert(error.message);
  }
}

async function control(path, body) {
  try {
    if (body && !confirm('Confirm this model control action?')) return;
    const result = await api(path, { method: 'POST', body: JSON.stringify(body || {}) });
    clearAlert();
    if (result?.job) await openLog(result.job.job_id);
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
    profile: 'default',
    context: Number($('context').value) || 4096,
    batch: Number($('batch').value) || 4096,
    ubatch: Number($('ubatch').value) || 4096,
    cache_ram: Number($('cache-ram').value) || 0,
    ctxcp: Number($('ctxcp').value) || 0,
    mtp: $('mtp').checked,
    sampling: currentSamplingPayload(),
  };
}

function startModel(id) {
  const payload = { ...currentLaunchPayload() };
  const port = Number($('port').value);
  if (port) payload.port = port;
  control(`/api/v1/models/${id}/start`, payload);
}

function stopModel(id) {
  control(`/api/v1/models/${id}/stop`, { timeout_seconds: 15 });
}

function switchModel(id) {
  const payload = {
    ...currentLaunchPayload(),
    stop_timeout_seconds: 20,
  };
  control(`/api/v1/models/${id}/switch-to`, payload);
}

function restartModel(id) {
  const payload = {
    ...currentLaunchPayload(),
    stop_timeout_seconds: 20,
  };
  control(`/api/v1/models/${id}/restart`, payload);
}

async function saveProfileForModel(id) {
  if (!confirm(`Save the current start options as the default profile for ${id}?`)) return;
  const payload = { ...currentLaunchPayload(), reasoning: 'off' };
  try {
    await api(`/api/v1/models/${id}/profiles/default`, {
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
    content.textContent = result.lines.join('\n') || '(empty log)';
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
$('show-invalid').onchange = () => renderModels(modelRows);
$('reload-log').onclick = () => loadLog({ force: true, follow: Boolean($('follow-log')?.checked) });
$('close-log').onclick = () => $('log-dialog').close();
$('auto-log').onchange = startLogPolling;
$('log-dialog').addEventListener('close', stopLogPolling);
tokenInput.addEventListener('keydown', event => { if (event.key === 'Enter') $('connect').click(); });

refresh();
scheduleRefresh();

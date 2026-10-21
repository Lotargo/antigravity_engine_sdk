'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="testbench-token"]').content;
const state = { models: [], job: null, posting: false, refreshing: false, file: null, preview: '', config: null, loading: true };
const statuses = { running: 'Запрос выполняется', cancelling: 'Отменяем запрос…', cancelled: 'Запрос отменён', completed: 'Готово', error: 'Ошибка запроса' };
const phases = { starting: 'Запускаем запрос…', working: 'Модель готовит ответ…', trajectory: 'Модель готовит ответ…' };
let polling = false;
let connectionLost = false;
let viewRevision = 0;
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...options.headers, 'X-Testbench-Token': token } });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || `HTTP ${response.status}`);
  return data;
}
function error(message = '') { $('error').textContent = message; $('error').hidden = !message; }
function active() { return state.posting || ['running', 'cancelling'].includes(state.job?.status); }
function controls() {
  const busy = active();
  const card = state.models.find(m => m.id === $('model').value);
  const incompatible = !!(state.file && card && !card.supports_images);
  $('model').disabled = busy || state.refreshing || !state.models.length;
  $('refresh').disabled = state.refreshing;
  $('prompt').disabled = busy;
  $('image').disabled = busy;
  $('choose-image').disabled = busy;
  $('remove-image').disabled = busy;
  $('send').disabled = busy || state.loading || state.refreshing || !$('prompt').value.trim() || !card || incompatible;
  $('cancel').disabled = !state.job || state.job.status !== 'running';
  $('send').textContent = busy ? 'Запрос выполняется…' : 'Отправить запрос';
  $('cancel').textContent = state.job?.status === 'cancelling' ? 'Отменяем…' : 'Отменить';
  if (card) $('model-hint').textContent = `${card.id} · ${card.supports_images ? 'Поддерживает изображения' : 'Только текст'}`;
  $('image-hint').textContent = incompatible ? 'Выберите модель с поддержкой изображений или уберите вложение.' : '';
  $('model').setAttribute('aria-invalid', String(incompatible));
}
async function loadModels() {
  state.refreshing = true; controls(); $('refresh').textContent = 'Загрузка…';
  try {
    const selected = $('model').value || state.job?.model || state.config.model;
    const models = await api('/api/models');
    state.models = models; $('model').replaceChildren();
    if (!models.length) $('model').add(new Option('Каталог пуст', ''));
    for (const m of models) $('model').add(new Option(`${m.name} — ${m.id}`, m.id));
    if (models.some(m => m.id === selected)) $('model').value = selected;
    if (!models.length) $('model-hint').textContent = 'Движок вернул пустой каталог. Попробуйте обновить.';
    error();
  } catch (e) {
    error(`Не удалось загрузить каталог: ${e.message}`);
    if (!state.models.length) { $('model').replaceChildren(new Option('Каталог недоступен', '')); $('model-hint').textContent = 'Проверьте вход в Antigravity и нажмите «Обновить».'; }
  } finally { state.refreshing = false; $('refresh').textContent = 'Обновить'; controls(); }
}
function renderJob(job) {
  state.job = job;
  if (!job) { controls(); return; }
  const busy = ['running', 'cancelling'].includes(job.status);
  const status = job.status === 'running' ? (phases[job.phase] || statuses.running) : (statuses[job.status] || job.status);
  if ($('status').textContent !== status) $('status').textContent = status;
  $('status').dataset.state = job.status;
  $('elapsed').textContent = `${(job.duration_ms / 1000).toFixed(1)} с`;
  $('answer').setAttribute('aria-busy', String(busy));
  const text = job.result?.output?.text;
  $('answer').classList.toggle('empty', !text);
  $('answer').textContent = text || (busy ? 'Ожидаем ответ модели…' : job.status === 'cancelled' ? 'Генерация остановлена. Можно отправить новый запрос.' : job.error ? `${job.error.code}: ${job.error.message}` : 'Ответ не получен.');
  $('diagnostics').hidden = busy;
  $('used-model').textContent = job.model;
  $('engine').textContent = job.result?.engine_version || '—';
  $('usage').textContent = job.result ? job.result.usage_scope : 'UNKNOWN';
  $('usage-json').textContent = job.result ? JSON.stringify(job.result.usage, null, 2) : 'Расход отменённого или незавершённого запроса неизвестен.';
  $('result-meta').textContent = [job.model, job.attachment ? `Вложение: ${job.attachment}` : ''].filter(Boolean).join(' · ');
  $('report-note').textContent = job.report_error ? `Отчёт не сохранён: ${job.report_error}` : job.report ? `Сохранён: ${job.report}` : '';
  $('download').disabled = !job.report;
  controls();
}
async function poll() {
  if (polling || state.posting) return; polling = true;
  const revision = viewRevision;
  try {
    const job = await api('/api/request');
    if (revision === viewRevision) renderJob(job);
    if (connectionLost) { connectionLost = false; error(); }
  }
  catch (e) { connectionLost = true; error(`Нет связи со стендом: ${e.message}. Повторяем подключение…`); }
  finally { polling = false; }
}
$('refresh').addEventListener('click', loadModels);
$('model').addEventListener('change', controls);
$('prompt').addEventListener('input', controls);
$('prompt').addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && !$('send').disabled) { e.preventDefault(); $('request-form').requestSubmit(); } });
$('choose-image').addEventListener('click', () => $('image').click());
function clearFile() {
  if (state.preview) URL.revokeObjectURL(state.preview);
  state.preview = ''; state.file = null; $('image').value = ''; $('preview').hidden = true; $('preview').removeAttribute('src');
  $('filename').textContent = 'PNG, JPEG, WebP или GIF'; $('filesize').textContent = 'Изображение будет отправлено вместе с промптом.';
  $('choose-image').hidden = false; $('remove-image').hidden = true; controls();
}
$('remove-image').addEventListener('click', clearFile);
$('image').addEventListener('change', () => {
  const file = $('image').files[0]; if (!file) return;
  if (!['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type) || !file.size || file.size > state.config.max_media_bytes) {
    clearFile(); error('Выберите PNG, JPEG, WebP или GIF размером до 20 MiB.'); return;
  }
  if (state.preview) URL.revokeObjectURL(state.preview);
  state.file = file; state.preview = URL.createObjectURL(file); $('preview').src = state.preview; $('preview').hidden = false;
  $('filename').textContent = file.name; $('filesize').textContent = `${(file.size / 1024).toFixed(1)} KiB`;
  $('choose-image').hidden = true; $('remove-image').hidden = false; error(); controls();
});
$('request-form').addEventListener('submit', async e => {
  e.preventDefault(); if ($('send').disabled) return;
  viewRevision++;
  state.posting = true; error(); controls();
  const data = new FormData(); data.set('model', $('model').value); data.set('prompt', $('prompt').value);
  if (state.file) data.set('image', state.file);
  try { renderJob(await api('/api/requests', { method: 'POST', body: data })); }
  catch (e) { error(e.message); }
  finally { state.posting = false; controls(); }
});
$('cancel').addEventListener('click', async () => {
  if (!state.job || state.job.status !== 'running') return;
  viewRevision++;
  $('cancel').disabled = true;
  try { renderJob(await api(`/api/requests/${state.job.id}/cancel`, { method: 'POST' })); }
  catch (e) { error(`Не удалось подтвердить отмену: ${e.message}`); controls(); }
});
$('download').addEventListener('click', async () => {
  try {
    const report = await api(`/api/requests/${state.job.id}/report`);
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `request-${state.job.id}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) { error(e.message); }
});
(async () => {
  try {
    state.config = await api('/api/config');
    $('mode').textContent = state.config.mode === 'live' ? 'Реальный движок' : 'Mock · без вызова модели';
    $('footer').textContent = `Локальный тестовый стенд · Go SDK ${state.config.version} · ${state.config.mode === 'live' ? 'Генерация запускается кнопкой «Отправить запрос»' : 'Ответы mock — тестовые данные'}`;
    renderJob(await api('/api/request'));
    if (state.job) $('prompt').value = state.job.prompt;
    await loadModels();
  } catch (e) { error(`Не удалось подключиться: ${e.message}`); }
  finally { state.loading = false; controls(); setInterval(poll, 600); }
})();

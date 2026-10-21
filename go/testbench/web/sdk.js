const select = document.getElementById('sdk');
const token = document.querySelector('meta[name="testbench-token"]').content;
let current, switching = true, busy = false;
const storageKey = 'antigravity-testbench-sdk';
function controls() { select.disabled = switching || busy || select.options.length < 2; }
function identify() {
  document.querySelectorAll('[data-sdk-label]').forEach(node => { node.textContent = current.label; });
  document.title = `Antigravity · ${current.label} SDK`;
  document.getElementById('sdk-status').textContent = `Выбран ${current.label} SDK ${current.version}.`;
}
export const sdkStand = {
  get id() { return current?.id; },
  get label() { return current?.label || ''; },
  get version() { return current?.version || ''; },
  setBusy(value) { busy = value; controls(); },
  async request(path, options = {}) {
    const response = await fetch(path, { ...options, headers: { ...options.headers, 'X-Testbench-Token': token, 'X-Testbench-SDK': current.id } });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error?.message || value.message || `HTTP ${response.status}`);
    return value;
  },
  async init(onChange, onError) {
    const response = await fetch('/api/backends', {headers: {'X-Testbench-Token': token}});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error?.message || 'Не удалось получить список SDK.');
    let preferred;
    try { preferred = sessionStorage.getItem(storageKey); } catch {}
    current = value.backends.find(item => item.id === preferred && item.available) || value.backends.find(item => item.id === value.default && item.available);
    if (!current) throw new Error('Нет доступного SDK.');
    select.replaceChildren();
    for (const item of value.backends) {
      const option = new Option(`${item.label} SDK${item.available ? '' : ' · недоступен'}`, item.id);
      option.disabled = !item.available;
      select.add(option);
    }
    select.value = current.id;
    const unavailable = value.backends.filter(item => !item.available);
    document.getElementById('sdk-help').textContent = unavailable.length ? unavailable.map(item => `${item.label}: ${item.reason}`).join(' ') : value.backends.length > 1 ? 'Код, промпт и вложение сохраняются при переключении. Сначала завершите или отмените текущий запрос.' : 'Отдельный Go-стенд. Для переключения запустите общий стенд из python/testbench.';
    identify();
    try { sessionStorage.setItem(storageKey, current.id); } catch {}
    select.addEventListener('change', async () => {
      if (busy || switching) { select.value = current.id; return; }
      const next = value.backends.find(item => item.id === select.value && item.available);
      if (!next || next.id === current.id) return;
      current = next; switching = true; controls(); identify();
      try { sessionStorage.setItem(storageKey, current.id); } catch {}
      try { await onChange(); } catch (error) { onError(error); }
      finally { switching = false; controls(); }
    });
    switching = false; controls();
  },
};

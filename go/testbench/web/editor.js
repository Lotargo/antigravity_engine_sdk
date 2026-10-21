'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="testbench-token"]').content;
let ready = false, busy = false, commandActive = false, latest = null, sentText = '', poll;
async function api(path, body) {
  const response = await fetch('/api/editor/' + path, {method: body === undefined ? 'GET' : 'POST', headers: {'X-Testbench-Token': token, 'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error?.message || value.message || JSON.stringify(value));
  return value;
}
function controls() {
  for (const id of ['example', 'setup', 'baseline', 'code']) $(id).disabled = busy;
  $('supercomplete').disabled = busy || !ready;
  $('jump').disabled = busy || !ready;
  $('cancel').disabled = !busy || !commandActive;
  const valid = !busy && latest && $('code').value === sentText;
  $('apply').disabled = !valid || latest.preview === undefined || latest.preview === sentText;
  $('move').disabled = !valid || !latest.result?.response?.jumpPosition || latest.result?.status !== 'jump';
}
function cursor() {
  const before = $('code').value.slice(0, $('code').selectionStart), lines = before.split('\n');
  $('cursor').textContent = `Курсор: строка ${lines.length}, столбец ${lines.at(-1).length + 1} · UTF-8 offset ${new TextEncoder().encode(before).length}`;
  controls();
}
function documentValue(text) {
  return {name:'sample.py', language:'python', text, cursor_offset: new TextEncoder().encode(text.slice(0,$('code').selectionStart)).length};
}
function failure(error) { $('error').textContent = error.message; busy = false; commandActive=false; controls(); }
$('example').onclick = async () => {
  try {const v = await api('example'); $('baseline').value = v.before; $('code').value = v.after; $('code').setSelectionRange(v.cursor,v.cursor); ready=false;latest=null;$('status').textContent='Пример загружен. Нажмите «Запомнить исходную версию».';cursor();} catch(e){failure(e);}
};
$('setup').onclick = async () => {
  busy=true;latest=null;controls();$('error').textContent='';$('status').textContent='Создаю сессию и историю исходного текста…';
  try {await api('setup',{name:'sample.py',language:'python',text:$('baseline').value,cursor_offset:0});ready=true;$('status').textContent='Сессия готова. Tab-to-Jump можно запускать отдельно, в любой момент.';busy=false;controls();} catch(e){failure(e);}
};
async function run(kind) {
  busy=true;commandActive=true;latest=null;sentText=$('code').value;controls();$('error').textContent='';$('preview').value='';$('diff').textContent='—';$('outcome').textContent='Ожидание нативного потока…';$('status').textContent='Запрос выполняется…';
  try {await api('run',{kind,document:documentValue(sentText)});poll=setInterval(refresh,300);await refresh();} catch(e){failure(e);}
}
async function refresh() {
  try {
    const v = await api('state');$('status').textContent=`${v.status} · кадров: ${v.frames}`;
    if (['running','cancelling'].includes(v.status)) return;
    clearInterval(poll);busy=false;commandActive=false;latest=v;
    $('error').textContent=v.error || v.report_error || '';
    $('preview').value=v.preview ?? '';
    const r=v.result?.response,j=r?.jumpPosition;
    $('outcome').textContent = r?.filterReason ? `Подсказка отфильтрована: ${r.filterReason.reason}` : j && v.result.status==='jump' ? `Переход: строка ${Number(j.row||0)+1}, столбец ${Number(j.col||0)+1}. Нативная позиция: row=${j.row||0}, col=${j.col||0}.` : v.result?.status==='empty' ? (v.result.preparation ? 'В этом запросе native не предложил переход.' : 'Supercomplete вернул пустой поток. Для него native учитывает недавние изменения.') : `Результат: ${v.result?.status || v.status}. ${v.preview_error || ''}`;
    $('diff').textContent=(r?.diff?.lines || []).map(line=>(line.type==='UNIFIED_DIFF_LINE_TYPE_INSERT'?'+ ':line.type==='UNIFIED_DIFF_LINE_TYPE_DELETE'?'- ':'  ')+(line.text || '')).join('\n') || '—';
    $('raw').textContent=JSON.stringify(v.result || v,null,2);$('report').textContent=v.report ? `Отчёт: ${v.report}` : '';controls();
  } catch(e){clearInterval(poll);failure(e);}
}
$('supercomplete').onclick=()=>run('supercomplete');$('jump').onclick=()=>run('tab_jump');
$('cancel').onclick=async()=>{try{await api('cancel',{});}catch(e){failure(e);}};
$('apply').onclick=()=>{if($('code').value!==sentText)return;$('code').value=latest.preview;latest=null;cursor();$('status').textContent='Правка применена в редакторе. Следующая команда запишет новый снимок.';};
$('move').onclick=()=>{if($('code').value!==sentText)return;const j=latest.result.response.jumpPosition,lines=sentText.split('\n'),row=Number(j.row||0),col=Number(j.col||0);if(row>=lines.length||col>lines[row].length){$('error').textContent='Позиция вне текущего текста.';return;}const offset=lines.slice(0,row).reduce((n,line)=>n+line.length+1,0)+col;$('code').focus();$('code').setSelectionRange(offset,offset);const lineHeight=parseFloat(getComputedStyle($('code')).lineHeight);$('code').scrollTop=Math.max(0,row*lineHeight-$('code').clientHeight/2);cursor();};
for(const event of ['input','click','keyup','select'])$('code').addEventListener(event,cursor);
controls();

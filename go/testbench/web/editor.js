'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="testbench-token"]').content;
let ready = false, busy = false, commandActive = false, hasDocument = false, latest = null, sentText = '', poll;
async function api(path, body) {
  const response = await fetch('/api/editor/' + path, {method: body === undefined ? 'GET' : 'POST', headers: {'X-Testbench-Token': token, 'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error?.message || value.message || JSON.stringify(value));
  return value;
}
function controls() {
  for (const id of ['code-file', 'language', 'example', 'completion-example', 'setup', 'baseline', 'code']) $(id).disabled = busy;
  $('supercomplete').disabled = busy || !ready;
  $('jump').disabled = busy || !ready;
  $('cancel').disabled = !busy || !commandActive;
  $('download').disabled = busy || !hasDocument;
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
  return {name:$('filename').value, language:$('language').value, text, cursor_offset: new TextEncoder().encode(text.slice(0,$('code').selectionStart)).length};
}
function failure(error) { $('error').textContent = error.message; busy = false; commandActive=false; controls(); }
function loadDocument(name, language, before, after, offset) {
  hasDocument=true;$('download-status').textContent='';
  $('filename').value=name; $('language').value=language;
  $('baseline').value=before; $('code').value=after; $('code').setSelectionRange(offset,offset);
  ready=false;latest=null;$('error').textContent='';$('preview').value='';$('diff').textContent='—';$('raw').textContent='';$('report').textContent='';
  $('outcome').textContent='Правка появится после завершения потока.';
  $('status').textContent=`Загружен ${name}. Нажмите «Запомнить исходную версию».`;cursor();
}
const fileLanguages = {py:'python',pyw:'python',go:'go',js:'javascript',mjs:'javascript',cjs:'javascript',jsx:'javascriptreact',ts:'typescript',mts:'typescript',cts:'typescript',tsx:'typescriptreact',rs:'rust',java:'java',c:'c',h:'c',cc:'cpp',cpp:'cpp',cxx:'cpp',hpp:'cpp',cs:'csharp',rb:'ruby',php:'php',swift:'swift',kt:'kotlin',kts:'kotlin',dart:'dart',html:'html',htm:'html',css:'css',scss:'scss',json:'json',yaml:'yaml',yml:'yaml',sql:'sql',sh:'shellscript',bash:'shellscript',zsh:'shellscript',ps1:'powershell',psm1:'powershell',md:'markdown'};
$('code-file').onchange = async () => {
  const file=$('code-file').files[0];if(!file)return;
  busy=true;controls();$('error').textContent='';$('code-file').removeAttribute('aria-invalid');
  try {
    if(file.size>4*1024*1024)throw new Error('Файл слишком большой. Максимальный размер — 4 МиБ.');
    let text;
    try {text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());}
    catch(e) {throw new Error('Не удалось прочитать файл как UTF-8. Сохраните текст в UTF-8 и выберите его снова.');}
    if(text.includes('\0'))throw new Error('В файле есть нулевые байты. Выберите текстовый файл кода UTF-8.');
    const extension=file.name.split('.').at(-1).toLowerCase();
    loadDocument(file.name,Object.hasOwn(fileLanguages,extension) ? fileLanguages[extension] : 'plaintext',text,text,0);
  } catch(e) {$('code-file').setAttribute('aria-invalid','true');failure(e);}
  finally {$('code-file').value='';busy=false;controls();}
};
$('language').onchange = () => {ready=false;latest=null;controls();$('status').textContent='Язык изменён. Нажмите «Запомнить исходную версию».';};
async function loadExample(kind) {
  busy=true;controls();
  try {const v = await api('example?kind=' + kind); loadDocument('sample.py','python',v.before,v.after,v.cursor);$('code-file').removeAttribute('aria-invalid');} catch(e){failure(e);}
  finally {busy=false;controls();}
}
$('example').onclick = () => loadExample('tab_jump');
$('completion-example').onclick = () => loadExample('supercomplete');
$('setup').onclick = async () => {
  busy=true;latest=null;controls();$('error').textContent='';$('status').textContent='Создаю сессию и историю исходного текста…';
  try {await api('setup',{...documentValue($('baseline').value),cursor_offset:0});ready=true;$('status').textContent='Сессия готова. Supercomplete и Tab-to-Jump можно запускать отдельно, в любой момент.';busy=false;controls();} catch(e){ready=false;failure(e);}
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
    $('outcome').textContent = r?.filterReason ? `Подсказка отфильтрована: ${r.filterReason.reason}` : j && v.result.status==='jump' ? `Переход: строка ${Number(j.row||0)+1}, столбец ${Number(j.col||0)+1}. Нативная позиция: row=${j.row||0}, col=${j.col||0}.` : v.result?.status==='empty' ? (v.result.kind === 'SUPERCOMPLETE' ? 'В этом запросе native не предложил правку.' : 'В этом запросе native не предложил переход.') : `Результат: ${v.result?.status || v.status}. ${v.preview_error || ''}`;
    $('diff').textContent=(r?.diff?.lines || []).map(line=>(line.type==='UNIFIED_DIFF_LINE_TYPE_INSERT'?'+ ':line.type==='UNIFIED_DIFF_LINE_TYPE_DELETE'?'- ':'  ')+(line.text || '')).join('\n') || '—';
    $('raw').textContent=JSON.stringify(v.result || v,null,2);$('report').textContent=v.report ? `Отчёт: ${v.report}` : '';controls();
  } catch(e){clearInterval(poll);failure(e);}
}
$('supercomplete').onclick=()=>run('supercomplete');$('jump').onclick=()=>run('tab_jump');
$('cancel').onclick=async()=>{try{await api('cancel',{});}catch(e){failure(e);}};
$('apply').onclick=()=>{if($('code').value!==sentText)return;$('code').value=latest.preview;latest=null;cursor();$('status').textContent='Правка применена в редакторе. Можно скачать текущий файл.';};
$('download').onclick=()=>{
  if(busy || !hasDocument)return;
  const url=URL.createObjectURL(new Blob([$('code').value],{type:'text/plain;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download=$('filename').value;
  document.body.appendChild(link);
  try {link.click();$('download-status').textContent=`Скачивание ${link.download} начато.`;}
  finally {link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
};
$('move').onclick=()=>{if($('code').value!==sentText)return;const j=latest.result.response.jumpPosition,lines=sentText.split('\n'),row=Number(j.row||0),col=Number(j.col||0);if(row>=lines.length||col>lines[row].length){$('error').textContent='Позиция вне текущего текста.';return;}const offset=lines.slice(0,row).reduce((n,line)=>n+line.length+1,0)+col;$('code').focus();$('code').setSelectionRange(offset,offset);const lineHeight=parseFloat(getComputedStyle($('code')).lineHeight);$('code').scrollTop=Math.max(0,row*lineHeight-$('code').clientHeight/2);cursor();};
for(const event of ['input','click','keyup','select'])$('code').addEventListener(event,cursor);
$('code').addEventListener('input',()=>{hasDocument=true;$('download-status').textContent='';controls();});
controls();

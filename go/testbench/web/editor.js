'use strict';
import {createCodeEditor} from './code-editor.js';
import {sdkStand} from './sdk.js';
const $ = id => document.getElementById(id);
const nonce = document.querySelector('meta[name="editor-style-nonce"]').content;
let ready = false, busy = true, commandActive = false, hasDocument = false, latest = null, sentText = '', poll, revision = 0, refreshing = false;
const sessions = new Map(), results = new Map();
function baselineKey() {return JSON.stringify([$('filename').value,$('language').value,editors.baseline.getText()]);}
const editors={};
editors.baseline=createCodeEditor({nonce,parent:$('baseline'),labelledBy:'baseline-label',describedBy:'baseline-help',onChange:()=>{ready=false;latest=null;controls();$('status').textContent='Исходная версия изменена. Нажмите «Запомнить исходную версию».';}});
editors.code=createCodeEditor({nonce,parent:$('code'),labelledBy:'code-label',describedBy:'editor-help cursor status',onChange:()=>{hasDocument=true;$('download-status').textContent='';$('status').textContent='Код изменён в редакторе. Можно скачать файл или запустить новую команду.';},onSelection:cursor});
editors.preview=createCodeEditor({nonce,parent:$('preview'),labelledBy:'preview-label',readOnly:true});
function setLanguage(language) {for(const editor of Object.values(editors))editor.setLanguage(language);}
async function api(path, body) {
  return sdkStand.request('/api/editor/' + path, {method: body === undefined ? 'GET' : 'POST', headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
}
function controls() {
  sdkStand.setBusy(busy);
  for (const id of ['code-file', 'language', 'example', 'completion-example', 'setup']) $(id).disabled = busy;
  editors.baseline.setLocked(busy);editors.code.setLocked(busy);
  $('supercomplete').disabled = busy || !ready;
  $('jump').disabled = busy || !ready;
  $('cancel').disabled = !busy || !commandActive;
  $('download').disabled = busy || !hasDocument;
  const valid = !busy && latest && editors.code.getText() === sentText;
  $('apply').disabled = !valid || latest.preview === undefined || latest.preview === sentText;
  $('move').disabled = !valid || !latest.result?.response?.jumpPosition || latest.result?.status !== 'jump';
}
function cursor() {
  const before = editors.code.getText().slice(0,editors.code.getOffset()), lines = before.split('\n');
  $('cursor').textContent = `Курсор: строка ${lines.length}, столбец ${lines.at(-1).length + 1} · UTF-8 offset ${new TextEncoder().encode(before).length}`;
  controls();
}
function documentValue(text) {
  return {name:$('filename').value, language:$('language').value, text, cursor_offset: new TextEncoder().encode(text.slice(0,editors.code.getOffset())).length};
}
function failure(error) { $('error').textContent = error.message; busy = false; commandActive=false; controls(); }
function loadDocument(name, language, before, after, offset) {
  sessions.clear(); results.clear();
  hasDocument=true;$('download-status').textContent='';
  $('filename').value=name; $('language').value=language;
  setLanguage(language);editors.baseline.setDocument(before);editors.code.setDocument(after,offset);
  ready=false;latest=null;$('error').textContent='';editors.preview.setDocument('');$('diff').textContent='—';$('raw').textContent='';$('report').textContent='';
  $('outcome').textContent='Правка появится после завершения потока.';
  $('result-sdk').textContent='';
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
$('language').onchange = () => {setLanguage($('language').value);editors.code.clearJump();ready=false;latest=null;controls();$('status').textContent='Язык изменён. Нажмите «Запомнить исходную версию».';};
async function loadExample(kind) {
  busy=true;controls();
  try {const v = await api('example?kind=' + kind); loadDocument('sample.py','python',v.before,v.after,v.cursor);$('code-file').removeAttribute('aria-invalid');} catch(e){failure(e);}
  finally {busy=false;controls();}
}
$('example').onclick = () => loadExample('tab_jump');
$('completion-example').onclick = () => loadExample('supercomplete');
$('setup').onclick = async () => {
  busy=true;latest=null;controls();$('error').textContent='';$('status').textContent='Создаю сессию и историю исходного текста…';
  try {await api('setup',{...documentValue(editors.baseline.getText()),cursor_offset:0});sessions.set(sdkStand.id,baselineKey());results.delete(sdkStand.id);ready=true;$('status').textContent=`Сессия ${sdkStand.label} готова. Supercomplete и Tab-to-Jump можно запускать отдельно, в любой момент.`;busy=false;controls();} catch(e){ready=false;sessions.delete(sdkStand.id);failure(e);}
};
async function run(kind) {
  revision++;
  busy=true;commandActive=true;latest=null;sentText=editors.code.getText();editors.code.clearJump();controls();$('error').textContent='';editors.preview.setDocument('');$('diff').textContent='—';$('outcome').textContent='Ожидание нативного потока…';$('status').textContent='Запрос выполняется…';
  try {await api('run',{kind,document:documentValue(sentText)});poll=setInterval(refresh,300);await refresh();} catch(e){failure(e);}
}
async function refresh() {
  if(refreshing)return;refreshing=true;const version=revision;
  try {
    const v = await api('state');if(version!==revision)return;$('status').textContent=`${v.status} · кадров: ${v.frames}`;
    if (['running','cancelling'].includes(v.status)) return;
    clearInterval(poll);busy=false;commandActive=false;latest=v;
    results.set(sdkStand.id,{latest:v,sentText});renderResult(v);controls();
  } catch(e){if(version===revision){clearInterval(poll);failure(e);}}
  finally {refreshing=false;}
}
function renderResult(v) {
    $('error').textContent=v.error || v.report_error || '';
    $('result-sdk').textContent=`${sdkStand.label} SDK ${sdkStand.version}${v.result ? ` · ${(v.result.latency_ms/1000).toFixed(2)} с · native ${v.result.engine_version}` : ''}`;
    editors.preview.setDocument(v.preview ?? '');
    const r=v.result?.response,j=r?.jumpPosition;
    $('outcome').textContent = r?.filterReason ? `Подсказка отфильтрована: ${r.filterReason.reason}` : j && v.result.status==='jump' ? `Переход: строка ${Number(j.row||0)+1}, столбец ${Number(j.col||0)+1}. Нативная позиция: row=${j.row||0}, col=${j.col||0}.` : v.result?.status==='empty' ? (v.result.kind === 'SUPERCOMPLETE' ? 'В этом запросе native не предложил правку.' : 'В этом запросе native не предложил переход.') : `Результат: ${v.result?.status || v.status}. ${v.preview_error || ''}`;
    $('diff').textContent=(r?.diff?.lines || []).map(line=>(line.type==='UNIFIED_DIFF_LINE_TYPE_INSERT'?'+ ':line.type==='UNIFIED_DIFF_LINE_TYPE_DELETE'?'- ':'  ')+(line.text || '')).join('\n') || '—';
    $('raw').textContent=JSON.stringify(v.result || v,null,2);$('report').textContent=v.report ? `Отчёт: ${v.report}` : '';
}
$('supercomplete').onclick=()=>run('supercomplete');$('jump').onclick=()=>run('tab_jump');
$('cancel').onclick=async()=>{try{await api('cancel',{});}catch(e){failure(e);}};
$('apply').onclick=()=>{if(editors.code.getText()!==sentText)return;editors.code.replaceText(latest.preview);latest=null;cursor();$('status').textContent='Правка применена в редакторе. Можно скачать текущий файл.';};
$('download').onclick=()=>{
  if(busy || !hasDocument)return;
  const url=URL.createObjectURL(new Blob([editors.code.getText()],{type:'text/plain;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download=$('filename').value;
  document.body.appendChild(link);
  try {link.click();$('download-status').textContent=`Скачивание ${link.download} начато.`;}
  finally {link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
};
$('move').onclick=()=>{
  if(editors.code.getText()!==sentText)return;
  const j=latest.result.response.jumpPosition,row=Number(j.row||0),col=Number(j.col||0);
  try {editors.code.jumpTo(row,col);cursor();$('status').textContent=`Курсор переведён: строка ${row+1}, столбец ${col+1}. Цель перехода выделена в коде.`;}
  catch(error){$('error').textContent=error.message;}
};
controls();
(async()=>{
  try {
    await sdkStand.init(()=>{
      revision++;clearInterval(poll);latest=null;sentText='';editors.code.clearJump();
      ready=sessions.get(sdkStand.id)===baselineKey();
      const cached=results.get(sdkStand.id);
      if(cached){latest=cached.latest;sentText=cached.sentText;renderResult(latest);}
      else {editors.preview.setDocument('');$('diff').textContent='—';$('raw').textContent='';$('report').textContent='';$('error').textContent='';$('result-sdk').textContent='';$('outcome').textContent='Для этого SDK результата ещё нет.';}
      $('status').textContent=ready ? `Сессия ${sdkStand.label} готова. Код сохранён.` : `Выбран ${sdkStand.label} SDK. Код сохранён. Нажмите «Запомнить исходную версию».`;
      controls();
    },failure);
    busy=false;controls();
  } catch(e){$('error').textContent=e.message;}
})();

import {Compartment, EditorState, StateEffect, StateField} from '@codemirror/state';
import {Decoration, EditorView, WidgetType, drawSelection, highlightActiveLine, highlightActiveLineGutter, highlightSpecialChars, keymap, lineNumbers} from '@codemirror/view';
import {defaultKeymap, history, historyKeymap} from '@codemirror/commands';
import {HighlightStyle, StreamLanguage, bracketMatching, indentOnInput, indentUnit, syntaxHighlighting} from '@codemirror/language';
import {highlightSelectionMatches, searchKeymap} from '@codemirror/search';
import {tags} from '@lezer/highlight';
import {python} from '@codemirror/lang-python';
import {javascript} from '@codemirror/lang-javascript';
import {go} from '@codemirror/lang-go';
import {rust} from '@codemirror/lang-rust';
import {cpp} from '@codemirror/lang-cpp';
import {java} from '@codemirror/lang-java';
import {html} from '@codemirror/lang-html';
import {css} from '@codemirror/lang-css';
import {json} from '@codemirror/lang-json';
import {markdown} from '@codemirror/lang-markdown';
import {php} from '@codemirror/lang-php';
import {sql} from '@codemirror/lang-sql';
import {yaml} from '@codemirror/lang-yaml';
import {ruby} from '@codemirror/legacy-modes/mode/ruby';
import {shell} from '@codemirror/legacy-modes/mode/shell';
import {powerShell} from '@codemirror/legacy-modes/mode/powershell';
import {swift} from '@codemirror/legacy-modes/mode/swift';
import {csharp, dart, kotlin} from '@codemirror/legacy-modes/mode/clike';
import {sCSS as scss} from '@codemirror/legacy-modes/mode/css';

const languages = {
  python: () => python(), go: () => go(), rust: () => rust(),
  javascript: () => javascript(), javascriptreact: () => javascript({jsx:true}),
  typescript: () => javascript({typescript:true}), typescriptreact: () => javascript({typescript:true,jsx:true}),
  c: () => cpp(), cpp: () => cpp(), java: () => java(), html: () => html(),
  css: () => css(), scss: () => StreamLanguage.define(scss), json: () => json(), markdown: () => markdown(),
  php: () => php(), sql: () => sql(), yaml: () => yaml(),
  ruby: () => StreamLanguage.define(ruby), shellscript: () => StreamLanguage.define(shell),
  powershell: () => StreamLanguage.define(powerShell), swift: () => StreamLanguage.define(swift),
  csharp: () => StreamLanguage.define(csharp), dart: () => StreamLanguage.define(dart), kotlin: () => StreamLanguage.define(kotlin),
};

const colors = HighlightStyle.define([
  {tag:tags.keyword,color:'#7b2c98'},
  {tag:[tags.string,tags.special(tags.string)],color:'#126145'},
  {tag:[tags.number,tags.bool,tags.null],color:'#a23c16'},
  {tag:tags.comment,color:'#58677b',fontStyle:'italic'},
  {tag:[tags.function(tags.variableName),tags.function(tags.propertyName)],color:'#1659a0'},
  {tag:[tags.typeName,tags.className],color:'#805209'},
  {tag:[tags.tagName,tags.attributeName],color:'#126145'},
  {tag:[tags.operator,tags.punctuation],color:'#45556a'},
]);

const markJump = StateEffect.define();
class JumpColumn extends WidgetType {
  toDOM() {
    const span=document.createElement('span');
    span.className='cm-jump-column';span.setAttribute('aria-hidden','true');
    return span;
  }
}
const jumpTarget = StateField.define({
  create: () => null,
  update(value,tr) {
    if(tr.docChanged || tr.selection)value=null;
    for(const effect of tr.effects)if(effect.is(markJump))value=effect.value;
    return value;
  },
  provide: field => EditorView.decorations.from(field,target => target === null ? Decoration.none : Decoration.set([
    Decoration.line({class:'cm-jump-target',attributes:{'data-jump-line':String(target.row+1)}}).range(target.from),
    Decoration.widget({widget:new JumpColumn(),side:1}).range(target.position),
  ],true)),
});

export function createCodeEditor({nonce,parent,labelledBy,describedBy='',readOnly=false,onChange,onSelection}) {
  const language=new Compartment(), access=new Compartment();
  let languageID='python',locked=false;
  function accessExtensions() {
    const ro=readOnly || locked;
    return [EditorState.readOnly.of(ro),EditorView.editable.of(!ro),
      EditorView.contentAttributes.of({'aria-labelledby':labelledBy,'aria-describedby':describedBy,'aria-readonly':String(ro),'aria-busy':String(locked),tabindex:'0',spellcheck:'false'})];
  }
  const languageSupport=() => Object.hasOwn(languages,languageID) ? languages[languageID]() : [];
  function state(doc='',offset=0) {
    return EditorState.create({doc,selection:{anchor:offset},extensions:[
      EditorView.cspNonce.of(nonce),lineNumbers(),highlightActiveLineGutter(),highlightSpecialChars(),drawSelection(),
      history(),indentUnit.of('    '),indentOnInput(),bracketMatching(),highlightActiveLine(),highlightSelectionMatches(),
      syntaxHighlighting(colors),EditorState.tabSize.of(4),jumpTarget,
      keymap.of([...defaultKeymap,...historyKeymap,...searchKeymap]),
      language.of(languageSupport()),access.of(accessExtensions()),
      EditorView.updateListener.of(update => {
        if(update.docChanged)onChange?.();
        if(update.docChanged || update.selectionSet)onSelection?.();
      }),
    ]});
  }
  const view=new EditorView({parent,state:state()});
  return {
    getText: () => view.state.doc.toString(),
    getOffset: () => view.state.selection.main.head,
    setDocument(text,offset=0) {
      // Loading another source is a new history, not an undoable edit to the old file.
      const doc=EditorState.create({doc:text}).doc;
      view.setState(state(doc,Math.min(offset,doc.length)));
    },
    replaceText(text) {
      view.dispatch({changes:{from:0,to:view.state.doc.length,insert:text},selection:{anchor:Math.min(view.state.selection.main.head,text.length)},userEvent:'input.apply'});
    },
    setLanguage(id) {
      if(id===languageID)return;
      languageID=id;view.dispatch({effects:language.reconfigure(languageSupport())});
    },
    setLocked(value) {
      if(value===locked)return;
      locked=value;view.dispatch({effects:access.reconfigure(accessExtensions())});
    },
    clearJump() {view.dispatch({effects:markJump.of(null)});},
    jumpTo(row,col) {
      if(!Number.isSafeInteger(row) || !Number.isSafeInteger(col) || row<0 || row>=view.state.doc.lines || col<0)throw new Error('Позиция вне текущего текста.');
      const line=view.state.doc.line(row+1);
      if(col>line.length || (col>0 && /[\uD800-\uDBFF]/.test(line.text[col-1]) && /[\uDC00-\uDFFF]/.test(line.text[col] || '')))throw new Error('Позиция вне границы символа текущего текста.');
      const position=line.from+col;
      view.dispatch({selection:{anchor:position},effects:[markJump.of({row,from:line.from,position}),EditorView.scrollIntoView(position,{y:'center',x:'nearest'})]});
      view.focus();
      view.dom.scrollIntoView({block:'nearest'});
    },
  };
}

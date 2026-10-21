package antigravity

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

// EditorDocument is an in-memory snapshot. Name is a basename, never a real
// project path. CursorOffset counts UTF-8 bytes, as required by the native RPC.
type EditorDocument struct {
	Name         string `json:"name"`
	Language     string `json:"language"`
	Text         string `json:"text"`
	CursorOffset int    `json:"cursor_offset"`
}

type EditorPosition struct {
	Row uint64 `json:"row,string"`
	Col uint64 `json:"col,string"`
}
type CharacterChange struct {
	Text string `json:"text,omitempty"`
	Type string `json:"type"`
}
type CharacterDiff struct {
	Changes []CharacterChange `json:"changes"`
}
type EditorResponse struct {
	CompletionID       string          `json:"completionId,omitempty"`
	PromptID           string          `json:"promptId,omitempty"`
	SelectionStartLine uint64          `json:"selectionStartLine,string,omitempty"`
	SelectionEndLine   uint64          `json:"selectionEndLine,string,omitempty"`
	CharacterDiff      *CharacterDiff  `json:"characterDiff,omitempty"`
	Diff               Object          `json:"diff,omitempty"`
	ComboDiff          Object          `json:"comboDiff,omitempty"`
	JumpPosition       *EditorPosition `json:"jumpPosition,omitempty"`
	FilterReason       *struct {
		Reason string `json:"reason"`
	} `json:"filterReason,omitempty"`
	RawText string `json:"rawText,omitempty"`
	TraceID string `json:"traceId,omitempty"`
}

// EditorResult retains each literal native data frame. Responses are cumulative
// snapshots, not patches to concatenate. Only the final response may be applied.
type EditorResult struct {
	Kind                 string             `json:"kind"`
	Status               string             `json:"status"` // edit, jump, filtered, empty, no_suggestion, incomplete
	Response             *EditorResponse    `json:"response,omitempty"`
	Frames               []json.RawMessage  `json:"frames"`
	Terminal             json.RawMessage    `json:"terminal,omitempty"`
	SourceSHA256         string             `json:"source_sha256"`
	EngineVersion        string             `json:"engine_version"`
	LatencyMS            int64              `json:"latency_ms"`
	Usage                string             `json:"usage"` // UNKNOWN: this RPC does not report token usage
	RequestedModel       string             `json:"requested_model"`
	RequestedNativeModel string             `json:"requested_native_model"`
	Preparation          *EditorPreparation `json:"preparation,omitempty"`
}

// EditorSnapshot records an actual snapshot supplied by Sync, never an invented
// edit. ObservedAt is retained when an unchanged document is synchronized again.
type EditorSnapshot struct {
	Document   EditorDocument `json:"document"`
	ObservedAt time.Time      `json:"observed_at"`
}
type EditorPreparation struct {
	Mode                 string            `json:"mode"`
	ReplayedAt           time.Time         `json:"replayed_at"`
	Snapshots            []EditorSnapshot  `json:"snapshots"`
	BootstrapFrames      []json.RawMessage `json:"bootstrap_frames"`
	BootstrapTerminal    json.RawMessage   `json:"bootstrap_terminal,omitempty"`
	BootstrapMS          int64             `json:"bootstrap_ms"`
	BootstrapDocument    Object            `json:"bootstrap_document,omitempty"`
	BootstrapNativeModel string            `json:"bootstrap_native_model,omitempty"`
}

// EditorSession owns a dedicated Client/process and synthetic workspace, keeping
// editor history separate from Chat/Complete and from the caller's real files.
type EditorSession struct {
	client         *Client
	mu             sync.Mutex
	document       EditorDocument
	uri, workspace string
	opened, primed bool
	sessionID      string
	history        []EditorSnapshot
}

func NewEditorSession(config Config) (*EditorSession, error) {
	c, err := NewClient(config)
	if err != nil {
		return nil, err
	}
	return &EditorSession{client: c, sessionID: hash(strconv.FormatInt(time.Now().UnixNano(), 10))}, nil
}

// Close interrupts any in-flight stream. It must not wait for the editor mutex.
func (s *EditorSession) Close() error { return s.client.Close() }

func validateEditorDocument(d EditorDocument) error {
	stem, _, _ := strings.Cut(strings.ToUpper(d.Name), ".")
	reserved := stem == "CON" || stem == "PRN" || stem == "AUX" || stem == "NUL" || (len(stem) == 4 && (strings.HasPrefix(stem, "COM") || strings.HasPrefix(stem, "LPT")) && stem[3] >= '0' && stem[3] <= '9')
	if d.Name == "" || d.Name == "." || d.Name == ".." || strings.TrimRight(d.Name, " .") != d.Name || reserved || strings.ContainsAny(d.Name, "/\\:<>\"|?*\x00") || filepath.Base(d.Name) != d.Name || d.Language == "" || len(d.Text) > 4*1024*1024 || !utf8.ValidString(d.Text) || d.CursorOffset < 0 || d.CursorOffset > len(d.Text) || !utf8.ValidString(d.Text[:max(0, min(d.CursorOffset, len(d.Text)))]) {
		return fault("invalid_input", "Editor requires a basename, language, valid UTF-8 text up to 4 MiB, and a cursor on a UTF-8 boundary.", nil)
	}
	return nil
}
func (s *EditorSession) nativeDocument(d EditorDocument) Object {
	return editorDocumentObject(s.uri, s.workspace, d)
}
func editorDocumentObject(uri, workspace string, d EditorDocument) Object {
	eol := "\n"
	if strings.Contains(d.Text, "\r\n") {
		eol = "\r\n"
	}
	return Object{"absoluteUri": uri, "workspaceUri": workspace, "text": d.Text, "editorLanguage": d.Language, "cursorOffset": strconv.Itoa(d.CursorOffset), "lineEnding": eol, "isDirty": true, "timestamp": time.Now().UTC().Format(time.RFC3339Nano)}
}

// Sync records the actual editor snapshot without generating a completion.
// The first snapshot becomes the baseline; subsequent snapshots build history.
func (s *EditorSession) Sync(parent context.Context, d EditorDocument) error {
	if err := validateEditorDocument(d); err != nil {
		return err
	}
	ctx, cancel, err := s.client.requestContext(parent)
	if err != nil {
		return err
	}
	defer cancel()
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.opened && d.Name != s.document.Name {
		return fault("invalid_input", "Use a new editor session for another document.", nil)
	}
	e, release, err := s.client.acquire(ctx, false)
	if err != nil {
		return err
	}
	defer release()
	// Losing the engine also loses native history: never silently continue it.
	if s.opened && s.workspace != fileURI(filepath.Join(e.root, "workspace")) {
		return fault("editor_not_ready", "Native editor history was lost; create a new session.", nil)
	}
	action := "IDE_ACTION_SELECTION_CHANGED"
	if !s.opened {
		root := filepath.Join(e.root, "workspace")
		s.workspace = fileURI(root)
		s.uri = fileURI(filepath.Join(root, d.Name))
		if err = os.WriteFile(filepath.Join(root, d.Name), []byte(d.Text), 0600); err != nil {
			return err
		}
		_, err = e.rpc(ctx, "AddTrackedWorkspace", Object{"workspace": s.workspace})
		if err != nil {
			s.client.checkFault(e, err)
			return err
		}
		action = "IDE_ACTION_STARTUP"
	}
	_, err = e.rpc(ctx, "RefreshContextForIdeAction", Object{"metadata": Object{"sessionId": s.sessionID}, "activeDocument": s.nativeDocument(d), "openDocumentUris": []string{s.uri}, "workspaceUris": []string{s.workspace}, "blocking": true, "ideAction": action})
	s.client.checkFault(e, err)
	if err == nil {
		if len(s.history) == 0 || s.history[len(s.history)-1].Document.Text != d.Text {
			s.history = append(s.history, EditorSnapshot{Document: d, ObservedAt: time.Now().UTC()})
			if len(s.history) > 2 {
				s.history = append([]EditorSnapshot(nil), s.history[len(s.history)-2:]...)
			}
		} else {
			s.history[len(s.history)-1].Document = d
		}
		s.document = d
		s.opened = true
	}
	return err
}

// Supercomplete is one real native model request. TYPING is essential: FORCED
// does not initialize granular history on native 2.24. No suggestion is applied.
func (s *EditorSession) Supercomplete(ctx context.Context, onFrame func(EditorResponse)) (*EditorResult, error) {
	return s.complete(ctx, "SUPERCOMPLETE", onFrame)
}

// TabToJump replays the two latest real snapshots in a fresh native context.
// It requires no previous Supercomplete and has no user-facing edit-age window.
// A zero-inference context bootstrap is supported on verified native 2.24.0.
func (s *EditorSession) TabToJump(ctx context.Context, onFrame func(EditorResponse)) (*EditorResult, error) {
	return s.replayTabToJump(ctx, onFrame)
}

// TabToJumpNative exposes the original automatic editor path. It requires a
// prior Supercomplete and recent edits; use TabToJump for an independent action.
func (s *EditorSession) TabToJumpNative(ctx context.Context, onFrame func(EditorResponse)) (*EditorResult, error) {
	return s.complete(ctx, "TAB_JUMP", onFrame)
}
func (s *EditorSession) complete(parent context.Context, kind string, onFrame func(EditorResponse)) (*EditorResult, error) {
	ctx, cancel, err := s.client.requestContext(parent)
	if err != nil {
		return nil, err
	}
	defer cancel()
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.opened || (kind == "TAB_JUMP" && !s.primed) {
		return nil, fault("editor_not_ready", "Sync snapshots and call Supercomplete before TabToJump.", nil)
	}
	e, release, err := s.client.acquire(ctx, false)
	if err != nil {
		return nil, err
	}
	defer release()
	if s.workspace != fileURI(filepath.Join(e.root, "workspace")) {
		return nil, fault("editor_not_ready", "Native editor history was lost; create a new session.", nil)
	}
	r := &EditorResult{Kind: kind, Status: "incomplete", Frames: []json.RawMessage{}, SourceSHA256: hash(s.document.Text), EngineVersion: e.installation.Version, Usage: "UNKNOWN"}
	modelID := "tab_flash_lite_preview"
	if kind == "TAB_JUMP" {
		modelID = "tab_jump_flash_lite_preview"
	}
	catalog, err := e.rpc(ctx, "GetAvailableModels", nil)
	if err != nil {
		s.client.checkFault(e, err)
		return r, err
	}
	nativeID := str(field(field(field(catalog, "response"), "models"), modelID), "model")
	if nativeID == "" {
		return r, fault("model_unavailable", "Editor preview model is absent from the native catalog.", nil)
	}
	r.RequestedModel = modelID
	r.RequestedNativeModel = nativeID
	payload := Object{"metadata": Object{"sessionId": s.sessionID}, "document": s.nativeDocument(s.document), "editorOptions": Object{"tabSize": "4", "insertSpaces": true}, "requestSource": "COMMAND_REQUEST_SOURCE_" + kind, "diffType": "DIFF_TYPE_TMP_" + kind, "supercompleteTriggerCondition": "SUPERCOMPLETE_TRIGGER_CONDITION_TYPING", "ignoreSupercompleteDebounce": true}
	payload["requestedModelId"] = nativeID
	started := time.Now()
	err = receiveEditorResult(ctx, e, payload, r, onFrame)
	r.LatencyMS = time.Since(started).Milliseconds()
	s.client.checkFault(e, err)
	if err != nil {
		return r, err
	}
	if kind == "SUPERCOMPLETE" && len(r.Frames) > 0 {
		s.primed = true
	}
	return r, nil
}
func receiveEditorResult(ctx context.Context, e *engine, payload Object, r *EditorResult, onFrame func(EditorResponse)) error {
	err := e.streamRPC(ctx, "HandleStreamingCommand", payload, func(raw json.RawMessage) error {
		r.Frames = append(r.Frames, append(json.RawMessage(nil), raw...))
		var response EditorResponse
		if err := json.Unmarshal(raw, &response); err != nil {
			return fault("incompatible_format", "Invalid native editor response.", err)
		}
		r.Response = &response
		if onFrame != nil {
			onFrame(response)
		}
		return nil
	}, &r.Terminal)
	if err != nil {
		return err
	}
	r.Status = "no_suggestion"
	switch {
	case r.Response == nil:
		r.Status = "empty"
	case r.Response.FilterReason != nil:
		r.Status = "filtered"
	case r.Response.JumpPosition != nil:
		r.Status = "jump"
	case hasEditorChanges(r.Response):
		r.Status = "edit"
	}
	return nil
}
func hasEditorChanges(r *EditorResponse) bool {
	if r.CharacterDiff != nil {
		for _, c := range r.CharacterDiff.Changes {
			if c.Type == "DIFF_CHANGE_TYPE_INSERT" || c.Type == "DIFF_CHANGE_TYPE_DELETE" {
				return true
			}
		}
	}
	return false
}

// Preview applies a final CharacterDiff only if the original text and selected
// source match exactly. It never writes files or repairs generated text.
func (r *EditorResult) Preview(text string) (string, error) {
	if r == nil || (r.Status != "edit" && r.Status != "jump") || r.Response == nil || r.Response.CharacterDiff == nil || r.SourceSHA256 != hash(text) {
		return "", fault("stale_edit", "No final applicable edit for this exact snapshot.", nil)
	}
	p := r.Response
	starts := []int{0}
	for i, c := range []byte(text) {
		if c == '\n' {
			starts = append(starts, i+1)
		}
	}
	start, end := p.SelectionStartLine, p.SelectionEndLine
	if end == start {
		end++
	}
	if start >= uint64(len(starts)) || end == 0 || end > uint64(len(starts)) {
		return "", fault("incompatible_format", "Native edit range is outside the document.", nil)
	}
	a := starts[start]
	b := len(text)
	if end < uint64(len(starts)) {
		b = starts[end] - 1
		if b > 0 && text[b-1] == '\r' {
			b--
		}
	}
	var old, new strings.Builder
	for _, c := range p.CharacterDiff.Changes {
		switch c.Type {
		case "DIFF_CHANGE_TYPE_UNCHANGED":
			old.WriteString(c.Text)
			new.WriteString(c.Text)
		case "DIFF_CHANGE_TYPE_DELETE":
			old.WriteString(c.Text)
		case "DIFF_CHANGE_TYPE_INSERT":
			new.WriteString(c.Text)
		default:
			return "", fault("incompatible_format", "Unknown character diff change type.", nil)
		}
	}
	if old.String() != text[a:b] {
		return "", fault("stale_edit", "Native diff source does not match the selected text.", nil)
	}
	return text[:a] + new.String() + text[b:], nil
}

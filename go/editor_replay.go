package antigravity

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

// Native 2.24.0's HandleTab builds granular history before testing its fixed
// 30s edit-age guard. An actual empty SDK sentinel with an epoch mtime takes
// that guard before any model request. User documents and their observed times
// remain separate; only genuine snapshots are then replayed into this context.
func (s *EditorSession) replayTabToJump(parent context.Context, onFrame func(EditorResponse)) (*EditorResult, error) {
	ctx, cancel, err := s.client.requestContext(parent)
	if err != nil {
		return nil, err
	}
	defer cancel()
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.opened {
		return nil, fault("editor_not_ready", "Sync a document before TabToJump.", nil)
	}
	r := &EditorResult{Kind: "TAB_JUMP", Status: "incomplete", Frames: []json.RawMessage{}, SourceSHA256: hash(s.document.Text), Usage: "UNKNOWN", RequestedModel: "tab_jump_flash_lite_preview", Preparation: &EditorPreparation{Mode: "snapshot_replay", ReplayedAt: time.Now().UTC(), Snapshots: append([]EditorSnapshot(nil), s.history...), BootstrapFrames: []json.RawMessage{}}}
	started := time.Now()
	defer func() { r.LatencyMS = time.Since(started).Milliseconds() }()
	child, err := NewClient(s.client.config)
	if err != nil {
		return r, err
	}
	defer child.Close()
	e, release, err := child.acquire(ctx, false)
	if err != nil {
		return r, err
	}
	defer release()
	r.EngineVersion = e.installation.Version
	// Fail closed on unverified engines: a future native may change the order of
	// its bootstrap/filter and silently turn initialization into model inference.
	if e.installation.Version != "2.24.0" {
		return r, fault("unsupported_editor_version", "Independent TabToJump is verified on native 2.24.0; no bootstrap was sent.", nil)
	}
	root := filepath.Join(e.root, "workspace")
	workspace := fileURI(root)
	f, err := os.CreateTemp(root, ".sdk-context-*.py")
	if err != nil {
		return r, err
	}
	primePath := f.Name()
	if err = f.Close(); err != nil {
		return r, err
	}
	epoch := time.Unix(0, 0).UTC()
	if err = os.Chtimes(primePath, epoch, epoch); err != nil {
		return r, err
	}
	prime := editorDocumentObject(fileURI(primePath), workspace, EditorDocument{Name: filepath.Base(primePath), Language: "python"})
	prime["timestamp"] = epoch.Format(time.RFC3339Nano)
	prime["isDirty"] = false
	r.Preparation.BootstrapDocument = prime
	if _, err = e.rpc(ctx, "AddTrackedWorkspace", Object{"workspace": workspace}); err != nil {
		return r, err
	}
	refresh := func(doc Object, action string) error {
		_, err := e.rpc(ctx, "RefreshContextForIdeAction", Object{"metadata": Object{"sessionId": s.sessionID}, "activeDocument": doc, "openDocumentUris": []string{str(doc, "absoluteUri")}, "workspaceUris": []string{workspace}, "blocking": true, "ideAction": "IDE_ACTION_" + action})
		return err
	}
	if err = refresh(prime, "STARTUP"); err != nil {
		return r, err
	}
	catalog, err := e.rpc(ctx, "GetAvailableModels", nil)
	if err != nil {
		return r, err
	}
	cards := field(field(catalog, "response"), "models")
	bootstrapModel := str(field(cards, "tab_flash_lite_preview"), "model")
	r.Preparation.BootstrapNativeModel = bootstrapModel
	r.RequestedNativeModel = str(field(cards, r.RequestedModel), "model")
	if bootstrapModel == "" || r.RequestedNativeModel == "" {
		return r, fault("model_unavailable", "Native editor preview models are absent from the catalog.", nil)
	}
	payload := func(doc Object, source, model string) Object {
		return Object{"metadata": Object{"sessionId": s.sessionID}, "document": doc, "requestedModelId": model, "editorOptions": Object{"tabSize": "4", "insertSpaces": true}, "requestSource": "COMMAND_REQUEST_SOURCE_" + source, "diffType": "DIFF_TYPE_TMP_" + source, "supercompleteTriggerCondition": "SUPERCOMPLETE_TRIGGER_CONDITION_TYPING", "ignoreSupercompleteDebounce": true}
	}
	prepStarted := time.Now()
	err = e.streamRPC(ctx, "HandleStreamingCommand", payload(prime, "SUPERCOMPLETE", bootstrapModel), func(raw json.RawMessage) error {
		r.Preparation.BootstrapFrames = append(r.Preparation.BootstrapFrames, append(json.RawMessage(nil), raw...))
		return fault("incompatible_format", "Context bootstrap unexpectedly returned a data frame; no Tab-to-Jump was sent.", nil)
	}, &r.Preparation.BootstrapTerminal)
	r.Preparation.BootstrapMS = time.Since(prepStarted).Milliseconds()
	if err != nil {
		return r, err
	}
	uri := fileURI(filepath.Join(root, s.document.Name))
	if err = os.WriteFile(filepath.Join(root, s.document.Name), []byte(s.history[0].Document.Text), 0600); err != nil {
		return r, err
	}
	r.Preparation.ReplayedAt = time.Now().UTC()
	for _, snapshot := range s.history {
		// Native timestamps here describe context loading, not historical user
		// edit times. The original times are retained in Preparation.Snapshots.
		if err = refresh(editorDocumentObject(uri, workspace, snapshot.Document), "ACTIVE_EDITOR_CHANGED"); err != nil {
			return r, err
		}
	}
	err = receiveEditorResult(ctx, e, payload(editorDocumentObject(uri, workspace, s.document), "TAB_JUMP", r.RequestedNativeModel), r, onFrame)
	return r, err
}

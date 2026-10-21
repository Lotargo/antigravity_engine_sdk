package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"

	sdk "github.com/Lotargo/antigravity_engine_sdk/go"
)

type editorView struct {
	ID           string              `json:"id"`
	Status       string              `json:"status"`
	Frames       int                 `json:"frames"`
	Last         *sdk.EditorResponse `json:"last,omitempty"`
	Result       *sdk.EditorResult   `json:"result,omitempty"`
	Preview      *string             `json:"preview,omitempty"`
	Error        string              `json:"error,omitempty"`
	PreviewError string              `json:"preview_error,omitempty"`
	Report       string              `json:"report,omitempty"`
	ReportError  string              `json:"report_error,omitempty"`
}
type editorStand struct {
	ctx      context.Context
	config   sdk.Config
	output   string
	mu       sync.Mutex
	session  *sdk.EditorSession
	busy     bool
	baseline string
	view     editorView
	cancel   context.CancelFunc
	wg       sync.WaitGroup
}

func (e *editorStand) close() {
	e.mu.Lock()
	s := e.session
	if e.cancel != nil {
		e.cancel()
	}
	e.mu.Unlock()
	if s != nil {
		_ = s.Close()
	}
	e.wg.Wait()
}
func readEditorJSON(w http.ResponseWriter, r *http.Request, v any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, 5*1024*1024)
	if err := json.NewDecoder(r.Body).Decode(v); err != nil {
		apiError(w, 400, fmt.Errorf("Invalid editor request"))
		return false
	}
	return true
}
func (e *editorStand) routes(mux *http.ServeMux) {
	mux.HandleFunc("GET /api/editor/example", func(w http.ResponseWriter, r *http.Request) {
		before, after, cursor := editorExample()
		if r.URL.Query().Get("kind") == "supercomplete" {
			before, after, cursor = completionExample()
		}
		writeJSON(w, 200, sdk.Object{"before": before, "after": after, "cursor": cursor})
	})
	mux.HandleFunc("GET /api/editor/state", func(w http.ResponseWriter, r *http.Request) {
		e.mu.Lock()
		defer e.mu.Unlock()
		writeJSON(w, 200, e.view)
	})
	mux.HandleFunc("POST /api/editor/setup", func(w http.ResponseWriter, r *http.Request) {
		var d sdk.EditorDocument
		if !readEditorJSON(w, r, &d) {
			return
		}
		e.mu.Lock()
		if e.busy {
			e.mu.Unlock()
			apiError(w, 409, fmt.Errorf("Editor is busy"))
			return
		}
		e.busy = true
		e.mu.Unlock()
		s, err := sdk.NewEditorSession(e.config)
		if err == nil {
			err = s.Sync(r.Context(), d)
		}
		e.mu.Lock()
		old := e.session
		e.busy = false
		if err == nil {
			e.session = s
			e.baseline = d.Text
			e.view = editorView{Status: "ready"}
		}
		e.mu.Unlock()
		if err != nil {
			if s != nil {
				_ = s.Close()
			}
			apiError(w, 502, err)
			return
		}
		if old != nil {
			_ = old.Close()
		}
		writeJSON(w, 200, sdk.Object{"status": "ready"})
	})
	mux.HandleFunc("POST /api/editor/run", func(w http.ResponseWriter, r *http.Request) {
		var p struct {
			Kind     string             `json:"kind"`
			Document sdk.EditorDocument `json:"document"`
		}
		if !readEditorJSON(w, r, &p) {
			return
		}
		if p.Kind != "supercomplete" && p.Kind != "tab_jump" {
			apiError(w, 400, fmt.Errorf("Unknown editor command"))
			return
		}
		e.mu.Lock()
		if e.busy || e.session == nil {
			e.mu.Unlock()
			apiError(w, 409, fmt.Errorf("Initialize the editor and wait for the current command"))
			return
		}
		s := e.session
		baseline := e.baseline
		ctx, cancel := context.WithCancel(e.ctx)
		e.cancel = cancel
		e.busy = true
		id := randomID()
		e.view = editorView{ID: id, Status: "running"}
		e.wg.Add(1)
		e.mu.Unlock()
		go func() {
			defer e.wg.Done()
			defer cancel()
			err := s.Sync(ctx, p.Document)
			var result *sdk.EditorResult
			if err == nil {
				frame := func(response sdk.EditorResponse) {
					e.mu.Lock()
					e.view.Frames++
					e.view.Last = &response
					e.mu.Unlock()
				}
				if p.Kind == "supercomplete" {
					result, err = s.Supercomplete(ctx, frame)
				} else {
					result, err = s.TabToJump(ctx, frame)
				}
			}
			e.mu.Lock()
			e.view.Result = result
			e.view.Status = "completed"
			if err != nil {
				e.view.Status = "error"
				e.view.Error = err.Error()
				if sdk.ErrorCode(err) == "cancelled" {
					e.view.Status = "cancelled"
				}
			}
			if err == nil && result != nil && (result.Status == "edit" || result.Status == "jump") && result.Response.CharacterDiff != nil && len(result.Response.CharacterDiff.Changes) > 0 {
				preview, previewErr := result.Preview(p.Document.Text)
				if previewErr == nil {
					e.view.Preview = &preview
				} else {
					e.view.PreviewError = previewErr.Error()
				}
			}
			report := sdk.Object{"date": time.Now().Format("2006-01-02"), "sdk_version": sdk.Version, "baseline": baseline, "document": p.Document, "request_kind": p.Kind, "view": e.view, "usage": "UNKNOWN", "model_selection": "native editor configuration"}
			data, saveErr := json.MarshalIndent(report, "", "  ")
			path := filepath.Join(e.output, "editor-"+id+".json")
			if saveErr == nil {
				saveErr = os.WriteFile(path, data, 0600)
			}
			if saveErr == nil {
				e.view.Report = path
			} else {
				e.view.ReportError = saveErr.Error()
			}
			e.busy = false
			e.cancel = nil
			e.mu.Unlock()
		}()
		writeJSON(w, 202, sdk.Object{"id": id})
	})
	mux.HandleFunc("POST /api/editor/cancel", func(w http.ResponseWriter, r *http.Request) {
		e.mu.Lock()
		defer e.mu.Unlock()
		if e.cancel != nil {
			e.cancel()
			e.view.Status = "cancelling"
		}
		writeJSON(w, 200, sdk.Object{"status": e.view.Status})
	})
}

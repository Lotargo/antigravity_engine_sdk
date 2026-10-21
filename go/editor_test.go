package antigravity

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func connectFrame(w io.Writer, flags byte, body string) {
	header := make([]byte, 5)
	header[0] = flags
	binary.BigEndian.PutUint32(header[1:], uint32(len(body)))
	_, _ = w.Write(header)
	_, _ = io.WriteString(w, body)
}
func editorFixture(t *testing.T, handler http.HandlerFunc) *EditorSession {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "GetAvailableModels") {
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"response":{"models":{"tab_flash_lite_preview":{"model":"MODEL_TAB"},"tab_jump_flash_lite_preview":{"model":"MODEL_JUMP"}}}}`)
			return
		}
		handler(w, r)
	}))
	c, err := NewClient(Config{Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	port, _ := strconv.Atoi(strings.TrimPrefix(server.URL, "http://127.0.0.1:"))
	e := newEngine(Installation{Version: "test"})
	e.root = t.TempDir()
	e.port = port
	e.ready = true
	e.done = make(chan struct{})
	// Sync uses the owned workspace in exactly the same way as the native engine.
	if err := os.Mkdir(filepath.Join(e.root, "workspace"), 0700); err != nil {
		t.Fatal(err)
	}
	c.engine = e
	s := &EditorSession{client: c, sessionID: "test-editor"}
	t.Cleanup(func() { close(e.done); _ = s.Close(); server.Close() })
	return s
}
func TestEditorSessionProtocol(t *testing.T) {
	var snapshots []string
	streamCount := 0
	s := editorFixture(t, func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "HandleStreamingCommand") {
			streamCount++
			var header [5]byte
			_, _ = io.ReadFull(r.Body, header[:])
			raw, _ := io.ReadAll(r.Body)
			if int(binary.BigEndian.Uint32(header[1:])) != len(raw) || header[0] != 0 || r.Header.Get("Content-Type") != "application/connect+json" {
				t.Error("invalid transport")
			}
			var p Object
			_ = json.Unmarshal(raw, &p)
			if str(p, "supercompleteTriggerCondition") != "SUPERCOMPLETE_TRIGGER_CONDITION_TYPING" {
				t.Error("missing native history bootstrap")
			}
			wantModel := "MODEL_TAB"
			if streamCount == 2 {
				wantModel = "MODEL_JUMP"
			}
			if str(p, "requestedModelId") != wantModel {
				t.Error("editor model must come from current native catalog")
			}
			w.Header().Set("Content-Type", "application/connect+json")
			connectFrame(w, 0, `{"completionId":"id","characterDiff":{"changes":[{"text":"a","type":"DIFF_CHANGE_TYPE_UNCHANGED"},{"text":"b","type":"DIFF_CHANGE_TYPE_INSERT"}]},"selectionEndLine":"1"}`)
			if streamCount == 2 {
				connectFrame(w, 0, `{"jumpPosition":{"row":"0","col":"0"},"characterDiff":{"changes":[{"text":"a","type":"DIFF_CHANGE_TYPE_UNCHANGED"},{"text":"c","type":"DIFF_CHANGE_TYPE_INSERT"}]},"selectionEndLine":"1"}`)
			}
			connectFrame(w, 2, `{}`)
			return
		}
		var p Object
		_ = json.NewDecoder(r.Body).Decode(&p)
		if strings.HasSuffix(r.URL.Path, "RefreshContextForIdeAction") {
			snapshots = append(snapshots, str(field(p, "activeDocument"), "text"))
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{}`)
	})
	ctx := context.Background()
	_, err := s.TabToJump(ctx, nil)
	requireCode(t, err, "editor_not_ready")
	for _, text := range []string{"old", "a"} {
		if err = s.Sync(ctx, EditorDocument{Name: "test.py", Language: "python", Text: text, CursorOffset: len(text)}); err != nil {
			t.Fatal(err)
		}
	}
	if strings.Join(snapshots, ",") != "old,a" || streamCount != 0 {
		t.Fatal("Sync must only record real snapshots")
	}
	r, err := s.Supercomplete(ctx, nil)
	if err != nil || r.Status != "edit" {
		t.Fatalf("%+v %v", r, err)
	}
	p, err := r.Preview("a")
	if err != nil || p != "ab" {
		t.Fatal(p, err)
	}
	r, err = s.TabToJump(ctx, nil)
	if err != nil || r.Status != "jump" || r.Response.JumpPosition.Row != 0 {
		t.Fatalf("%+v %v", r, err)
	}
	p, err = r.Preview("a")
	if err != nil || p != "ac" {
		t.Fatal("frames must replace, not concatenate", p, err)
	}
	_, err = r.Preview("changed")
	requireCode(t, err, "stale_edit")
	if r.Usage != "UNKNOWN" || len(r.Frames) != 2 || string(r.Terminal) != "{}" {
		t.Fatal("lost native evidence")
	}
}
func TestEditorStreamFailuresAndFiltering(t *testing.T) {
	for _, tc := range []struct {
		name, code, status string
		send               func(http.ResponseWriter, *http.Request)
	}{
		{"empty", "", "empty", func(w http.ResponseWriter, r *http.Request) { connectFrame(w, 2, `{}`) }},
		{"filtered", "", "filtered", func(w http.ResponseWriter, r *http.Request) {
			connectFrame(w, 0, `{"filterReason":{"reason":"edit is no-op"}}`)
			connectFrame(w, 2, `{}`)
		}},
		{"terminal_error", "provider_error", "incomplete", func(w http.ResponseWriter, r *http.Request) {
			connectFrame(w, 0, `{"jumpPosition":{"row":"1"}}`)
			connectFrame(w, 2, `{"error":{"code":"unavailable"}}`)
		}},
		{"truncated", "incompatible_format", "incomplete", func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte{0, 0}) }},
		{"compressed", "incompatible_format", "incomplete", func(w http.ResponseWriter, r *http.Request) { connectFrame(w, 1, `{}`) }},
		{"cancelled", "cancelled", "incomplete", func(w http.ResponseWriter, r *http.Request) {
			connectFrame(w, 0, `{}`)
			w.(http.Flusher).Flush()
			<-r.Context().Done()
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := editorFixture(t, func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/connect+json")
				tc.send(w, r)
			})
			s.opened = true
			s.uri = "file:///test.py"
			s.workspace = fileURI(filepath.Join(s.client.engine.root, "workspace"))
			s.document = EditorDocument{Name: "test.py", Language: "python", Text: "a", CursorOffset: 1}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			r, err := s.Supercomplete(ctx, func(EditorResponse) {
				if tc.name == "cancelled" {
					cancel()
				}
			})
			if ErrorCode(err) != tc.code || r.Status != tc.status {
				t.Fatalf("status=%s err=%v", r.Status, err)
			}
			if tc.code != "" {
				if _, e := r.Preview("a"); ErrorCode(e) != "stale_edit" {
					t.Fatal("partial stream became applicable")
				}
			}
		})
	}
}
func TestEditorPreviewUnicodeCRLF(t *testing.T) {
	text := "# пример\r\n    return α\r\n# end\r\n"
	r := &EditorResult{Status: "edit", SourceSHA256: hash(text), Response: &EditorResponse{SelectionStartLine: 1, SelectionEndLine: 2, CharacterDiff: &CharacterDiff{Changes: []CharacterChange{{Text: "    return ", Type: "DIFF_CHANGE_TYPE_UNCHANGED"}, {Text: "α", Type: "DIFF_CHANGE_TYPE_DELETE"}, {Text: "β", Type: "DIFF_CHANGE_TYPE_INSERT"}}}}}
	p, err := r.Preview(text)
	if err != nil || p != strings.Replace(text, "α", "β", 1) {
		t.Fatal(p, err)
	}
	r.Response.CharacterDiff.Changes[1].Text = "different"
	_, err = r.Preview(text)
	requireCode(t, err, "stale_edit")
	for _, d := range []EditorDocument{{Name: "../x.py", Language: "python"}, {Name: "x.py", Language: "python", Text: "α", CursorOffset: 1}, {Name: "x.py", Language: "python", Text: "a", CursorOffset: 2}} {
		requireCode(t, validateEditorDocument(d), "invalid_input")
	}
}

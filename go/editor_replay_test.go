package antigravity

import (
	"context"
	"encoding/json"
	"testing"
	"time"
)

func replayFixture(t *testing.T, scenarioName string) *EditorSession {
	t.Helper()
	t.Setenv("ANTIGRAVITY_GO_MOCK_SCENARIO", scenarioName)
	c := fixture(t)
	c.config.Installation.Version = "2.24.0"
	return &EditorSession{client: c, sessionID: "manual-test"}
}

func TestIndependentSupercomplete(t *testing.T) {
	for _, scenario := range []string{"editor_success", "editor_empty", "editor_filtered", "editor_bootstrap_data", "editor_running"} {
		t.Run(scenario, func(t *testing.T) {
			s := replayFixture(t, scenario)
			ctx := context.Background()
			for _, text := range []string{"old", "new"} {
				if err := s.Sync(ctx, EditorDocument{Name: "test.py", Language: "python", Text: text}); err != nil {
					t.Fatal(err)
				}
			}
			observed := time.Now().Add(-time.Hour)
			s.history[1].ObservedAt = observed
			request, cancel := context.WithCancel(ctx)
			defer cancel()
			r, err := s.Supercomplete(request, func(EditorResponse) {
				if scenario == "editor_running" {
					cancel()
				}
			})
			if scenario == "editor_bootstrap_data" {
				requireCode(t, err, "incompatible_format")
				if len(r.Frames) != 0 || len(r.Preparation.BootstrapFrames) != 1 {
					t.Fatal(r)
				}
				return
			}
			if scenario == "editor_running" {
				requireCode(t, err, "cancelled")
				if r.Status != "incomplete" {
					t.Fatal(r)
				}
				if _, err = r.Preview("new"); ErrorCode(err) != "stale_edit" {
					t.Fatal(err)
				}
				t.Setenv("ANTIGRAVITY_GO_MOCK_SCENARIO", "editor_success")
				r, err = s.Supercomplete(ctx, nil)
			}
			if err != nil {
				t.Fatal(err)
			}
			if r.Kind != "SUPERCOMPLETE" || r.RequestedNativeModel != "MODEL_TAB" || s.primed || !r.Preparation.Snapshots[1].ObservedAt.Equal(observed) || len(r.Preparation.BootstrapFrames) != 0 {
				t.Fatal(r)
			}
			if scenario == "editor_empty" {
				if r.Status != "empty" {
					t.Fatal(r)
				}
				return
			}
			if scenario == "editor_filtered" {
				if r.Status != "filtered" {
					t.Fatal(r)
				}
				return
			}
			if r.Status != "edit" || r.Response.JumpPosition != nil {
				t.Fatal(r)
			}
			var raw Object
			_ = json.Unmarshal(r.Frames[0], &raw)
			calls := field(field(raw, "requestInfo"), "testGenerationCalls")
			if calls["supercomplete"] != float64(1) || calls["tab_jump"] != float64(0) {
				t.Fatal("unexpected generation", calls)
			}
			preview, err := r.Preview("new")
			if err != nil || preview != "new!" {
				t.Fatal(preview, err)
			}
		})
	}
	t.Run("no_snapshot", func(t *testing.T) {
		s := replayFixture(t, "editor_success")
		_, err := s.Supercomplete(context.Background(), nil)
		requireCode(t, err, "editor_not_ready")
	})
	t.Run("single_snapshot", func(t *testing.T) {
		s := replayFixture(t, "editor_success")
		if err := s.Sync(context.Background(), EditorDocument{Name: "test.py", Language: "python", Text: "current"}); err != nil {
			t.Fatal(err)
		}
		r, err := s.Supercomplete(context.Background(), nil)
		if err != nil || r.Status != "edit" || len(r.Preparation.Snapshots) != 1 {
			t.Fatal(r, err)
		}
	})
	t.Run("version", func(t *testing.T) {
		s := replayFixture(t, "editor_success")
		s.client.config.Installation.Version = "2.23.0"
		if err := s.Sync(context.Background(), EditorDocument{Name: "test.py", Language: "python", Text: "current"}); err != nil {
			t.Fatal(err)
		}
		r, err := s.Supercomplete(context.Background(), nil)
		requireCode(t, err, "unsupported_editor_version")
		if r.Preparation.BootstrapTerminal != nil {
			t.Fatal("unexpected bootstrap")
		}
	})
}
func TestIndependentTabReplayWithoutSupercompleteOrRecentEdit(t *testing.T) {
	s := replayFixture(t, "editor_success")
	ctx := context.Background()
	for _, text := range []string{"old", "new"} {
		if err := s.Sync(ctx, EditorDocument{Name: "test.py", Language: "python", Text: text, CursorOffset: 1}); err != nil {
			t.Fatal(err)
		}
	}
	// An old observation must remain old in evidence while context loading uses
	// the present time. No user-history timestamp is rewritten by TabToJump.
	s.history[0].ObservedAt = time.Now().Add(-2 * time.Hour)
	s.history[1].ObservedAt = time.Now().Add(-time.Hour)
	when := s.history[1].ObservedAt
	count := 0
	r, err := s.TabToJump(ctx, func(EditorResponse) { count++ })
	if err != nil || r.Status != "jump" || count != 1 || s.primed {
		t.Fatalf("%+v %v callbacks=%d", r, err, count)
	}
	if r.Preparation.Mode != "snapshot_replay" || len(r.Preparation.BootstrapFrames) != 0 || string(r.Preparation.BootstrapTerminal) != "{}" || !r.Preparation.Snapshots[1].ObservedAt.Equal(when) {
		t.Fatal("lost bootstrap/history provenance", r.Preparation)
	}
	if str(r.Preparation.BootstrapDocument, "timestamp") != "1970-01-01T00:00:00Z" || str(r.Preparation.BootstrapDocument, "text") != "" || r.Preparation.BootstrapNativeModel != "MODEL_TAB" {
		t.Fatal("missing initialization provenance", r.Preparation)
	}
	var raw Object
	_ = json.Unmarshal(r.Frames[0], &raw)
	info := field(raw, "requestInfo")
	calls := field(info, "testGenerationCalls")
	if calls["supercomplete"] != float64(0) || calls["tab_jump"] != float64(1) {
		t.Fatal("hidden Supercomplete generation", calls)
	}
	docs := list(info, "testReplay")
	if len(docs) != 3 || str(object(docs[0]), "text") != "" || str(object(docs[1]), "text") != "old" || str(object(docs[2]), "text") != "new" {
		t.Fatal("invented or missing user snapshot", docs)
	}
	if str(object(docs[0]), "absoluteUri") == str(object(docs[1]), "absoluteUri") || str(object(docs[1]), "timestamp") == "1970-01-01T00:00:00Z" {
		t.Fatal("sentinel leaked into user history")
	}
	p, err := r.Preview("new")
	if err != nil || p != "new!" {
		t.Fatal(p, err)
	}
	if r.EngineVersion != "2.24.0" || r.RequestedNativeModel != "MODEL_JUMP" || r.LatencyMS == 0 {
		t.Fatal(r)
	}
	// Repeating Sync cannot erase useful history by replacing it with an
	// identical current/current pair.
	if err = s.Sync(ctx, s.document); err != nil {
		t.Fatal(err)
	}
	if len(s.history) != 2 || s.history[0].Document.Text != "old" || !s.history[1].ObservedAt.Equal(when) {
		t.Fatal(s.history)
	}
}
func TestIndependentTabFirstSnapshotAndOutcomes(t *testing.T) {
	for _, tc := range []struct{ scenario, code, status string }{
		{"editor_success", "", "jump"}, {"editor_empty", "", "empty"}, {"editor_filtered", "", "filtered"}, {"editor_bootstrap_data", "incompatible_format", "incomplete"},
	} {
		t.Run(tc.scenario, func(t *testing.T) {
			s := replayFixture(t, tc.scenario)
			if err := s.Sync(context.Background(), EditorDocument{Name: "test.py", Language: "python", Text: "current", CursorOffset: 0}); err != nil {
				t.Fatal(err)
			}
			r, err := s.TabToJump(context.Background(), nil)
			if ErrorCode(err) != tc.code || r.Status != tc.status {
				t.Fatal(r, err)
			}
			if tc.scenario == "editor_bootstrap_data" && (len(r.Preparation.BootstrapFrames) != 1 || len(r.Frames) != 0 || r.Response != nil) {
				t.Fatal("continued past unexpected bootstrap inference", r)
			}
		})
	}
}
func TestIndependentTabVersionAndCancellation(t *testing.T) {
	t.Run("version", func(t *testing.T) {
		s := replayFixture(t, "editor_success")
		s.client.config.Installation.Version = "2.23.0"
		if err := s.Sync(context.Background(), EditorDocument{Name: "test.py", Language: "python", Text: "current"}); err != nil {
			t.Fatal(err)
		}
		r, err := s.TabToJump(context.Background(), nil)
		requireCode(t, err, "unsupported_editor_version")
		if len(r.Preparation.BootstrapFrames) != 0 || r.Preparation.BootstrapTerminal != nil {
			t.Fatal("bootstrapped an unsupported engine")
		}
	})
	t.Run("cancel_and_recover", func(t *testing.T) {
		s := replayFixture(t, "editor_running")
		if err := s.Sync(context.Background(), EditorDocument{Name: "test.py", Language: "python", Text: "current"}); err != nil {
			t.Fatal(err)
		}
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		r, err := s.TabToJump(ctx, func(EditorResponse) { cancel() })
		requireCode(t, err, "cancelled")
		if r.Status != "incomplete" {
			t.Fatal(r)
		}
		_, err = r.Preview("current")
		requireCode(t, err, "stale_edit")
		t.Setenv("ANTIGRAVITY_GO_MOCK_SCENARIO", "editor_success")
		r, err = s.TabToJump(context.Background(), nil)
		if err != nil || r.Status != "jump" {
			t.Fatal(r, err)
		}
	})
	t.Run("close_active_child", func(t *testing.T) {
		s := replayFixture(t, "editor_running")
		if err := s.Sync(context.Background(), EditorDocument{Name: "test.py", Language: "python", Text: "current"}); err != nil {
			t.Fatal(err)
		}
		r, err := s.TabToJump(context.Background(), func(EditorResponse) { _ = s.Close() })
		requireCode(t, err, "cancelled")
		if r.Status != "incomplete" {
			t.Fatal(r)
		}
		_, err = s.TabToJump(context.Background(), nil)
		requireCode(t, err, "client_closed")
	})
}

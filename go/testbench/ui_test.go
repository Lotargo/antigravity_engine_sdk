package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	sdk "github.com/Lotargo/antigravity_engine_sdk/go"
	"github.com/Lotargo/antigravity_engine_sdk/go/internal/mockengine"
)

func TestMain(m *testing.M) { mockengine.MaybeRun(); os.Exit(m.Run()) }
func testUI(t *testing.T) (*uiServer, *httptest.Server) {
	t.Helper()
	t.Setenv(mockengine.Environment, "1")
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	client, err := sdk.NewClient(sdk.Config{Installation: &sdk.Installation{Executable: exe, Version: "2.0.0-mock"}, Timeout: 5 * time.Second, PollInterval: 5 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	app := newUIServer(ctx, client, "mock", "mock-low", t.TempDir())
	server := httptest.NewServer(app.handler())
	t.Cleanup(func() { cancel(); _ = client.Close(); server.Close(); app.wg.Wait() })
	return app, server
}
func uiCall(t *testing.T, a *uiServer, s *httptest.Server, method, path string, body io.Reader, kind string) (int, []byte) {
	t.Helper()
	r, err := http.NewRequest(method, s.URL+path, body)
	if err != nil {
		t.Fatal(err)
	}
	r.Header.Set("X-Testbench-Token", a.token)
	if kind != "" {
		r.Header.Set("Content-Type", kind)
	}
	res, err := s.Client().Do(r)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	data, err := io.ReadAll(res.Body)
	if err != nil {
		t.Fatal(err)
	}
	return res.StatusCode, data
}
func uiSubmit(t *testing.T, a *uiServer, s *httptest.Server, prompt string, image []byte) (int, *uiJob) {
	return uiSubmitMode(t, a, s, prompt, image, "finish")
}
func uiSubmitMode(t *testing.T, a *uiServer, s *httptest.Server, prompt string, image []byte, mode string) (int, *uiJob) {
	t.Helper()
	var b bytes.Buffer
	w := multipart.NewWriter(&b)
	_ = w.WriteField("model", "mock-low")
	_ = w.WriteField("prompt", prompt)
	_ = w.WriteField("response_mode", mode)
	if image != nil {
		f, err := w.CreateFormFile("image", "fixture.png")
		if err != nil {
			t.Fatal(err)
		}
		_, _ = f.Write(image)
	}
	_ = w.Close()
	code, data := uiCall(t, a, s, "POST", "/api/requests", &b, w.FormDataContentType())
	var job uiJob
	if code == 202 {
		if err := json.Unmarshal(data, &job); err != nil {
			t.Fatal(err)
		}
	}
	return code, &job
}

func TestUIExplicitPlainTextMode(t *testing.T) {
	a, s := testUI(t)
	if code, _ := uiSubmitMode(t, a, s, "plain", nil, "unknown"); code != 400 {
		t.Fatal(code)
	}
	if code, _ := uiSubmitMode(t, a, s, "plain", nil, "text"); code != 202 {
		t.Fatal(code)
	}
	done := waitJob(t, a, s, func(j *uiJob) bool { return !active(j.Status) })
	if done.Status != "completed" || done.ResponseMode != "text" || done.Result.OutputMode != "planner_text" {
		t.Fatal(done)
	}
}
func waitJob(t *testing.T, a *uiServer, s *httptest.Server, predicate func(*uiJob) bool) *uiJob {
	t.Helper()
	deadline := time.Now().Add(4 * time.Second)
	for time.Now().Before(deadline) {
		_, data := uiCall(t, a, s, "GET", "/api/request", nil, "")
		var job uiJob
		if err := json.Unmarshal(data, &job); err != nil {
			t.Fatal(err)
		}
		if predicate(&job) {
			return &job
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("request state did not settle")
	return nil
}
func configure(t *testing.T, a *uiServer, scenario string) {
	t.Helper()
	if _, err := a.client.RPC(context.Background(), "TestConfigure", sdk.Object{"scenario": scenario}, sdk.RPCOptions{}); err != nil {
		t.Fatal(err)
	}
}
func TestUIPageAndRequestWithImage(t *testing.T) {
	a, s := testUI(t)
	code, page := uiCall(t, a, s, "GET", "/", nil, "")
	if code != 200 || !bytes.Contains(page, []byte(a.token)) || !bytes.Contains(page, []byte(`id="cancel"`)) {
		t.Fatal(code, string(page))
	}
	code, data := uiCall(t, a, s, "GET", "/api/models", nil, "")
	if code != 200 || !bytes.Contains(data, []byte("mock-low")) || bytes.Contains(data, []byte("MODEL_INTERNAL")) {
		t.Fatal(code, string(data))
	}
	code, job := uiSubmit(t, a, s, "describe", visionFixture().Data)
	if code != 202 || job.ID == "" {
		t.Fatal(code, job)
	}
	done := waitJob(t, a, s, func(j *uiJob) bool { return !active(j.Status) })
	if done.Status != "completed" || done.Result == nil || done.Attachment != "fixture.png" || done.Result.Usage[0]["inputTokens"] != "17" {
		t.Fatalf("bad result: %+v", done)
	}
	if _, err := os.Stat(filepath.Join(a.output, done.Report)); err != nil {
		t.Fatal(err)
	}
	code, data = uiCall(t, a, s, "GET", "/api/requests/"+job.ID+"/report", nil, "")
	if code != 200 || !bytes.Contains(data, []byte("raw_response_sha256")) {
		t.Fatal(code, string(data))
	}
}
func TestUICancelConflictAndRecovery(t *testing.T) {
	a, s := testUI(t)
	configure(t, a, "running")
	code, job := uiSubmit(t, a, s, "wait", nil)
	if code != 202 {
		t.Fatal(code)
	}
	waitJob(t, a, s, func(j *uiJob) bool { return j.Phase == "trajectory" })
	if code, _ = uiSubmit(t, a, s, "duplicate", nil); code != 409 {
		t.Fatal("concurrent submit", code)
	}
	if code, _ = uiCall(t, a, s, "POST", "/api/requests/wrong/cancel", nil, ""); code != 404 {
		t.Fatal(code)
	}
	code, _ = uiCall(t, a, s, "POST", "/api/requests/"+job.ID+"/cancel", nil, "")
	if code != 200 {
		t.Fatal(code)
	}
	done := waitJob(t, a, s, func(j *uiJob) bool { return !active(j.Status) })
	if done.Status != "cancelled" || done.Result != nil || done.Error.Code != "cancelled" || !strings.Contains(done.UsageNote, "UNKNOWN") {
		t.Fatalf("bad cancellation: %+v", done)
	}
	native, err := a.client.RPC(context.Background(), "TestState", nil, sdk.RPCOptions{})
	if err != nil {
		t.Fatal(err)
	}
	calls := native["calls"].(map[string]any)
	if calls["CancelCascadeInvocation"] != float64(1) {
		t.Fatal("native cancel missing", calls)
	}
	configure(t, a, "success")
	if code, _ = uiSubmit(t, a, s, "retry", nil); code != 202 {
		t.Fatal(code)
	}
	if done = waitJob(t, a, s, func(j *uiJob) bool { return !active(j.Status) }); done.Status != "completed" {
		t.Fatal(done)
	}
}
func TestUIInvalidImageAndPrompt(t *testing.T) {
	a, s := testUI(t)
	if code, _ := uiSubmit(t, a, s, "", nil); code != 400 {
		t.Fatal(code)
	}
	if code, _ := uiSubmit(t, a, s, "image", []byte("not a PNG")); code != 400 {
		t.Fatal(code)
	}
	code, data := uiCall(t, a, s, "GET", "/api/request", nil, "")
	if code != 200 || strings.TrimSpace(string(data)) != "null" {
		t.Fatal(code, string(data))
	}
}
func TestUISameOriginAndToken(t *testing.T) {
	a, s := testUI(t)
	for _, test := range []struct{ token, origin, host string }{{"", "", ""}, {a.token, "http://example.test", ""}, {a.token, "", "evil.test:8765"}} {
		r, _ := http.NewRequest("GET", s.URL+"/api/config", nil)
		r.Header.Set("X-Testbench-Token", test.token)
		r.Header.Set("Origin", test.origin)
		if test.host != "" {
			r.Host = test.host
		}
		res, err := s.Client().Do(r)
		if err != nil {
			t.Fatal(err)
		}
		_ = res.Body.Close()
		if res.StatusCode != 403 {
			t.Fatal("unexpected origin/token status", res.StatusCode)
		}
	}
}
func TestUIFailureAndFreshCatalog(t *testing.T) {
	a, s := testUI(t)
	configure(t, a, "catalog_changed")
	code, data := uiCall(t, a, s, "GET", "/api/models", nil, "")
	if code != 200 || bytes.Contains(data, []byte("text-only")) || !bytes.Contains(data, []byte("new-model")) {
		t.Fatal(code, string(data))
	}
	configure(t, a, "failed")
	if code, _ := uiSubmit(t, a, s, "fail", nil); code != 202 {
		t.Fatal(code)
	}
	done := waitJob(t, a, s, func(j *uiJob) bool { return !active(j.Status) })
	if done.Status != "error" || done.Error.Code != "provider_error" || done.Report == "" {
		t.Fatalf("missing failure evidence: %+v", done)
	}
}

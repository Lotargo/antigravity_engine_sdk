package antigravity

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Lotargo/antigravity_engine_sdk/go/internal/mockengine"
)

func TestMain(m *testing.M) { mockengine.MaybeRun(); os.Exit(m.Run()) }
func fixture(t *testing.T) *Client {
	t.Helper()
	t.Setenv(mockengine.Environment, "1")
	path, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	c, err := NewClient(Config{Installation: &Installation{path, "2.0.0-mock"}, Timeout: 5 * time.Second, PollInterval: 5 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = c.Close() })
	return c
}
func scenario(t *testing.T, c *Client, name string) {
	t.Helper()
	if _, err := c.RPC(context.Background(), "TestConfigure", Object{"scenario": name}, RPCOptions{}); err != nil {
		t.Fatal(err)
	}
}
func state(t *testing.T, c *Client) Object {
	t.Helper()
	s, err := c.RPC(context.Background(), "TestState", nil, RPCOptions{Concurrent: true})
	if err != nil {
		t.Fatal(err)
	}
	return s
}
func requireCode(t *testing.T, err error, code string) {
	t.Helper()
	if ErrorCode(err) != code {
		t.Fatalf("want %s; got %v", code, err)
	}
}
func answerSchema() Object {
	return Object{"type": "object", "properties": Object{"answer": Object{"type": "integer"}, "nested": Object{"$ref": "#/$defs/nested"}}, "required": []string{"answer", "nested"}, "additionalProperties": false, "$defs": Object{"nested": Object{"type": "object", "properties": Object{"ok": Object{"type": "boolean"}}, "required": []string{"ok"}}}}
}
func TestNativeBoundary(t *testing.T) {
	t.Setenv("GEMINI_API_KEY", "must-not-be-inherited")
	c := fixture(t)
	var phases []string
	prompt := strings.Repeat("quotes \" and real\nline breaks ", 4000)
	r, err := c.Generate(context.Background(), Request{Prompt: prompt, Model: "mock-low", Schema: answerSchema(), Thinking: "high", Media: []Media{{Data: []byte("image"), MIMEType: "image/png"}}, OnProgress: func(e ProgressEvent) { phases = append(phases, e.Phase) }})
	if err != nil {
		t.Fatal(err)
	}
	if r.Output["answer"] != float64(42) || r.Model != "mock-low" || r.NativeModel != "MODEL_MOCK" || r.EngineVersion != "2.0.0-mock" || r.Usage[0]["inputTokens"] != "17" || r.UsageScope != "trajectory_model_steps" || r.RawResponseSHA256 != hash(r.RawOutput) {
		t.Fatalf("lost native result fields: %+v", r)
	}
	if !reflect.DeepEqual(phases, []string{"starting", "working", "trajectory", "completed"}) {
		t.Fatal(phases)
	}
	s := state(t, c)
	if flag(s, "api_key_inherited") {
		t.Fatal("inherited API key")
	}
	start := object(list(s, "starts")[0])
	if marshal(list(field(field(start, "customAgentSpec"), "customAgent"), "toolNames")) != `["finish"]` {
		t.Fatal(start)
	}
	message := field(s, "last_send")
	if !strings.Contains(str(object(list(message, "items")[0]), "text"), prompt) {
		t.Fatal("prompt changed")
	}
	if marshal(message["media"]) != `[{"inlineData":"aW1hZ2U=","mimeType":"image/png"}]` {
		t.Fatal(message["media"])
	}
	planner := field(field(message, "cascadeConfig"), "plannerConfig")
	if marshal(planner["completionConfigOverride"]) != `{"thinkingLevel":3}` {
		t.Fatal(planner)
	}
}
func TestLazyOwnershipAndFreshRequests(t *testing.T) {
	c := fixture(t)
	if c.engine != nil {
		t.Fatal("eager process launch")
	}
	first, err := c.Chat(context.Background(), Request{Prompt: "hello", Model: "mock-low"})
	if err != nil {
		t.Fatal(err)
	}
	e := c.engine
	root := e.root
	second, err := c.Chat(context.Background(), Request{Prompt: "again", Model: "mock-low"})
	if err != nil {
		t.Fatal(err)
	}
	if c.engine != e || first.CascadeID == second.CascadeID {
		t.Fatal("reuse/fresh cascade violated")
	}
	starts := list(state(t, c), "starts")
	if marshal(object(starts[0])["workspaceUris"]) == marshal(object(starts[1])["workspaceUris"]) {
		t.Fatal("reused workspace")
	}
	other := fixture(t)
	if _, err = other.Models(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	if other.engine == e || other.engine.cmd.Process.Pid == e.cmd.Process.Pid {
		t.Fatal("shared engine")
	}
	_ = c.Close()
	if _, err = os.Stat(root); !os.IsNotExist(err) {
		t.Fatalf("runtime directory remains: %v", err)
	}
	if !other.engine.usable() {
		t.Fatal("closed foreign client")
	}
	_, err = c.Models(context.Background(), false)
	requireCode(t, err, "client_closed")
}
func TestFreshCatalogAndRecovery(t *testing.T) {
	c := fixture(t)
	models, err := c.Models(context.Background(), false)
	if err != nil {
		t.Fatal(err)
	}
	if len(models) != 2 {
		t.Fatal(models)
	}
	all, err := c.Models(context.Background(), true)
	if err != nil || len(all) != 3 {
		t.Fatal(all, err)
	}
	scenario(t, c, "catalog_changed")
	models, err = c.Models(context.Background(), false)
	if err != nil {
		t.Fatal(err)
	}
	for _, m := range models {
		if m.ID == "text-only" {
			t.Fatal("stale catalog")
		}
	}
	if models[1].ID != "new-model" || !flag(models[1].Raw, "providerFlag") {
		t.Fatal(models)
	}
	for _, name := range []string{"send_error", "retired"} {
		scenario(t, c, name)
		_, err = c.Chat(context.Background(), Request{Prompt: "test", Model: "mock-low"})
		if err == nil {
			t.Fatal("expected error")
		}
		scenario(t, c, "success")
		if _, err = c.Chat(context.Background(), Request{Prompt: "retry", Model: "mock-low"}); err != nil {
			t.Fatal("catalog poisoned", err)
		}
	}
	auth, err := c.Authentication(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(marshal(auth), "accessToken") || !auth.Available {
		t.Fatal(auth)
	}
}
func TestFailureContracts(t *testing.T) {
	for name, code := range map[string]string{"invalid": "contract_violation", "extra": "contract_violation", "invalid_json": "contract_violation", "null": "contract_violation", "no_finish": "incompatible_format", "pending_finish": "incompatible_format", "failed": "provider_error", "blocked": "content_rejected", "retired": "model_unavailable", "refused": "content_rejected", "no_id": "incompatible_format"} {
		t.Run(name, func(t *testing.T) {
			c := fixture(t)
			scenario(t, c, name)
			_, err := c.Generate(context.Background(), Request{Prompt: "test", Model: "mock-low", Schema: answerSchema()})
			requireCode(t, err, code)
		})
	}
}
func TestPreflight(t *testing.T) {
	for name, r := range map[string]Request{
		"thinking":    {Prompt: "p", Model: "m", Schema: answerSchema(), Thinking: "extra-high"},
		"invocations": {Prompt: "p", Model: "m", Schema: answerSchema(), MaxInvocations: -1},
		"empty":       {Model: "m", Schema: answerSchema()},
		"media":       {Prompt: "p", Model: "m", Schema: answerSchema(), Media: []Media{{}}},
		"array":       {Prompt: "p", Model: "m", Schema: Object{"type": "array"}},
		"remote":      {Prompt: "p", Model: "m", Schema: Object{"type": "object", "properties": Object{"a": Object{"$ref": "https://example.invalid/schema"}}}},
		"unresolved":  {Prompt: "p", Model: "m", Schema: Object{"type": "object", "properties": Object{"a": Object{"$ref": "#/$defs/missing"}}}},
	} {
		t.Run(name, func(t *testing.T) {
			c := fixture(t)
			_, err := c.Generate(context.Background(), r)
			if err == nil || c.engine != nil {
				t.Fatalf("preflight failed: %v", err)
			}
		})
	}
	c := fixture(t)
	_, err := c.Chat(context.Background(), Request{Prompt: "p", Model: "missing"})
	requireCode(t, err, "model_unavailable")
	_, err = c.Chat(context.Background(), Request{Prompt: "p", Model: "text-only", Media: []Media{{Data: []byte{1}, MIMEType: "image/png"}}})
	requireCode(t, err, "incompatible_image_input")
	if len(list(state(t, c), "starts")) != 0 {
		t.Fatal("preflight created a cascade")
	}
	scenario(t, c, "dropped_media")
	_, err = c.Chat(context.Background(), Request{Prompt: "p", Model: "mock-low", Media: []Media{{Data: []byte{1}, MIMEType: "image/png"}}})
	requireCode(t, err, "incompatible_image_input")
}
func TestCancellationConcurrentMetadataQueueAndRecovery(t *testing.T) {
	c := fixture(t)
	scenario(t, c, "running")
	e := c.engine
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	working := make(chan struct{})
	finished := make(chan error, 1)
	go func() {
		_, err := c.Chat(ctx, Request{Prompt: "wait", Model: "mock-low", OnProgress: func(p ProgressEvent) {
			if p.Phase == "working" {
				close(working)
			}
		}})
		finished <- err
	}()
	select {
	case <-working:
	case <-time.After(3 * time.Second):
		t.Fatal("no working event")
	}
	metaCtx, metaCancel := context.WithTimeout(context.Background(), time.Second)
	defer metaCancel()
	if _, err := c.RPC(metaCtx, "GetAvailableModels", nil, RPCOptions{Concurrent: true}); err != nil {
		t.Fatal("metadata blocked", err)
	}
	queueCtx, queueCancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer queueCancel()
	_, err := c.Chat(queueCtx, Request{Prompt: "queued", Model: "mock-low"})
	requireCode(t, err, "timeout")
	if len(list(state(t, c), "starts")) != 1 {
		t.Fatal("queued request created cascade")
	}
	cancel()
	select {
	case err = <-finished:
		requireCode(t, err, "cancelled")
	case <-time.After(3 * time.Second):
		t.Fatal("cancel stuck")
	}
	if field(state(t, c), "calls")["CancelCascadeInvocation"] != float64(1) {
		t.Fatal("missing cancel RPC")
	}
	scenario(t, c, "success")
	if _, err = c.Chat(context.Background(), Request{Prompt: "recovered", Model: "mock-low"}); err != nil || c.engine != e {
		t.Fatal("failed to reuse cancelled engine", err)
	}
}
func TestFailedCancelRetiresEngine(t *testing.T) {
	c := fixture(t)
	scenario(t, c, "cancel_error")
	e := c.engine
	ctx, cancel := context.WithCancel(context.Background())
	_, err := c.Chat(ctx, Request{Prompt: "wait", Model: "mock-low", OnProgress: func(p ProgressEvent) {
		if p.Phase == "working" {
			cancel()
		}
	}})
	cancel()
	requireCode(t, err, "cancelled")
	if e.usable() || c.engine != nil {
		t.Fatal("uncertain cancellation kept engine")
	}
	if _, err = c.Chat(context.Background(), Request{Prompt: "recovered", Model: "mock-low"}); err != nil || c.engine == e {
		t.Fatal("failed recovery", err)
	}
}
func TestSendErrorAlsoCancels(t *testing.T) {
	c := fixture(t)
	scenario(t, c, "send_error")
	_, err := c.Chat(context.Background(), Request{Prompt: "p", Model: "mock-low"})
	requireCode(t, err, "provider_error")
	if field(state(t, c), "calls")["CancelCascadeInvocation"] != float64(1) {
		t.Fatal("missing send-failure cancel")
	}
}
func TestCloseInterruptsActive(t *testing.T) {
	c := fixture(t)
	scenario(t, c, "running")
	working := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		_, err := c.Chat(context.Background(), Request{Prompt: "wait", Model: "mock-low", OnProgress: func(e ProgressEvent) {
			if e.Phase == "working" {
				close(working)
			}
		}})
		done <- err
	}()
	<-working
	_ = c.Close()
	select {
	case err := <-done:
		requireCode(t, err, "cancelled")
	case <-time.After(3 * time.Second):
		t.Fatal("close stuck")
	}
}
func TestCallbackPanicCancels(t *testing.T) {
	c := fixture(t)
	func() {
		defer func() {
			if recover() == nil {
				t.Fatal("panic swallowed")
			}
		}()
		_, _ = c.Chat(context.Background(), Request{Prompt: "p", Model: "mock-low", OnProgress: func(e ProgressEvent) {
			if e.Phase == "working" {
				panic("consumer failure")
			}
		}})
	}()
	if field(state(t, c), "calls")["CancelCascadeInvocation"] != float64(1) {
		t.Fatal("callback panic did not cancel")
	}
}
func TestDeadEngineReplaced(t *testing.T) {
	c := fixture(t)
	if _, err := c.Models(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	e := c.engine
	_ = e.cmd.Process.Kill()
	select {
	case <-e.done:
	case <-time.After(3 * time.Second):
		t.Fatal("process did not exit")
	}
	if _, err := c.Chat(context.Background(), Request{Prompt: "p", Model: "mock-low"}); err != nil || c.engine == e {
		t.Fatal("dead engine not replaced", err)
	}
}
func TestImages(t *testing.T) {
	for _, name := range []string{"success", "image_file", "image_missing", "image_invalid"} {
		t.Run(name, func(t *testing.T) {
			c := fixture(t)
			scenario(t, c, name)
			r, err := c.GenerateImage(context.Background(), ImageRequest{Prompt: "cup", Planner: "mock-low", ExpectedModel: "mock-image"})
			if name == "image_missing" {
				requireCode(t, err, "image_missing")
				return
			}
			if name == "image_invalid" {
				requireCode(t, err, "incompatible_format")
				return
			}
			if err != nil || len(r.Images) != 1 || len(r.Images[0].Data) == 0 {
				t.Fatal(r, err)
			}
			_ = c.Close()
			path := filepath.Join(t.TempDir(), "cup.png")
			if err = r.Images[0].Save(path); err != nil {
				t.Fatal(err)
			}
			if err = r.Images[0].Save(path); err == nil {
				t.Fatal("overwrote existing artifact")
			}
		})
	}
	c := fixture(t)
	_, err := c.GenerateImage(context.Background(), ImageRequest{Prompt: "cup", Planner: "mock-low", ExpectedModel: "other"})
	requireCode(t, err, "image_model_mismatch")
}
func TestUnsafeImagePathsIgnored(t *testing.T) {
	root := t.TempDir()
	outside := filepath.Join(t.TempDir(), "external.png")
	if err := os.WriteFile(outside, []byte("outside"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(root, "link.png")); err != nil {
		t.Skip("symlink privilege unavailable:", err)
	}
	_, err := collectImages([]any{Object{"generateImage": Object{"modelName": "m", "generatedMedia": Object{"uri": outside}}}}, root, "")
	requireCode(t, err, "image_missing")
}
func TestConfigAndRPCErrors(t *testing.T) {
	for _, cfg := range []Config{{Timeout: -1}, {ReadyTimeout: -1}, {PollInterval: -1}, {Executable: "x", Installation: &Installation{"x", "v"}}, {Installation: &Installation{}}} {
		if _, err := NewClient(cfg); ErrorCode(err) != "invalid_input" {
			t.Fatal(err)
		}
	}
	c := fixture(t)
	_, err := c.RPC(context.Background(), "../x", nil, RPCOptions{})
	requireCode(t, err, "invalid_input")
	if c.engine != nil {
		t.Fatal("invalid RPC launched engine")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err = c.Models(ctx, false)
	requireCode(t, err, "cancelled")
	_, err = c.Chat(nil, Request{})
	requireCode(t, err, "invalid_input")
}
func TestSchemaNullObjectRejected(t *testing.T) {
	c := fixture(t)
	scenario(t, c, "null")
	_, err := c.Generate(context.Background(), Request{Prompt: "p", Model: "mock-low", Schema: Object{"type": "object"}})
	requireCode(t, err, "contract_violation")
}
func TestConcurrentColdStartSingleOwner(t *testing.T) {
	c := fixture(t)
	var wg sync.WaitGroup
	errs := make(chan error, 6)
	for i := 0; i < 6; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := c.RPC(context.Background(), "GetAvailableModels", nil, RPCOptions{Concurrent: true})
			errs <- err
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	s := state(t, c)
	if field(s, "calls")["HasAuthToken"] != float64(1) {
		t.Fatal("multiple cold starts")
	}
}
func TestResultJSON(t *testing.T) {
	r := &Result{Output: Object{"text": "hello"}}
	s, err := r.Text()
	if err != nil || s != "hello" {
		t.Fatal(s, err)
	}
	if _, err = json.Marshal(r); err != nil {
		t.Fatal(err)
	}
	r.Output = Object{}
	if _, err = r.Text(); err == nil {
		t.Fatal("missing text accepted")
	}
}

func TestStartupTimeoutAndAuthentication(t *testing.T) {
	for name, code := range map[string]string{"not_ready": "timeout", "signed_out": "authentication_required"} {
		t.Run(name, func(t *testing.T) {
			t.Setenv("ANTIGRAVITY_GO_MOCK_SCENARIO", name)
			c := fixture(t)
			c.config.ReadyTimeout = 150 * time.Millisecond
			if name == "signed_out" {
				c.config.ReadyTimeout = 2 * time.Second
			}
			_, err := c.Models(context.Background(), false)
			requireCode(t, err, code)
			if c.engine != nil {
				t.Fatal("failed startup retained process")
			}
		})
	}
}
func TestCloseDuringStartup(t *testing.T) {
	t.Setenv("ANTIGRAVITY_GO_MOCK_SCENARIO", "not_ready")
	c := fixture(t)
	done := make(chan error, 1)
	go func() { _, err := c.Models(context.Background(), false); done <- err }()
	deadline := time.After(3 * time.Second)
	for {
		c.mu.Lock()
		e := c.engine
		c.mu.Unlock()
		if e != nil {
			_ = c.Close()
			break
		}
		select {
		case <-deadline:
			t.Fatal("no startup")
		case <-time.After(time.Millisecond):
		}
	}
	select {
	case err := <-done:
		requireCode(t, err, "cancelled")
	case <-time.After(3 * time.Second):
		t.Fatal("startup did not stop")
	}
}

func TestDeadlineCancelsNativeInvocation(t *testing.T) {
	c := fixture(t)
	scenario(t, c, "running")
	// Leave enough budget for schema compilation under the race detector and
	// a loaded build machine, so this exercises an active invocation timeout.
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_, err := c.Chat(ctx, Request{Prompt: "wait", Model: "mock-low"})
	requireCode(t, err, "timeout")
	if field(state(t, c), "calls")["CancelCascadeInvocation"] != float64(1) {
		t.Fatal("deadline did not cancel native work")
	}
}

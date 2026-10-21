package antigravity

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

const service = "/exa.language_server_pb.LanguageServerService/"

var portPattern = regexp.MustCompile(`listening on .*?port at (\d+) for HTTP(?:$|\s)`)
var methodPattern = regexp.MustCompile(`^[A-Za-z0-9_]+$`)

// engine owns one process and never stores or forwards native logs or tokens.
type engine struct {
	mu                        sync.Mutex
	installation              Installation
	root, csrf                string
	port                      int
	closed, ready, authPrompt bool
	cmd                       *exec.Cmd
	stdin                     io.WriteCloser
	done                      chan struct{}
	job                       *processJob
	http                      *http.Client
}

func newEngine(installation Installation) *engine {
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		panic(err)
	}
	return &engine{installation: installation, csrf: hex.EncodeToString(secret), http: &http.Client{Transport: &http.Transport{Proxy: nil}}}
}
func (e *engine) usable() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.closed || !e.ready || e.authPrompt || e.done == nil {
		return false
	}
	select {
	case <-e.done:
		return false
	default:
		return true
	}
}
func (e *engine) check(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return contextError(err)
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.closed {
		return fault("cancelled", "Engine closed.", nil)
	}
	if e.authPrompt {
		return fault("authentication_required", "Sign in once in Antigravity 2.x.", nil)
	}
	if e.done != nil {
		select {
		case <-e.done:
			return fault("engine_exited", "Native engine exited.", nil)
		default:
		}
	}
	return nil
}
func contextError(err error) error {
	if errors.Is(err, context.DeadlineExceeded) {
		return fault("timeout", "Request timed out.", err)
	}
	return fault("cancelled", "Request cancelled.", err)
}
func (e *engine) start(ctx context.Context, readyTimeout time.Duration) (err error) {
	defer func() {
		if err != nil {
			e.close()
		}
	}()
	e.mu.Lock()
	if e.closed {
		e.mu.Unlock()
		return fault("cancelled", "Engine closed.", nil)
	}
	e.root, err = os.MkdirTemp("", "antigravity-go-sdk-")
	if err != nil {
		e.mu.Unlock()
		return fault("engine_unavailable", "Cannot create runtime directory.", err)
	}
	config := filepath.Join(e.root, "gemini", "config")
	workspace := filepath.Join(e.root, "workspace")
	for _, dir := range []string{config, workspace} {
		if err = os.MkdirAll(dir, 0700); err != nil {
			e.mu.Unlock()
			return err
		}
	}
	for name, data := range map[string]string{"config.json": "{}", "mcp_config.json": `{"mcpServers":{}}`} {
		if err = os.WriteFile(filepath.Join(config, name), []byte(data), 0600); err != nil {
			e.mu.Unlock()
			return err
		}
	}
	e.cmd = exec.Command(e.installation.Executable,
		"--standalone", "--exit_on_stdin_close", "--disable_telemetry",
		"--gemini_dir", filepath.Join(e.root, "gemini"), "--config_dir", "config",
		"--app_data_dir", "antigravity-engine-sdk", "--csrf_token", e.csrf,
		"--override_ide_name", "antigravity", "--override_ide_version", e.installation.Version,
		"--override_user_agent_name", "antigravity", "--subclient_type", "hub",
		"--api_server_url", "https://generativelanguage.googleapis.com",
		"--cloud_code_endpoint", "https://daily-cloudcode-pa.googleapis.com")
	e.cmd.Dir = workspace
	configureProcess(e.cmd)
	blocked := map[string]bool{"GEMINI_API_KEY": true, "GOOGLE_API_KEY": true, "AGY_ADC_AUTH": true, "GOOGLE_GENAI_USE_VERTEXAI": true, "ANTIGRAVITY_VSCODE_HOST": true}
	for _, v := range os.Environ() {
		k, _, _ := strings.Cut(v, "=")
		if !blocked[strings.ToUpper(k)] {
			e.cmd.Env = append(e.cmd.Env, v)
		}
	}
	e.cmd.Env = append(e.cmd.Env, "ANTIGRAVITY_VSCODE_HOST=1")
	e.stdin, err = e.cmd.StdinPipe()
	if err != nil {
		e.mu.Unlock()
		return err
	}
	stdout, err := e.cmd.StdoutPipe()
	if err != nil {
		e.mu.Unlock()
		return err
	}
	stderr, err := e.cmd.StderrPipe()
	if err != nil {
		e.mu.Unlock()
		return err
	}
	if err = e.cmd.Start(); err != nil {
		e.mu.Unlock()
		return fault("engine_unavailable", "Cannot start native engine.", err)
	}
	e.done = make(chan struct{})
	e.job = attachJob(e.cmd)
	// Drain before Wait, as required by os/exec's StdoutPipe contract.
	var readers sync.WaitGroup
	readers.Add(2)
	for _, stream := range []io.Reader{stdout, stderr} {
		go func(r io.Reader) { defer readers.Done(); e.drain(r) }(stream)
	}
	go func() { readers.Wait(); _ = e.cmd.Wait(); close(e.done) }()
	e.mu.Unlock()
	readyCtx, cancel := context.WithTimeout(ctx, readyTimeout)
	defer cancel()
	for {
		if err = e.check(readyCtx); err != nil {
			return err
		}
		e.mu.Lock()
		port := e.port
		e.mu.Unlock()
		if port > 0 {
			rpcCtx, stop := context.WithTimeout(readyCtx, time.Second)
			auth, rpcErr := e.rpc(rpcCtx, "HasAuthToken", nil)
			stop()
			if rpcErr == nil {
				if !flag(auth, "hasToken") {
					return fault("authentication_required", "Sign in once in Antigravity 2.x.", nil)
				}
				e.mu.Lock()
				e.ready = true
				e.mu.Unlock()
				return nil
			}
			if code := ErrorCode(rpcErr); code != "engine_connection" && code != "timeout" {
				return rpcErr
			}
		}
		if err = pause(readyCtx, 50*time.Millisecond); err != nil {
			return err
		}
	}
}
func (e *engine) drain(r io.Reader) {
	scanner := bufio.NewScanner(r)
	scanner.Buffer(make([]byte, 4096), 1024*1024)
	for scanner.Scan() {
		line := scanner.Text()
		e.mu.Lock()
		if strings.Contains(line, "accounts.google.com/") || strings.Contains(strings.ToLower(line), "authorization code:") {
			e.authPrompt = true
		}
		if match := portPattern.FindStringSubmatch(line); len(match) > 1 {
			e.port, _ = strconv.Atoi(match[1])
		}
		e.mu.Unlock()
	}
	// Keep draining oversized native log lines without retaining their contents.
	_, _ = io.Copy(io.Discard, r)
}
func (e *engine) rpc(ctx context.Context, method string, payload Object) (Object, error) {
	if !methodPattern.MatchString(method) {
		return nil, fault("invalid_input", "Invalid RPC method.", nil)
	}
	if err := e.check(ctx); err != nil {
		return nil, err
	}
	if payload == nil {
		payload = Object{}
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, fault("invalid_input", "RPC payload is not JSON.", err)
	}
	e.mu.Lock()
	port := e.port
	e.mu.Unlock()
	req, err := http.NewRequestWithContext(ctx, "POST", "http://127.0.0.1:"+strconv.Itoa(port)+service+method, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Connect-Protocol-Version", "1")
	req.Header.Set("X-Codeium-Csrf-Token", e.csrf)
	resp, err := e.http.Do(req)
	if err != nil {
		if check := e.check(ctx); check != nil {
			return nil, check
		}
		return nil, fault("engine_connection", "Native engine is not responding.", nil)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		// Raw HTTP errors may contain credentials. Keep only the method/status.
		code := "provider_error"
		if resp.StatusCode == 401 || resp.StatusCode == 403 {
			code = "authentication_required"
		}
		return nil, &Error{Code: code, Message: "Native RPC failed.", Details: Object{"method": method, "http_status": resp.StatusCode}}
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 16*1024*1024+1))
	if err != nil {
		if check := e.check(ctx); check != nil {
			return nil, check
		}
		return nil, fault("engine_connection", "Cannot read RPC response.", nil)
	}
	if len(raw) > 16*1024*1024 {
		return nil, fault("incompatible_format", "RPC response exceeds 16 MiB.", nil)
	}
	var result Object
	if err = json.Unmarshal(raw, &result); err != nil || result == nil {
		return nil, fault("incompatible_format", "RPC returned an invalid JSON object.", nil)
	}
	if err = e.check(ctx); err != nil {
		return nil, err
	}
	return result, nil
}
func (e *engine) close() {
	e.mu.Lock()
	if e.closed {
		e.mu.Unlock()
		return
	}
	e.closed = true
	stdin, done, cmd, job, root := e.stdin, e.done, e.cmd, e.job, e.root
	e.mu.Unlock()
	if stdin != nil {
		_ = stdin.Close()
	}
	if done != nil {
		select {
		case <-done:
		case <-time.After(1500 * time.Millisecond):
			killTree(cmd, job)
			<-done
		}
	}
	if job != nil {
		job.close()
	}
	e.http.CloseIdleConnections()
	if root != "" {
		_ = os.RemoveAll(root)
	}
}
func pause(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return contextError(ctx.Err())
	case <-t.C:
		return nil
	}
}

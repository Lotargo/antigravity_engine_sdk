package main

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"html/template"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	sdk "github.com/Lotargo/antigravity_engine_sdk/go"
)

//go:embed web/*
var uiAssets embed.FS

type uiJob struct {
	ID           string      `json:"id"`
	Mode         string      `json:"mode"`
	SDKVersion   string      `json:"sdk_version"`
	Status       string      `json:"status"`
	Phase        string      `json:"phase"`
	Model        string      `json:"model"`
	Prompt       string      `json:"prompt"`
	Attachment   string      `json:"attachment,omitempty"`
	StartedAt    time.Time   `json:"started_at"`
	DurationMS   int64       `json:"duration_ms"`
	Result       *sdk.Result `json:"result,omitempty"`
	Error        *sdk.Error  `json:"error,omitempty"`
	Trajectory   sdk.Object  `json:"partial_trajectory,omitempty"`
	Report       string      `json:"report,omitempty"`
	ReportError  string      `json:"report_error,omitempty"`
	UsageNote    string      `json:"usage_note"`
	ResponseMode string      `json:"response_mode"`
	cancel       context.CancelFunc
}
type uiServer struct {
	client                     *sdk.Client
	ctx                        context.Context
	mode, model, output, token string
	mu                         sync.Mutex
	job                        *uiJob
	wg                         sync.WaitGroup
}

func newUIServer(ctx context.Context, c *sdk.Client, mode, model, output string) *uiServer {
	return &uiServer{client: c, ctx: ctx, mode: mode, model: model, output: output, token: randomID()}
}
func randomID() string {
	var b [24]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b[:])
}
func serveUI(ctx context.Context, c *sdk.Client, mode, model, address, output string) int {
	host, _, err := net.SplitHostPort(address)
	if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		fmt.Fprintln(os.Stderr, "--listen must be a loopback IP:port")
		return 2
	}
	listener, err := net.Listen("tcp", address)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	appCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	app := newUIServer(appCtx, c, mode, model, output)
	server := &http.Server{Handler: app.handler(), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 60 * time.Second}
	stopped := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
		case <-stopped:
			return
		}
		cancel()
		_ = c.Close()
		shutdownCtx, stop := context.WithTimeout(context.Background(), 5*time.Second)
		defer stop()
		_ = server.Shutdown(shutdownCtx)
	}()
	fmt.Printf("Antigravity Go testbench | %s\nOpen http://%s\nReports: %s\nCtrl+C to stop.\n", strings.ToUpper(mode), listener.Addr(), output)
	err = server.Serve(listener)
	close(stopped)
	cancel()
	_ = c.Close()
	app.wg.Wait()
	if err != nil && !errors.Is(err, http.ErrServerClosed) {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	return 0
}
func (a *uiServer) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		page, _ := uiAssets.ReadFile("web/index.html")
		t, err := template.New("index").Parse(string(page))
		if err != nil {
			http.Error(w, "UI template error", 500)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_ = t.Execute(w, struct{ Token string }{a.token})
	})
	for _, asset := range []string{"app.js", "style.css"} {
		name := asset
		mux.HandleFunc("GET /"+name, func(w http.ResponseWriter, r *http.Request) {
			data, _ := uiAssets.ReadFile("web/" + name)
			kind := "text/css"
			if name == "app.js" {
				kind = "text/javascript"
			}
			w.Header().Set("Content-Type", kind+"; charset=utf-8")
			_, _ = w.Write(data)
		})
	}
	mux.HandleFunc("GET /api/config", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, sdk.Object{"mode": a.mode, "model": a.model, "version": sdk.Version, "max_media_bytes": sdk.MaxMediaBytes})
	})
	mux.HandleFunc("GET /api/models", func(w http.ResponseWriter, r *http.Request) {
		// Fresh catalog even while a high-level request owns the inference lock.
		native, err := a.client.RPC(r.Context(), "GetAvailableModels", nil, sdk.RPCOptions{Concurrent: true})
		if err != nil {
			apiError(w, 502, err)
			return
		}
		response, _ := native["response"].(map[string]any)
		cards, _ := response["models"].(map[string]any)
		models := make([]sdk.Model, 0, len(cards))
		for id, value := range cards {
			card, _ := value.(map[string]any)
			nativeID, _ := card["model"].(string)
			internal, _ := card["isInternal"].(bool)
			if nativeID == "" || internal {
				continue
			}
			name, _ := card["displayName"].(string)
			if name == "" {
				name = id
			}
			images, _ := card["supportsImages"].(bool)
			models = append(models, sdk.Model{ID: id, NativeID: nativeID, Name: name, SupportsImages: images, Raw: card})
		}
		// Display ordering belongs to the stand, catalog IDs remain unchanged.
		sort.Slice(models, func(i, j int) bool { return models[i].ID < models[j].ID })
		writeJSON(w, 200, models)
	})
	mux.HandleFunc("GET /api/request", func(w http.ResponseWriter, r *http.Request) {
		a.mu.Lock()
		defer a.mu.Unlock()
		view := a.snapshotLocked()
		if view != nil {
			// Polling needs display data only. Full raw evidence remains in reports.
			view.Trajectory = nil
			if view.Result != nil {
				result := *view.Result
				result.Trajectory = nil
				view.Result = &result
			}
		}
		writeJSON(w, 200, view)
	})
	mux.HandleFunc("POST /api/requests", a.submit)
	mux.HandleFunc("POST /api/requests/{id}/cancel", a.cancelJob)
	mux.HandleFunc("GET /api/requests/{id}/report", func(w http.ResponseWriter, r *http.Request) {
		a.mu.Lock()
		defer a.mu.Unlock()
		if a.job == nil || a.job.ID != r.PathValue("id") || a.job.Report == "" {
			apiError(w, 404, fmt.Errorf("Report not found"))
			return
		}
		w.Header().Set("Content-Disposition", `attachment; filename="request-`+a.job.ID+`.json"`)
		writeJSON(w, 200, a.snapshotLocked())
	})
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
		host, _, err := net.SplitHostPort(r.Host)
		if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
			apiError(w, 403, fmt.Errorf("Loopback host required"))
			return
		}
		if origin := r.Header.Get("Origin"); origin != "" && origin != "http://"+r.Host {
			apiError(w, 403, fmt.Errorf("Origin rejected"))
			return
		}
		if strings.HasPrefix(r.URL.Path, "/api/") && subtle.ConstantTimeCompare([]byte(r.Header.Get("X-Testbench-Token")), []byte(a.token)) != 1 {
			apiError(w, 403, fmt.Errorf("Reload the testbench page"))
			return
		}
		mux.ServeHTTP(w, r)
	})
}
func (a *uiServer) snapshotLocked() *uiJob {
	if a.job == nil {
		return nil
	}
	copy := *a.job
	if active(copy.Status) {
		copy.DurationMS = time.Since(copy.StartedAt).Milliseconds()
	}
	copy.cancel = nil
	return &copy
}
func active(status string) bool { return status == "running" || status == "cancelling" }
func (a *uiServer) submit(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, sdk.MaxMediaBytes+1024*1024)
	if err := r.ParseMultipartForm(1024 * 1024); err != nil {
		if r.MultipartForm != nil {
			_ = r.MultipartForm.RemoveAll()
		}
		apiError(w, 400, fmt.Errorf("Cannot read request; image limit is 20 MiB"))
		return
	}
	defer r.MultipartForm.RemoveAll()
	prompt, model := r.FormValue("prompt"), r.FormValue("model")
	responseMode := r.FormValue("response_mode")
	if responseMode == "" {
		responseMode = "finish"
	}
	if responseMode != "finish" && responseMode != "text" {
		apiError(w, 400, fmt.Errorf("Unknown response mode"))
		return
	}
	if strings.TrimSpace(prompt) == "" || strings.TrimSpace(model) == "" {
		apiError(w, 400, fmt.Errorf("Prompt and model are required"))
		return
	}
	var media []sdk.Media
	attachment := ""
	files := r.MultipartForm.File["image"]
	if len(files) > 1 {
		apiError(w, 400, fmt.Errorf("Attach one image"))
		return
	}
	if len(files) == 1 {
		f, err := files[0].Open()
		if err != nil {
			apiError(w, 400, err)
			return
		}
		data, err := io.ReadAll(io.LimitReader(f, sdk.MaxMediaBytes+1))
		_ = f.Close()
		if err != nil || len(data) == 0 || len(data) > sdk.MaxMediaBytes {
			apiError(w, 400, fmt.Errorf("Image must contain 1 byte to 20 MiB"))
			return
		}
		kind := http.DetectContentType(data)
		if kind != "image/png" && kind != "image/jpeg" && kind != "image/gif" && kind != "image/webp" {
			apiError(w, 400, fmt.Errorf("Use PNG, JPEG, WebP or GIF"))
			return
		}
		media = []sdk.Media{{Data: data, MIMEType: kind}}
		attachment = filepath.Base(files[0].Filename)
	}
	a.mu.Lock()
	if a.job != nil && active(a.job.Status) {
		a.mu.Unlock()
		apiError(w, 409, fmt.Errorf("A request is already running"))
		return
	}
	ctx, cancel := context.WithCancel(a.ctx)
	job := &uiJob{ID: randomID(), Mode: a.mode, SDKVersion: sdk.Version, ResponseMode: responseMode, Status: "running", Phase: "starting", Model: model, Prompt: prompt, Attachment: attachment, StartedAt: time.Now(), UsageNote: "UNKNOWN until a completed native result is available", cancel: cancel}
	a.job = job
	a.wg.Add(1)
	snapshot := a.snapshotLocked()
	a.mu.Unlock()
	go a.generate(ctx, job, media)
	writeJSON(w, 202, snapshot)
}
func (a *uiServer) generate(ctx context.Context, job *uiJob, media []sdk.Media) {
	defer a.wg.Done()
	defer job.cancel()
	request := sdk.Request{Prompt: job.Prompt, Model: job.Model, Media: media, OnProgress: func(p sdk.ProgressEvent) {
		a.mu.Lock()
		defer a.mu.Unlock()
		job.Phase = p.Phase
		if p.Trajectory != nil {
			job.Trajectory = p.Trajectory
		}
	}}
	var result *sdk.Result
	var err error
	if job.ResponseMode == "text" {
		result, err = a.client.Complete(ctx, request)
	} else {
		result, err = a.client.Chat(ctx, request)
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	job.DurationMS = time.Since(job.StartedAt).Milliseconds()
	job.Result = result
	job.Status = "completed"
	if err != nil {
		job.Status = "error"
		if sdk.ErrorCode(err) == "cancelled" {
			job.Status = "cancelled"
		}
		var sdkError *sdk.Error
		if errors.As(err, &sdkError) {
			job.Error = sdkError
		} else {
			job.Error = &sdk.Error{Code: "stand_error", Message: err.Error()}
		}
	} else {
		job.UsageNote = "trajectory_model_steps"
	}
	path := filepath.Join(a.output, "request-"+job.ID+".json")
	job.Report = filepath.Base(path)
	data, saveErr := json.MarshalIndent(a.snapshotLocked(), "", "  ")
	if saveErr == nil {
		saveErr = os.WriteFile(path, data, 0600)
	}
	if saveErr != nil {
		job.Report = ""
		job.ReportError = saveErr.Error()
	}
}
func (a *uiServer) cancelJob(w http.ResponseWriter, r *http.Request) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.job == nil || a.job.ID != r.PathValue("id") {
		apiError(w, 404, fmt.Errorf("Request not found"))
		return
	}
	if active(a.job.Status) {
		a.job.Status = "cancelling"
		a.job.cancel()
	}
	writeJSON(w, 200, a.snapshotLocked())
}
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
func apiError(w http.ResponseWriter, status int, err error) {
	code := sdk.ErrorCode(err)
	if code == "" {
		code = "stand_error"
	}
	writeJSON(w, status, sdk.Object{"error": sdk.Object{"code": code, "message": err.Error()}})
}

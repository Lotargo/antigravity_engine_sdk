// Package antigravity connects directly to an installed Antigravity 2.x engine.
// Each Client owns a lazy native process. No Python runtime or agy CLI is used.
package antigravity

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"os"
	"path/filepath"
	"time"
)

const Version = "0.4.0"
const MaxMediaBytes = 20 * 1024 * 1024

// Object preserves native fields, including string token counters.
type Object = map[string]any

type Error struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Details any    `json:"details,omitempty"`
	Cause   error  `json:"-"`
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }
func (e *Error) Unwrap() error { return e.Cause }
func ErrorCode(err error) string {
	var e *Error
	if errors.As(err, &e) {
		return e.Code
	}
	return ""
}
func fault(code, message string, cause error) error {
	return &Error{Code: code, Message: message, Cause: cause}
}

type Installation struct {
	Executable string `json:"executable"`
	Version    string `json:"version"`
}
type Model struct {
	ID             string `json:"id"`
	NativeID       string `json:"native_id"`
	Name           string `json:"name"`
	SupportsImages bool   `json:"supports_images"`
	Raw            Object `json:"raw"`
}
type Authentication struct {
	Available          bool `json:"available"`
	EligibilityWarning any  `json:"eligibility_warning,omitempty"`
	Ineligible         bool `json:"ineligible"`
}
type Media struct {
	Data     []byte
	MIMEType string
}

func MediaFromFile(path string) (Media, error) {
	f, err := os.Open(path)
	if err != nil {
		return Media{}, err
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, MaxMediaBytes+1))
	if err != nil {
		return Media{}, err
	}
	m := Media{Data: b, MIMEType: mime.TypeByExtension(filepath.Ext(path))}
	if m.MIMEType == "" {
		m.MIMEType = "application/octet-stream"
	}
	if err = m.validate(); err != nil {
		return Media{}, err
	}
	return m, nil
}
func (m Media) validate() error {
	if len(m.Data) == 0 || len(m.Data) > MaxMediaBytes || m.MIMEType == "" {
		return fault("invalid_input", "Media requires 1 byte to 20 MiB and a MIME type.", nil)
	}
	return nil
}

type ProgressEvent struct {
	Phase      string `json:"phase"`
	CascadeID  string `json:"cascade_id,omitempty"`
	Trajectory Object `json:"trajectory,omitempty"`
}
type ImageArtifact struct {
	Data     []byte `json:"-"`
	MIMEType string `json:"mime_type"`
	Model    string `json:"model,omitempty"`
}

// Save creates a new file and refuses to replace existing files.
func (a ImageArtifact) Save(path string) error {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	_, err = f.Write(a.Data)
	closeErr := f.Close()
	if err != nil {
		return err
	}
	return closeErr
}

type Result struct {
	OutputMode        string          `json:"output_mode"`
	Output            Object          `json:"output"`
	RawOutput         string          `json:"raw_output"`
	RawResponseSHA256 string          `json:"raw_response_sha256"`
	Trajectory        Object          `json:"trajectory"`
	CascadeID         string          `json:"cascade_id"`
	Model             string          `json:"model"`
	NativeModel       string          `json:"native_model"`
	EngineVersion     string          `json:"engine_version"`
	LatencyMS         int64           `json:"latency_ms"`
	Usage             []Object        `json:"usage"`
	UsageScope        string          `json:"usage_scope"`
	Images            []ImageArtifact `json:"images,omitempty"`
}

func (r *Result) Text() (string, error) {
	s, ok := r.Output["text"].(string)
	if !ok {
		return "", fmt.Errorf("result has no text field; use Output")
	}
	return s, nil
}
func hash(s string) string { h := sha256.Sum256([]byte(s)); return hex.EncodeToString(h[:]) }

// Config's zero values select defaults. Installation bypasses discovery for an
// explicitly identified binary (also used by the isolated test stand).
type Config struct {
	Executable   string
	Installation *Installation
	Timeout      time.Duration
	ReadyTimeout time.Duration
	PollInterval time.Duration
}
type Request struct {
	Prompt         string
	Model          string
	Schema         Object
	Instructions   string
	Media          []Media
	Thinking       string
	MaxInvocations int
	OnProgress     func(ProgressEvent)
}
type ImageRequest struct {
	Prompt        string
	Planner       string
	ExpectedModel string
	Media         []Media
	Thinking      string
	OnProgress    func(ProgressEvent)
}
type RPCOptions struct{ Concurrent bool }

func object(v any) Object             { o, _ := v.(map[string]any); return o }
func field(o Object, k string) Object { return object(o[k]) }
func str(o Object, k string) string   { s, _ := o[k].(string); return s }
func flag(o Object, k string) bool    { b, _ := o[k].(bool); return b }
func list(o Object, k string) []any   { a, _ := o[k].([]any); return a }
func marshal(v any) string            { b, _ := json.Marshal(v); return string(b) }

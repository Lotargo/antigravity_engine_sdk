// Package mockengine is an offline protocol fixture shared by tests and the
// standalone stand. It never calls a provider and is not part of the SDK API.
package mockengine

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

const Environment = "ANTIGRAVITY_GO_MOCK_ENGINE"

type object = map[string]any

func obj(v any) object              { o, _ := v.(map[string]any); return o }
func str(o object, k string) string { s, _ := o[k].(string); return s }
func nested(o object, keys ...string) object {
	for _, key := range keys {
		o = obj(o[key])
	}
	return o
}

// MaybeRun re-enters the stand/test executable as a native protocol process.
// Only an explicit environment flag enables it; normal CLI flags are ignored.
func MaybeRun() {
	if os.Getenv(Environment) != "1" {
		return
	}
	run()
	os.Exit(0)
}
func run() {
	csrf := ""
	for i, a := range os.Args {
		if a == "--csrf_token" && i+1 < len(os.Args) {
			csrf = os.Args[i+1]
		}
	}
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		os.Exit(2)
	}
	scenario := os.Getenv("ANTIGRAVITY_GO_MOCK_SCENARIO")
	if scenario == "" {
		scenario = "success"
	}
	if scenario != "not_ready" {
		fmt.Printf("listening on random port at %d for HTTP\n", l.Addr().(*net.TCPAddr).Port)
	}
	s := &server{scenario: scenario, calls: map[string]int{}, messages: map[string]object{}}
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Codeium-Csrf-Token") != csrf || r.Header.Get("Connect-Protocol-Version") != "1" || r.Method != "POST" {
			http.Error(w, "bad transport", 403)
			return
		}
		var payload object
		if json.NewDecoder(r.Body).Decode(&payload) != nil {
			http.Error(w, "bad JSON", 400)
			return
		}
		s.mu.Lock()
		defer s.mu.Unlock()
		method := filepath.Base(r.URL.Path)
		s.calls[method]++
		out, status := s.handle(method, payload)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(out)
	})
	go func() { _, _ = io.Copy(io.Discard, os.Stdin); os.Exit(0) }()
	_ = http.Serve(l, h)
}

type server struct {
	mu       sync.Mutex
	scenario string
	calls    map[string]int
	messages map[string]object
	starts   []object
	sequence int
	lastSend object
}

func (s *server) handle(method string, p object) (object, int) {
	switch method {
	case "HasAuthToken":
		return object{"hasToken": s.scenario != "signed_out"}, 200
	case "GetAuthStatus":
		return object{"authResult": object{"hasValidAuth": true, "uiMessage": "native warning", "accessToken": "must-never-be-returned"}}, 200
	case "TestConfigure":
		s.scenario = str(p, "scenario")
		return object{}, 200
	case "TestState":
		return object{"calls": s.calls, "starts": s.starts, "last_send": s.lastSend, "api_key_inherited": os.Getenv("GEMINI_API_KEY") != "", "pid": os.Getpid()}, 200
	case "GetAvailableModels":
		cards := object{"mock-low": object{"model": "MODEL_MOCK", "displayName": "Offline fixture", "supportsImages": true}, "text-only": object{"model": "MODEL_TEXT"}, "internal": object{"model": "MODEL_INTERNAL", "isInternal": true}}
		if s.scenario == "catalog_changed" {
			delete(cards, "text-only")
			cards["new-model"] = object{"model": "MODEL_NEW", "providerFlag": true}
		}
		return object{"response": object{"models": cards}}, 200
	case "StartCascade":
		s.sequence++
		s.starts = append(s.starts, p)
		if s.scenario == "no_id" {
			return object{}, 200
		}
		return object{"cascadeId": fmt.Sprintf("mock-%d", s.sequence)}, 200
	case "SendUserCascadeMessage":
		s.messages[str(p, "cascadeId")] = p
		s.lastSend = p
		if s.scenario == "send_error" {
			return object{"error": "temporary provider failure"}, 503
		}
		return object{}, 200
	case "CancelCascadeInvocation":
		if s.scenario == "cancel_error" {
			return object{}, 503
		}
		return object{}, 200
	case "GetCascadeTrajectory":
		if s.scenario == "crash" {
			os.Exit(3)
		}
		message := s.messages[str(p, "cascadeId")]
		user := object{"type": "CORTEX_STEP_TYPE_USER_INPUT", "userInput": object{"media": message["media"]}}
		if s.scenario == "dropped_media" {
			user["userInput"] = object{}
		}
		steps := []any{user}
		if s.scenario == "running" || s.scenario == "cancel_error" {
			return object{"status": "CASCADE_RUN_STATUS_RUNNING", "trajectory": object{"steps": steps}}, 200
		}
		tools := nested(message, "cascadeConfig", "plannerConfig", "toolConfig")
		var schema object
		_ = json.Unmarshal([]byte(str(obj(tools["finish"]), "resultJsonSchemaString")), &schema)
		output := object{"text": "Native Go SDK answer"}
		props := obj(schema["properties"])
		if _, ok := props["answer"]; ok {
			output = object{"answer": float64(42), "nested": object{"ok": true}}
		}
		if _, ok := props["status"]; ok {
			output = object{"status": "created"}
			// One valid 1x1 PNG. Fixture bytes are explicitly mock evidence.
			data := "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
			image := object{"modelName": "mock-image", "generatedMedia": object{"mimeType": "image/png", "inlineData": data}}
			if s.scenario == "image_file" {
				bytes, _ := base64.StdEncoding.DecodeString(data)
				_ = os.WriteFile(filepath.Join(str(obj(tools["generateImage"]), "outputDirectory"), "fixture.png"), bytes, 0600)
				image["generatedMedia"] = object{"uri": "file:///ignored.png"}
			}
			if s.scenario == "image_missing" {
				image["generatedMedia"] = object{"uri": "https://example.invalid/never-download.png"}
			}
			if s.scenario == "image_invalid" {
				image["generatedMedia"] = object{"inlineData": "!!!!"}
			}
			steps = append(steps, object{"type": "CORTEX_STEP_TYPE_GENERATE_IMAGE", "generateImage": image})
		}
		raw, _ := json.Marshal(output)
		finish := object{"type": "CORTEX_STEP_TYPE_FINISH", "status": "CORTEX_STEP_STATUS_DONE", "finish": object{"outputString": string(raw)}, "metadata": object{"modelUsage": object{"inputTokens": "17", "outputTokens": "9"}}}
		switch s.scenario {
		case "invalid":
			finish["finish"] = object{"outputString": `{"answer":"wrong"}`}
		case "extra":
			output["unexpected"] = true
			raw, _ = json.Marshal(output)
			finish["finish"] = object{"outputString": string(raw)}
		case "invalid_json":
			finish["finish"] = object{"outputString": "broken"}
		case "null":
			finish["finish"] = object{"outputString": "null"}
		case "pending_finish":
			finish["status"] = "CORTEX_STEP_STATUS_PENDING"
		case "no_finish":
			finish = object{"plannerResponse": object{"response": "prose is not finish"}}
		case "retired":
			finish = object{"plannerResponse": object{"response": "This model is no longer available"}}
		case "refused":
			finish = object{"plannerResponse": object{"response": "I cannot help with this request"}}
		case "blocked":
			finish = object{"type": "CORTEX_STEP_TYPE_ERROR", "errorMessage": object{"message": "content_filter"}}
		case "failed":
			finish = object{"type": "CORTEX_STEP_TYPE_ERROR", "errorMessage": object{"message": "temporary failure"}}
		}
		steps = append(steps, finish)
		return object{"status": "CASCADE_RUN_STATUS_IDLE", "trajectory": object{"steps": steps}}, 200
	default:
		if strings.HasPrefix(method, "Test") {
			return object{}, 200
		}
		return object{}, 404
	}
}

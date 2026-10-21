// Standalone SDK acceptance stand. Defaults to a local protocol process.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"time"

	sdk "github.com/Lotargo/antigravity_engine_sdk/go"
	"github.com/Lotargo/antigravity_engine_sdk/go/internal/mockengine"
)

type check struct {
	Name       string `json:"name"`
	OK         bool   `json:"ok"`
	DurationMS int64  `json:"duration_ms"`
	Value      any    `json:"value,omitempty"`
	Error      string `json:"error,omitempty"`
	Code       string `json:"code,omitempty"`
}
type report struct {
	Date   string  `json:"date"`
	Mode   string  `json:"mode"`
	SDK    string  `json:"sdk_version"`
	Model  string  `json:"model"`
	Checks []check `json:"checks"`
	Note   string  `json:"note"`
}

func main() { mockengine.MaybeRun(); os.Exit(run()) }
func run() int {
	live := flag.Bool("live", false, "use installed Antigravity; generation cases make real model calls")
	selected := flag.String("case", "all", "all, catalog, text, plain, schema, vision, cancel, image, editor, jump, supercomplete")
	editorIdle := flag.Duration("editor-idle", 0, "idle after syncing real snapshots before an editor case")
	model := flag.String("model", "gemini-3.8-flash-low", "exact native catalog ID (text/planner)")
	expected := flag.String("expected-image-model", "", "assert actual image model; does not select it")
	output := flag.String("output", "", "new report directory; refuses existing paths")
	executable := flag.String("executable", "", "explicit native language_server path")
	timeout := flag.Duration("timeout", 180*time.Second, "per-request timeout including startup and queue")
	ui := flag.Bool("ui", false, "serve the local browser interface")
	listen := flag.String("listen", "127.0.0.1:8765", "loopback address for --ui")
	flag.Parse()
	valid := map[string]bool{"all": true, "catalog": true, "text": true, "plain": true, "schema": true, "vision": true, "cancel": true, "image": true, "editor": true, "jump": true, "supercomplete": true}
	if *editorIdle < 0 {
		fmt.Fprintln(os.Stderr, "--editor-idle must not be negative")
		return 2
	}
	if !valid[*selected] {
		fmt.Fprintln(os.Stderr, "unknown --case")
		return 2
	}
	mode := "mock"
	config := sdk.Config{Executable: *executable, Timeout: *timeout}
	if *live {
		mode = "live"
	} else {
		if err := os.Setenv(mockengine.Environment, "1"); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
		path, err := os.Executable()
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
		config.Executable = ""
		config.Installation = &sdk.Installation{Executable: path, Version: "2.0.0-mock"}
		*model = "mock-low"
		if *expected == "" {
			*expected = "mock-image"
		}
	}
	if *output == "" {
		*output = filepath.Join("reports", "go-"+mode+"-"+time.Now().Format("20060102-150405.000000000"))
	}
	if err := os.MkdirAll(filepath.Dir(*output), 0700); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	if err := os.Mkdir(*output, 0700); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	client, err := sdk.NewClient(config)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	defer client.Close()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	if *selected == "editor" || *selected == "jump" || *selected == "supercomplete" {
		if !*live {
			fmt.Fprintln(os.Stderr, "--case editor/jump/supercomplete requires --live (real native editor inference)")
			return 2
		}
		return runEditorScenario(ctx, config, *output, *selected, *editorIdle)
	}
	if *ui {
		return serveUI(ctx, client, mode, *model, *listen, *output, config)
	}
	r := report{Date: time.Now().Format("2006-01-02"), Mode: mode, SDK: sdk.Version, Model: *model, Checks: []check{}, Note: "Mock checks prove local protocol behavior only. Live results describe this engine/account/run. Usage covers trajectory model steps; cancelled usage and image price are UNKNOWN. Progress is polling, not token streaming."}
	passed := true
	var lastTrajectory sdk.Object
	runCheck := func(name string, fn func() (any, error)) bool {
		lastTrajectory = nil
		started := time.Now()
		value, err := fn()
		entry := check{Name: name, OK: err == nil, DurationMS: time.Since(started).Milliseconds(), Value: value}
		if err != nil {
			entry.Error = err.Error()
			entry.Code = sdk.ErrorCode(err)
			passed = false
			entry.Value = sdk.Object{"partial_trajectory": lastTrajectory, "usage": "UNKNOWN"}
		}
		r.Checks = append(r.Checks, entry)
		label := "PASS"
		if !entry.OK {
			label = "FAIL"
		}
		fmt.Printf("[%s] %s (%d ms)\n", label, name, entry.DurationMS)
		if err != nil {
			fmt.Println(" ", err)
		}
		return entry.OK
	}
	fmt.Printf("Antigravity Go SDK %s | %s | %s\n", sdk.Version, strings.ToUpper(mode), *model)
	if !runCheck("catalog", func() (any, error) { return client.Models(ctx, false) }) {
		return saveReport(*output, r, false)
	}
	if !runCheck("authentication", func() (any, error) { return client.Authentication(ctx) }) {
		return saveReport(*output, r, false)
	}
	lastProgress := ""
	progress := func(p sdk.ProgressEvent) {
		if p.Trajectory != nil {
			lastTrajectory = p.Trajectory
		}
		key := p.Phase + p.CascadeID
		if key != lastProgress {
			fmt.Printf("  %s %s\n", p.Phase, p.CascadeID)
			lastProgress = key
		}
	}
	if *selected == "all" || *selected == "text" {
		runCheck("text", func() (any, error) {
			return client.Chat(ctx, sdk.Request{Prompt: "Write one short sentence about a blue ceramic cup.", Model: *model, OnProgress: progress})
		})
	}
	if *selected == "plain" {
		runCheck("plain-text", func() (any, error) {
			return client.Complete(ctx, sdk.Request{Prompt: "Compute 17 * 23. Reply with only the number as plain text.", Model: *model, OnProgress: progress})
		})
	}
	if *selected == "all" || *selected == "schema" {
		runCheck("schema", func() (any, error) {
			result, err := client.Generate(ctx, sdk.Request{Prompt: "Return answer=42 and nested.ok=true.", Model: *model, Schema: answerSchema(), Thinking: "low", OnProgress: progress})
			if err == nil && (result.Output["answer"] != float64(42) || !boolField(result.Output, "nested", "ok")) {
				err = fmt.Errorf("semantic schema fixture values differ")
			}
			return result, err
		})
	}
	if *selected == "all" || *selected == "vision" {
		runCheck("vision", func() (any, error) {
			media := visionFixture()
			if err := os.WriteFile(filepath.Join(*output, "vision-input.png"), media.Data, 0600); err != nil {
				return nil, err
			}
			return client.Chat(ctx, sdk.Request{Prompt: "Name the colors of the four quadrants in the attached image, in reading order. Be concise.", Model: *model, Media: []sdk.Media{media}, OnProgress: progress})
		})
	}
	if *selected == "all" || *selected == "cancel" {
		runCheck("cancel-and-concurrent-catalog", func() (any, error) {
			if !*live {
				if _, err := client.RPC(ctx, "TestConfigure", sdk.Object{"scenario": "running"}, sdk.RPCOptions{}); err != nil {
					return nil, err
				}
			}
			cancelCtx, cancel := context.WithCancel(ctx)
			defer cancel()
			var metadataErr error
			var catalog sdk.Object
			_, err := client.Chat(cancelCtx, sdk.Request{Prompt: "Explain matrix multiplication in detail.", Model: *model, OnProgress: func(p sdk.ProgressEvent) {
				if p.Phase == "working" {
					catalog, metadataErr = client.RPC(ctx, "GetAvailableModels", nil, sdk.RPCOptions{Concurrent: true})
					cancel()
				}
			}})
			if metadataErr != nil {
				return nil, metadataErr
			}
			if sdk.ErrorCode(err) != "cancelled" {
				return nil, fmt.Errorf("expected cancelled; got %v", err)
			}
			return sdk.Object{"code": "cancelled", "usage": "UNKNOWN", "catalog_during_generation": catalog}, nil
		})
		if !*live {
			_, err = client.RPC(ctx, "TestConfigure", sdk.Object{"scenario": "success"}, sdk.RPCOptions{})
			if err != nil {
				fmt.Fprintln(os.Stderr, err)
				passed = false
			}
		}
		runCheck("recovery-after-cancel", func() (any, error) { return client.Chat(ctx, sdk.Request{Prompt: "Say ready.", Model: *model}) })
	}
	if *selected == "image" || (*selected == "all" && !*live) {
		runCheck("image", func() (any, error) {
			result, err := client.GenerateImage(ctx, sdk.ImageRequest{Prompt: "One blue ceramic cup with a yellow triangle on a white table. Square image.", Planner: *model, ExpectedModel: *expected, OnProgress: progress})
			if err != nil {
				return nil, err
			}
			for i, a := range result.Images {
				ext := map[string]string{"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}[a.MIMEType]
				if ext == "" {
					ext = ".bin"
				}
				if err = a.Save(filepath.Join(*output, fmt.Sprintf("image-%d%s", i+1, ext))); err != nil {
					return result, err
				}
			}
			return result, nil
		})
	}
	return saveReport(*output, r, passed)
}
func saveReport(directory string, r report, passed bool) int {
	b, err := json.MarshalIndent(r, "", "  ")
	if err == nil {
		err = os.WriteFile(filepath.Join(directory, "report.json"), b, 0600)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	absolute, _ := filepath.Abs(directory)
	fmt.Println("Report:", filepath.Join(absolute, "report.json"))
	if !passed {
		return 1
	}
	return 0
}
func answerSchema() sdk.Object {
	return sdk.Object{"type": "object", "properties": sdk.Object{"answer": sdk.Object{"type": "integer"}, "nested": sdk.Object{"$ref": "#/$defs/nested"}}, "required": []string{"answer", "nested"}, "additionalProperties": false, "$defs": sdk.Object{"nested": sdk.Object{"type": "object", "properties": sdk.Object{"ok": sdk.Object{"type": "boolean"}}, "required": []string{"ok"}, "additionalProperties": false}}}
}
func boolField(o sdk.Object, first, second string) bool {
	nested, _ := o[first].(map[string]any)
	b, _ := nested[second].(bool)
	return b
}
func visionFixture() sdk.Media {
	img := image.NewRGBA(image.Rect(0, 0, 256, 256))
	colors := []color.RGBA{{255, 0, 0, 255}, {0, 255, 0, 255}, {0, 0, 255, 255}, {255, 255, 0, 255}}
	for y := 0; y < 256; y++ {
		for x := 0; x < 256; x++ {
			img.SetRGBA(x, y, colors[(y/128)*2+x/128])
		}
	}
	var b bytes.Buffer
	_ = png.Encode(&b, img)
	return sdk.Media{Data: b.Bytes(), MIMEType: "image/png"}
}

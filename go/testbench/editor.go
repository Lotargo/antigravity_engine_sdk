package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	sdk "github.com/Lotargo/antigravity_engine_sdk/go"
)

func editorExample() (string, string, int) {
	before := "def calculate_total(prices):\n    tax_rate = 0.1\n    subtotal = sum(prices)\n"
	for i := 0; i < 25; i++ {
		before += fmt.Sprintf("    # pricing calculation step %d\n", i)
	}
	before += "    tax = subtotal * tax_rate\n    return subtotal + tax\n"
	after := strings.Replace(before, "tax_rate", "vat_rate", 1)
	return before, after, strings.Index(after, "vat_rate") + len("vat_rate")
}
func runEditorScenario(ctx context.Context, config sdk.Config, output string, independent bool, idle time.Duration) int {
	s, err := sdk.NewEditorSession(config)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	defer s.Close()
	before, after, cursor := editorExample()
	doc := sdk.EditorDocument{Name: "sample.py", Language: "python", Text: before, CursorOffset: cursor}
	if err = s.Sync(ctx, doc); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	doc.Text = after
	if err = s.Sync(ctx, doc); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	r := sdk.Object{"date": time.Now().Format("2006-01-02"), "sdk_version": sdk.Version, "before": before, "document": doc, "usage": "UNKNOWN", "model_selection": "native editor configuration; requested catalog ID is not an attribution of actual model"}
	if idle > 0 {
		fmt.Printf("Snapshots synced; idle for %s without edits or Supercomplete.\n", idle)
		started := time.Now()
		timer := time.NewTimer(idle)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			fmt.Fprintln(os.Stderr, ctx.Err())
			return 1
		case <-timer.C:
		}
		r["idle_ms"] = time.Since(started).Milliseconds()
	}
	code := 0
	kinds := []string{"supercomplete", "tab_jump"}
	if independent {
		kinds = []string{"tab_jump"}
	}
	for _, kind := range kinds {
		var result *sdk.EditorResult
		if kind == "supercomplete" {
			result, err = s.Supercomplete(ctx, nil)
		} else {
			result, err = s.TabToJump(ctx, nil)
		}
		r[kind] = result
		if err != nil {
			r[kind+"_error"] = err.Error()
			code = 1
			continue
		}
		preview, previewErr := result.Preview(after)
		if previewErr == nil {
			r[kind+"_preview"] = preview
		} else {
			r[kind+"_preview_error"] = previewErr.Error()
		}
		fmt.Printf("%s: %s (%d ms), frames=%d\n", kind, result.Status, result.LatencyMS, len(result.Frames))
		if result.Response != nil && result.Response.JumpPosition != nil {
			fmt.Printf("jump: row=%d col=%d (zero-based)\n", result.Response.JumpPosition.Row, result.Response.JumpPosition.Col)
		}
	}
	data, _ := json.MarshalIndent(r, "", "  ")
	if err = os.WriteFile(filepath.Join(output, "editor-report.json"), data, 0600); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	fmt.Println("Report:", filepath.Join(output, "editor-report.json"))
	return code
}

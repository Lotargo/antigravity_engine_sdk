package antigravity

import (
	"context"
	"strings"
	"testing"
)

func TestCompleteUsesToollessNativeText(t *testing.T) {
	c := fixture(t)
	r, err := c.Complete(context.Background(), Request{Prompt: "Complete this function:\nreturn ", Model: "mock-low", Instructions: "Only plain code."})
	if err != nil {
		t.Fatal(err)
	}
	text, err := r.Text()
	if err != nil || text != "Native Go SDK plain text answer" || r.RawOutput != text || r.OutputMode != "planner_text" {
		t.Fatal(r, err)
	}
	if r.Usage[0]["inputTokens"] != "17" || r.RawResponseSHA256 != hash(text) {
		t.Fatal("lost raw output or usage")
	}
	s := state(t, c)
	message := field(s, "last_send")
	planner := field(field(message, "cascadeConfig"), "plannerConfig")
	executor := field(field(message, "cascadeConfig"), "executorConfig")
	if _, exists := field(planner, "toolConfig")["finish"]; exists {
		t.Fatal("implicit finish tool")
	}
	if flag(executor, "requireFinishTool") || executor["maxGeneratorInvocations"] != float64(1) {
		t.Fatal(executor)
	}
	if str(object(list(message, "items")[0]), "text") != "Only plain code.\n\nComplete this function:\nreturn " {
		t.Fatal("rewrote caller prompt")
	}
	start := object(list(s, "starts")[0])
	tools := list(field(field(start, "customAgentSpec"), "customAgent"), "toolNames")
	if tools == nil || len(tools) != 0 {
		t.Fatal("tools not explicitly disabled", start)
	}
}
func TestCompleteDoesNotRelaxChatContract(t *testing.T) {
	c := fixture(t)
	scenario(t, c, "no_finish")
	_, err := c.Chat(context.Background(), Request{Prompt: "p", Model: "mock-low"})
	requireCode(t, err, "incompatible_format")
	_, err = c.Generate(context.Background(), Request{Prompt: "p", Model: "mock-low", Schema: answerSchema()})
	requireCode(t, err, "incompatible_format")
	_, err = c.Complete(context.Background(), Request{Prompt: "p", Model: "mock-low", Schema: answerSchema()})
	requireCode(t, err, "invalid_input")
}
func TestCompleteErrorOutcomes(t *testing.T) {
	for scenarioName, code := range map[string]string{"failed": "provider_error", "blocked": "content_rejected", "retired": "model_unavailable", "no_finish": "incompatible_format"} {
		t.Run(scenarioName, func(t *testing.T) {
			c := fixture(t)
			scenario(t, c, scenarioName)
			_, err := c.Complete(context.Background(), Request{Prompt: "p", Model: "mock-low"})
			requireCode(t, err, code)
		})
	}
}
func TestCompleteCancellationAndRecovery(t *testing.T) {
	c := fixture(t)
	scenario(t, c, "running")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	_, err := c.Complete(ctx, Request{Prompt: "p", Model: "mock-low", OnProgress: func(e ProgressEvent) {
		if e.Phase == "working" {
			cancel()
		}
	}})
	requireCode(t, err, "cancelled")
	if field(state(t, c), "calls")["CancelCascadeInvocation"] != float64(1) {
		t.Fatal("missing native cancellation")
	}
	scenario(t, c, "success")
	if _, err = c.Complete(context.Background(), Request{Prompt: "p", Model: "mock-low"}); err != nil {
		t.Fatal(err)
	}
}
func TestPlannerTextPreservesEscapesAndRejectsIncompleteSteps(t *testing.T) {
	raw := `return "literal\n"`
	trajectory := Object{"steps": []any{Object{"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE", "plannerResponse": Object{"response": raw}}, Object{"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_RUNNING", "plannerResponse": Object{"response": "unfinished"}}}}
	text, err := plannerText(trajectory)
	if err != nil || text != raw || strings.Contains(text, "unfinished") {
		t.Fatal(text, err)
	}
}

package antigravity

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"
)

var thinkingLevels = map[string]int{"minimal": 4, "low": 1, "medium": 2, "high": 3}
var providerBlock = regexp.MustCompile(`(?i)content[_ ]filter|content[_ ]policy|prohibited[_ ]content|blocklist|block_?reason|finish_?reason\W+safety|safety (filter|system|setting|block)|usage polic|flagged as potentially|invalid_prompt|moderation|responsible ai|unsafe content|\brecitation\b|\bspii\b`)
var refusal = regexp.MustCompile(`(?i)\bi (can't|cannot|can not|won't|will not|am unable to) (help|assist|create|generate|write|produce|provide|comply|continue)|\bi (must|have to|need to) decline|не могу (с этим )?(помочь|выполнить|создать|написать|сгенерировать|описать|продолжить)|вынужден[а]? отказаться`)

func textSchema() Object {
	return Object{"type": "object", "properties": Object{"text": Object{"type": "string"}}, "required": []string{"text"}, "additionalProperties": false}
}

// Chat uses the same strict finish contract as Generate, with a text field.
func (c *Client) Chat(ctx context.Context, request Request) (*Result, error) {
	request.Schema = textSchema()
	return c.Generate(ctx, request)
}
func (c *Client) Generate(ctx context.Context, request Request) (*Result, error) {
	return c.execute(ctx, request, false, "")
}
func (c *Client) GenerateImage(ctx context.Context, request ImageRequest) (*Result, error) {
	instructions := "Generate an actual image using generate_image, then call finish with a status string. Do not substitute a description or code for an image."
	if request.ExpectedModel != "" {
		instructions += " The expected image model is " + request.ExpectedModel + "."
	}
	r := Request{Prompt: request.Prompt, Model: request.Planner, Schema: Object{"type": "object", "properties": Object{"status": Object{"type": "string"}}, "required": []string{"status"}, "additionalProperties": false}, Instructions: instructions, Media: request.Media, Thinking: request.Thinking, MaxInvocations: 5, OnProgress: request.OnProgress}
	return c.execute(ctx, r, true, request.ExpectedModel)
}
func (c *Client) execute(parent context.Context, r Request, imageGeneration bool, expectedModel string) (result *Result, err error) {
	started := time.Now()
	ctx, cancel, err := c.requestContext(parent)
	if err != nil {
		return nil, err
	}
	defer cancel()
	if strings.TrimSpace(r.Prompt) == "" || strings.TrimSpace(r.Model) == "" || r.MaxInvocations < 0 {
		return nil, fault("invalid_input", "Prompt/model required; MaxInvocations must be positive or zero for default.", nil)
	}
	if r.Thinking != "" {
		if _, ok := thinkingLevels[r.Thinking]; !ok {
			return nil, fault("invalid_input", "Thinking must be minimal, low, medium or high.", nil)
		}
	}
	if r.MaxInvocations == 0 {
		r.MaxInvocations = 3
	}
	schema, validator, err := compileSchema(r.Schema)
	if err != nil {
		return nil, err
	}
	attachments := make([]any, 0, len(r.Media))
	for _, m := range r.Media {
		if err = m.validate(); err != nil {
			return nil, err
		}
		attachments = append(attachments, Object{"mimeType": m.MIMEType, "inlineData": base64.StdEncoding.EncodeToString(m.Data)})
	}
	emit := func(phase, id string, t Object) {
		if r.OnProgress != nil {
			r.OnProgress(ProgressEvent{phase, id, t})
		}
	}
	emit("starting", "", nil)
	e, release, err := c.acquire(ctx, false)
	if err != nil {
		return nil, err
	}
	defer release()
	defer func() { c.checkFault(e, err) }()
	native, err := e.rpc(ctx, "GetAvailableModels", nil)
	if err != nil {
		return nil, err
	}
	card := field(field(field(native, "response"), "models"), r.Model)
	if str(card, "model") == "" || flag(card, "isInternal") {
		return nil, fault("model_unavailable", "Selected model is unavailable in the current native catalog.", nil)
	}
	if len(attachments) > 0 && !flag(card, "supportsImages") {
		return nil, fault("incompatible_image_input", "Selected model does not support images.", nil)
	}
	directory, err := os.MkdirTemp(e.root, "request-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(directory)
	generated := filepath.Join(directory, "images")
	if err = os.Mkdir(generated, 0700); err != nil {
		return nil, err
	}
	tools := []string{"finish"}
	if imageGeneration {
		tools = []string{"generate_image", "finish"}
	}
	start, err := e.rpc(ctx, "StartCascade", Object{"source": "CORTEX_TRAJECTORY_SOURCE_SDK", "requestedModel": card["model"], "workspaceUris": []string{fileURI(directory)}, "backend": "BACKEND_TYPE_LOCAL", "customAgentSpec": Object{"customAgent": Object{"toolNames": tools}}})
	if err != nil {
		return nil, err
	}
	cascade := str(start, "cascadeId")
	if cascade == "" {
		return nil, fault("incompatible_format", "Engine returned no conversation ID.", nil)
	}
	complete := false
	defer func() {
		if !complete {
			// Cancellation must use a fresh budget, even after the caller deadline.
			stopCtx, stop := context.WithTimeout(context.Background(), time.Second)
			defer stop()
			if _, stopErr := e.rpc(stopCtx, "CancelCascadeInvocation", Object{"cascadeId": cascade, "killBackgroundTasks": true}); stopErr != nil {
				c.retire(e)
			}
		}
	}()
	toolConfig := Object{"finish": Object{"resultJsonSchemaString": marshal(schema)}, "autoInteractionBehavior": "AUTO_INTERACTION_BEHAVIOR_DENY_ALL"}
	if imageGeneration {
		toolConfig["generateImage"] = Object{"forceDisable": false, "outputDirectory": generated, "saveToParentArtifactDirectory": false}
	}
	planner := Object{"requestedModel": Object{"model": card["model"]}, "toolConfig": toolConfig}
	if r.Thinking != "" {
		planner["completionConfigOverride"] = Object{"thinkingLevel": thinkingLevels[r.Thinking]}
	}
	packageText := r.Instructions + "\n\n" + r.Prompt + "\n\nTRANSPORT OUTPUT\nCall finish with the required result object. Use plain text and real line breaks inside field values. Do not return the object as planner prose. Use only the supplied tools."
	message := Object{"cascadeId": cascade, "items": []any{Object{"text": packageText}}, "cascadeConfig": Object{"applyModelDefaultOverride": true, "plannerConfig": planner, "executorConfig": Object{"maxGeneratorInvocations": r.MaxInvocations, "requireFinishTool": true}}, "blocking": false}
	if len(attachments) > 0 {
		message["media"] = attachments
	}
	if _, err = e.rpc(ctx, "SendUserCascadeMessage", message); err != nil {
		return nil, err
	}
	emit("working", cascade, nil)
	var trajectory Object
	for {
		response, rpcErr := e.rpc(ctx, "GetCascadeTrajectory", Object{"cascadeId": cascade, "verbosity": "CLIENT_TRAJECTORY_VERBOSITY_PROD_UI"})
		if rpcErr != nil {
			return nil, rpcErr
		}
		trajectory = field(response, "trajectory")
		emit("trajectory", cascade, trajectory)
		if str(response, "status") == "CASCADE_RUN_STATUS_IDLE" && len(list(trajectory, "steps")) > 1 {
			complete = true
			break
		}
		if err = pause(ctx, c.config.PollInterval); err != nil {
			return nil, err
		}
	}
	raw, err := finish(trajectory)
	if err != nil {
		return nil, err
	}
	var output Object
	if err = json.Unmarshal([]byte(raw), &output); err != nil || output == nil {
		return nil, fault("contract_violation", "Native finish is not a JSON object.", err)
	}
	if err = validator.Validate(output); err != nil {
		return nil, fault("contract_violation", "Native finish violates the requested schema.", err)
	}
	steps := list(trajectory, "steps")
	if len(attachments) > 0 {
		received := false
		for _, v := range steps {
			if len(list(field(object(v), "userInput"), "media")) > 0 {
				received = true
			}
		}
		if !received {
			return nil, fault("incompatible_image_input", "Engine did not retain attached media.", nil)
		}
	}
	var images []ImageArtifact
	if imageGeneration {
		images, err = collectImages(steps, generated, expectedModel)
		if err != nil {
			return nil, err
		}
	}
	usage := make([]Object, 0)
	for _, v := range steps {
		if u := field(field(object(v), "metadata"), "modelUsage"); len(u) > 0 {
			usage = append(usage, u)
		}
	}
	emit("completed", cascade, nil)
	return &Result{Output: output, RawOutput: raw, RawResponseSHA256: hash(raw), Trajectory: trajectory, CascadeID: cascade, Model: r.Model, NativeModel: str(card, "model"), EngineVersion: e.installation.Version, LatencyMS: time.Since(started).Milliseconds(), Usage: usage, UsageScope: "trajectory_model_steps", Images: images}, nil
}
func fileURI(path string) string {
	s := filepath.ToSlash(path)
	if runtime.GOOS == "windows" {
		s = "/" + s
	}
	return (&url.URL{Scheme: "file", Path: s}).String()
}
func finish(trajectory Object) (string, error) {
	steps := list(trajectory, "steps")
	for _, v := range steps {
		s := object(v)
		if strings.Contains(str(s, "type"), "ERROR") || str(s, "status") == "CORTEX_STEP_STATUS_ERROR" || str(s, "status") == "CORTEX_STEP_STATUS_FAILED" {
			code := "provider_error"
			if providerBlock.MatchString(marshal(s["errorMessage"])) {
				code = "content_rejected"
			}
			return "", &Error{Code: code, Message: "Native request failed.", Details: s["errorMessage"]}
		}
	}
	for i := len(steps) - 1; i >= 0; i-- {
		s := object(steps[i])
		if str(s, "type") == "CORTEX_STEP_TYPE_FINISH" && str(s, "status") != "CORTEX_STEP_STATUS_RUNNING" && str(s, "status") != "CORTEX_STEP_STATUS_PENDING" {
			f := field(s, "finish")
			out := str(f, "outputString")
			if out == "" {
				out = str(field(f, "output"), "arguments_json")
			}
			if strings.TrimSpace(out) != "" {
				return out, nil
			}
		}
	}
	for _, v := range steps {
		message := str(field(object(v), "plannerResponse"), "response")
		lower := strings.ToLower(message)
		if strings.Contains(lower, "is no longer available") || strings.Contains(lower, "is no longer supported") || strings.Contains(lower, "has been retired") {
			return "", fault("model_unavailable", "Selected model has been retired.", nil)
		}
		if refusal.MatchString(message) || providerBlock.MatchString(message) {
			return "", fault("content_rejected", "Model declined the request.", nil)
		}
	}
	return "", fault("incompatible_format", "Engine returned no native finish result.", nil)
}

package antigravity

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

type localOnlyLoader struct{}

func (localOnlyLoader) Load(url string) (any, error) {
	return nil, fmt.Errorf("external schema resource blocked: %s", url)
}
func compileSchema(input Object) (Object, *jsonschema.Schema, error) {
	b, err := json.Marshal(input)
	if err != nil {
		return nil, nil, fault("invalid_schema", "Schema is not JSON.", err)
	}
	var schema Object
	if err = json.Unmarshal(b, &schema); err != nil || str(schema, "type") != "object" {
		return nil, nil, fault("invalid_schema", "finish requires an object JSON Schema.", err)
	}
	if err = localReferences(schema); err != nil {
		return nil, nil, fault("invalid_schema", "Only local fragment references are supported.", err)
	}
	c := jsonschema.NewCompiler()
	c.UseLoader(localOnlyLoader{})
	if err = c.AddResource("https://sdk.invalid/result.json", schema); err != nil {
		return nil, nil, fault("invalid_schema", "Invalid JSON Schema.", err)
	}
	compiled, err := c.Compile("https://sdk.invalid/result.json")
	if err != nil {
		return nil, nil, fault("invalid_schema", "Invalid JSON Schema or unresolved local reference.", err)
	}
	return schema, compiled, nil
}
func localReferences(v any) error {
	switch x := v.(type) {
	case map[string]any:
		for k, item := range x {
			if k == "$ref" || k == "$dynamicRef" || k == "$recursiveRef" {
				if s, ok := item.(string); ok && !strings.HasPrefix(s, "#") {
					return fmt.Errorf("external reference: %s", s)
				}
			}
			if err := localReferences(item); err != nil {
				return err
			}
		}
	case []any:
		for _, item := range x {
			if err := localReferences(item); err != nil {
				return err
			}
		}
	}
	return nil
}

package antigravity

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"io"
	"mime"
	"net/http"
	"strconv"
)

// streamRPC implements uncompressed Connect server streaming. Cancellation
// closes the HTTP body through the request context; no Cascade RPC is involved.
func (e *engine) streamRPC(ctx context.Context, method string, payload Object, onFrame func(json.RawMessage) error, terminal *json.RawMessage) error {
	if err := e.check(ctx); err != nil {
		return err
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		return fault("invalid_input", "Cannot encode streaming request.", err)
	}
	body := make([]byte, 5+len(raw))
	binary.BigEndian.PutUint32(body[1:5], uint32(len(raw)))
	copy(body[5:], raw)
	e.mu.Lock()
	port := e.port
	e.mu.Unlock()
	req, err := http.NewRequestWithContext(ctx, "POST", "http://127.0.0.1:"+strconv.Itoa(port)+service+method, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/connect+json")
	req.Header.Set("Connect-Protocol-Version", "1")
	req.Header.Set("X-Codeium-Csrf-Token", e.csrf)
	resp, err := e.http.Do(req)
	if err != nil {
		if check := e.check(ctx); check != nil {
			return check
		}
		return fault("engine_connection", "Cannot open native stream.", nil)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		code := "provider_error"
		if resp.StatusCode == 401 || resp.StatusCode == 403 {
			code = "authentication_required"
		}
		return &Error{Code: code, Message: "Native stream failed.", Details: Object{"method": method, "http_status": resp.StatusCode}}
	}
	kind, _, _ := mime.ParseMediaType(resp.Header.Get("Content-Type"))
	if kind != "application/connect+json" {
		return fault("incompatible_format", "Unexpected native stream content type.", nil)
	}
	total := uint64(0)
	for {
		var header [5]byte
		if _, err = io.ReadFull(resp.Body, header[:]); err != nil {
			if check := e.check(ctx); check != nil {
				return check
			}
			return fault("incompatible_format", "Stream ended without a complete terminal frame.", nil)
		}
		size := binary.BigEndian.Uint32(header[1:])
		total += uint64(size) + 5
		if (header[0] != 0 && header[0] != 2) || size > 16*1024*1024 || total > 64*1024*1024 {
			return fault("incompatible_format", "Unsupported or oversized Connect frame.", nil)
		}
		raw = make([]byte, size)
		if _, err = io.ReadFull(resp.Body, raw); err != nil {
			if check := e.check(ctx); check != nil {
				return check
			}
			return fault("incompatible_format", "Truncated Connect frame body.", nil)
		}
		var value Object
		if json.Unmarshal(raw, &value) != nil || value == nil {
			return fault("incompatible_format", "Connect frame is not a JSON object.", nil)
		}
		if header[0] == 2 {
			*terminal = append(json.RawMessage(nil), raw...)
			if value["error"] != nil {
				return fault("provider_error", "Native stream returned a terminal error; inspect the terminal frame.", nil)
			}
			return e.check(ctx)
		}
		if err = onFrame(raw); err != nil {
			return err
		}
	}
}

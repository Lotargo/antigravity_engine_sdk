package antigravity

import (
	"context"
	"sort"
	"sync"
	"time"
)

// Client owns a native engine. Calls are serialized unless RPC explicitly opts
// into concurrency. Close is terminal and interrupts queued/active calls.
type Client struct {
	config Config
	mu     sync.Mutex
	engine *engine
	closed bool
	gate   chan struct{}
	ctx    context.Context
	cancel context.CancelFunc
}

func NewClient(config Config) (*Client, error) {
	if config.Executable != "" && config.Installation != nil {
		return nil, fault("invalid_input", "Choose Executable or Installation.", nil)
	}
	if config.Timeout < 0 || config.ReadyTimeout < 0 || config.PollInterval < 0 {
		return nil, fault("invalid_input", "Durations must be positive or zero for defaults.", nil)
	}
	if config.Timeout == 0 {
		config.Timeout = 240 * time.Second
	}
	if config.ReadyTimeout == 0 {
		config.ReadyTimeout = 60 * time.Second
	}
	if config.PollInterval == 0 {
		config.PollInterval = 200 * time.Millisecond
	}
	if config.Installation != nil {
		i := *config.Installation
		if i.Executable == "" || i.Version == "" {
			return nil, fault("invalid_input", "Installation requires executable and version.", nil)
		}
		config.Installation = &i
	}
	ctx, cancel := context.WithCancel(context.Background())
	return &Client{config: config, gate: make(chan struct{}, 1), ctx: ctx, cancel: cancel}, nil
}
func (c *Client) Close() error {
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return nil
	}
	c.closed = true
	c.cancel()
	e := c.engine
	c.engine = nil
	c.mu.Unlock()
	if e != nil {
		e.close()
	}
	return nil
}
func (c *Client) retire(e *engine) {
	c.mu.Lock()
	if c.engine == e {
		c.engine = nil
	}
	c.mu.Unlock()
	e.close()
}
func (c *Client) requestContext(parent context.Context) (context.Context, func(), error) {
	if parent == nil {
		return nil, nil, fault("invalid_input", "Context is required.", nil)
	}
	c.mu.Lock()
	closed := c.closed
	c.mu.Unlock()
	if closed {
		return nil, nil, fault("client_closed", "Client is closed.", nil)
	}
	ctx, cancel := context.WithTimeout(parent, c.config.Timeout)
	stop := context.AfterFunc(c.ctx, cancel)
	return ctx, func() { stop(); cancel() }, nil
}
func (c *Client) acquire(ctx context.Context, concurrent bool) (*engine, func(), error) {
	if ctx.Err() != nil {
		return nil, nil, contextError(ctx.Err())
	}
	c.mu.Lock()
	e := c.engine
	c.mu.Unlock()
	if concurrent && e != nil && e.usable() {
		return e, func() {}, nil
	}
	select {
	case <-ctx.Done():
		return nil, nil, contextError(ctx.Err())
	case c.gate <- struct{}{}:
	}
	release := func() { <-c.gate }
	if ctx.Err() != nil {
		release()
		return nil, nil, contextError(ctx.Err())
	}
	c.mu.Lock()
	e = c.engine
	closed := c.closed
	c.mu.Unlock()
	if closed {
		release()
		return nil, nil, fault("cancelled", "Client closed during request.", nil)
	}
	if e == nil || !e.usable() {
		if e != nil {
			c.retire(e)
		}
		installation := c.config.Installation
		if installation == nil {
			var err error
			installation, err = FindInstallation(c.config.Executable)
			if err != nil {
				release()
				return nil, nil, err
			}
		}
		e = newEngine(*installation)
		c.mu.Lock()
		if c.closed {
			c.mu.Unlock()
			release()
			return nil, nil, fault("cancelled", "Client closed during request.", nil)
		}
		c.engine = e
		c.mu.Unlock()
		if err := e.start(ctx, c.config.ReadyTimeout); err != nil {
			c.retire(e)
			release()
			return nil, nil, err
		}
	}
	return e, release, nil
}
func (c *Client) checkFault(e *engine, err error) {
	if err == nil {
		return
	}
	switch ErrorCode(err) {
	case "engine_connection", "engine_exited", "authentication_required":
		c.retire(e)
	}
}

// RPC exposes the installed engine's internal protocol. Concurrent is intended
// for independent metadata reads; callers own ordering of arbitrary mutations.
func (c *Client) RPC(parent context.Context, method string, payload Object, options RPCOptions) (Object, error) {
	if !methodPattern.MatchString(method) {
		return nil, fault("invalid_input", "Invalid RPC method.", nil)
	}
	ctx, cancel, err := c.requestContext(parent)
	if err != nil {
		return nil, err
	}
	defer cancel()
	e, release, err := c.acquire(ctx, options.Concurrent)
	if err != nil {
		return nil, err
	}
	defer release()
	result, err := e.rpc(ctx, method, payload)
	c.checkFault(e, err)
	return result, err
}
func (c *Client) Models(ctx context.Context, includeInternal bool) ([]Model, error) {
	native, err := c.RPC(ctx, "GetAvailableModels", nil, RPCOptions{})
	if err != nil {
		return nil, err
	}
	cards := field(field(native, "response"), "models")
	models := make([]Model, 0, len(cards))
	for id, value := range cards {
		card := object(value)
		if str(card, "model") == "" || (!includeInternal && flag(card, "isInternal")) {
			continue
		}
		name := str(card, "displayName")
		if name == "" {
			name = id
		}
		models = append(models, Model{id, str(card, "model"), name, flag(card, "supportsImages"), card})
	}
	sort.Slice(models, func(i, j int) bool { return models[i].ID < models[j].ID })
	return models, nil
}
func (c *Client) Authentication(ctx context.Context) (*Authentication, error) {
	native, err := c.RPC(ctx, "GetAuthStatus", nil, RPCOptions{})
	if err != nil {
		return nil, err
	}
	auth := field(native, "authResult")
	_, ineligible := auth["ineligible"]
	return &Authentication{flag(auth, "hasValidAuth"), auth["uiMessage"], ineligible}, nil
}

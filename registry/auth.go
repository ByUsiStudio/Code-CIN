package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"
)

var ErrUnauthorized = errors.New("unauthorized")

type User struct {
	Sub      string `json:"sub"`
	Name     string `json:"name"`
	Email    string `json:"email"`
	Nickname string `json:"nickname"`
}

func (u User) Publisher() Publisher {
	nick := u.Nickname
	if nick == "" {
		nick = u.Name
	}
	return Publisher{ID: u.Sub, Email: u.Email, Nickname: nick}
}

type authCacheEntry struct {
	user    User
	expires time.Time
	err     error
}

type Authenticator struct {
	baseURL string
	client  *http.Client
	ttl     time.Duration
	negTTL  time.Duration
	mu      sync.Mutex
	cache   map[string]authCacheEntry
}

func NewAuthenticator(baseURL string, client *http.Client) *Authenticator {
	return &Authenticator{
		baseURL: strings.TrimRight(baseURL, "/"),
		client:  client,
		ttl:     5 * time.Minute,
		negTTL:  30 * time.Second,
		cache:   map[string]authCacheEntry{},
	}
}

func (a *Authenticator) Authenticate(token string) (User, error) {
	token = strings.TrimSpace(token)
	if token == "" {
		return User{}, ErrUnauthorized
	}
	sum := sha256.Sum256([]byte(token))
	key := hex.EncodeToString(sum[:])
	now := time.Now()

	a.mu.Lock()
	if entry, ok := a.cache[key]; ok && now.Before(entry.expires) {
		a.mu.Unlock()
		return entry.user, entry.err
	}
	a.mu.Unlock()

	user, err := a.fetch(token)
	entry := authCacheEntry{user: user, err: err}
	if err != nil {
		entry.expires = now.Add(a.negTTL)
	} else {
		entry.expires = now.Add(a.ttl)
	}
	a.mu.Lock()
	a.cache[key] = entry
	a.mu.Unlock()
	return user, err
}

func (a *Authenticator) fetch(token string) (User, error) {
	if a.baseURL == "" {
		return User{}, errors.New("CDIFIT_API_BASE is not configured")
	}
	body := bytes.NewBufferString("{}")
	req, err := http.NewRequest(http.MethodPost,
		a.baseURL+"/api/v4/session/oauth/userinfo", body)
	if err != nil {
		return User{}, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "codecin-pic-registry/"+serverVersion)

	resp, err := a.client.Do(req)
	if err != nil {
		return User{}, fmt.Errorf("cdifit userinfo: %w", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return User{}, ErrUnauthorized
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return User{}, fmt.Errorf("cdifit userinfo: HTTP %d: %s",
			resp.StatusCode, strings.TrimSpace(string(raw)))
	}
	var payload struct {
		Code int    `json:"code"`
		Msg  string `json:"msg"`
		Data *User  `json:"data"`
		User
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		return User{}, fmt.Errorf("cdifit userinfo: invalid JSON: %w", err)
	}
	user := payload.User
	if payload.Data != nil {
		user = *payload.Data
	}
	if payload.Code != 0 && payload.Code != 200 {
		return User{}, fmt.Errorf("cdifit userinfo: %s", payload.Msg)
	}
	if user.Sub == "" {
		return User{}, errors.New("cdifit userinfo: missing sub")
	}
	return user, nil
}

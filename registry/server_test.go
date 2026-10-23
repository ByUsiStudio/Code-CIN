package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const testToken = "test-token"

func newTestApp(t *testing.T) (*App, *httptest.Server) {
	t.Helper()
	authSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v4/session/oauth/userinfo" {
			http.NotFound(w, r)
			return
		}
		if r.Header.Get("Authorization") != "Bearer "+testToken {
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = w.Write([]byte(`{"code":401,"msg":"unauthorized"}`))
			return
		}
		_, _ = w.Write([]byte(`{"sub":"u1","email":"user@example.com","name":"Tester"}`))
	}))
	t.Cleanup(authSrv.Close)

	store, err := NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	app := NewApp(store, NewAuthenticator(authSrv.URL,
		&http.Client{Timeout: 5 * time.Second}), "https://package.codecin.org.cn",
		testLogger(t))
	srv := httptest.NewServer(app.routes())
	t.Cleanup(srv.Close)
	return app, srv
}

func testLogger(t *testing.T) *log.Logger {
	t.Helper()
	return log.New(io.Discard, "", 0)
}

func request(t *testing.T, method, url string, body []byte,
	contentType string) (*http.Response, []byte) {
	t.Helper()
	req, err := http.NewRequest(method, url, bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if body != nil {
		req.Header.Set("Content-Type", contentType)
	}
	req.Header.Set("Authorization", "Bearer "+testToken)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	return resp, raw
}

func TestPublishArtifactAndYank(t *testing.T) {
	app, srv := newTestApp(t)
	archive := []byte("fake-cinpkg")
	sum := sha256.Sum256(archive)
	archiveSHA := hex.EncodeToString(sum[:])

	meta := map[string]any{
		"repo": "https://github.com/u/hello", "tag": "v1.0.0",
		"rev": "abc123", "source_sha256": strings.Repeat("a", 64),
		"manifest_sha256": strings.Repeat("b", 64),
		"archive_sha256":  archiveSHA, "archive_size": len(archive),
	}
	raw, _ := json.Marshal(meta)
	resp, body := request(t, http.MethodPut,
		srv.URL+"/api/v1/packages/hello/1.0.0", raw, "application/json")
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("publish: %d %s", resp.StatusCode, body)
	}

	resp, body = request(t, http.MethodPost,
		srv.URL+"/api/v1/packages/hello/1.0.0/artifact?sha256="+archiveSHA,
		archive, "application/octet-stream")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("artifact: %d %s", resp.StatusCode, body)
	}
	var uploaded struct {
		Data struct {
			URL string `json:"url"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &uploaded); err != nil {
		t.Fatal(err)
	}
	if uploaded.Data.URL == "" {
		t.Fatal("artifact URL missing")
	}

	resp, body = request(t, http.MethodGet,
		srv.URL+"/api/v1/packages/hello", nil, "")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("get: %d %s", resp.StatusCode, body)
	}
	var envelope struct {
		OK   bool    `json:"ok"`
		Data Package `json:"data"`
	}
	if err := json.Unmarshal(body, &envelope); err != nil {
		t.Fatal(err)
	}
	pkg := envelope.Data
	if pkg.Versions["1.0.0"] == nil {
		t.Fatalf("version missing: %s", body)
	}
	if pkg.Versions["1.0.0"].ArchiveSHA256 != archiveSHA {
		t.Fatalf("archive digest: %+v", pkg.Versions["1.0.0"])
	}
	if pkg.Versions["1.0.0"].Publisher.ID != "u1" {
		t.Fatalf("publisher: %+v", pkg.Versions["1.0.0"].Publisher)
	}

	resp, got := request(t, http.MethodGet,
		srv.URL+"/api/v1/packages/hello/1.0.0/artifact", nil, "")
	if resp.StatusCode != http.StatusOK || !bytes.Equal(got, archive) {
		t.Fatalf("download: %d %q", resp.StatusCode, got)
	}
	if resp.Header.Get("X-PIC-SHA256") != archiveSHA {
		t.Fatalf("checksum header: %q", resp.Header.Get("X-PIC-SHA256"))
	}

	// 相同内容重复发布是幂等的
	resp, body = request(t, http.MethodPut,
		srv.URL+"/api/v1/packages/hello/1.0.0", raw, "application/json")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("republish: %d %s", resp.StatusCode, body)
	}
	// 不同内容必须拒绝
	meta["archive_sha256"] = strings.Repeat("c", 64)
	raw, _ = json.Marshal(meta)
	resp, body = request(t, http.MethodPut,
		srv.URL+"/api/v1/packages/hello/1.0.0", raw, "application/json")
	if resp.StatusCode != http.StatusConflict {
		t.Fatalf("conflict expected: %d %s", resp.StatusCode, body)
	}

	resp, body = request(t, http.MethodPost,
		srv.URL+"/api/v1/packages/hello/1.0.0/yank", []byte(`{"yanked":true}`),
		"application/json")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("yank: %d %s", resp.StatusCode, body)
	}
	rawPkg, err := os.ReadFile(filepath.Join(app.store.root, "packages", "hello.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(rawPkg), `"yanked": true`) {
		t.Fatalf("yanked flag missing: %s", rawPkg)
	}
}

func TestListAndValidation(t *testing.T) {
	_, srv := newTestApp(t)
	archiveSHA := strings.Repeat("d", 64)
	meta := map[string]any{
		"repo": "https://github.com/u/hello", "tag": "v1.0.0",
		"source_sha256":   strings.Repeat("a", 64),
		"manifest_sha256": strings.Repeat("b", 64),
		"archive_sha256":  archiveSHA, "archive_size": 3,
	}
	raw, _ := json.Marshal(meta)
	resp, body := request(t, http.MethodPut,
		srv.URL+"/api/v1/packages/hello/1.0.0", raw, "application/json")
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("publish: %d %s", resp.StatusCode, body)
	}
	resp, body = request(t, http.MethodGet,
		srv.URL+"/api/v1/packages?q=hello", nil, "")
	if resp.StatusCode != http.StatusOK || !strings.Contains(string(body), `"latest":"1.0.0"`) {
		t.Fatalf("list: %d %s", resp.StatusCode, body)
	}
	resp, _ = request(t, http.MethodPut,
		srv.URL+"/api/v1/packages/bad%20name/1.0.0", raw, "application/json")
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("invalid name expected 400, got %d", resp.StatusCode)
	}

	// 未认证发布
	req, _ := http.NewRequest(http.MethodPut,
		srv.URL+"/api/v1/packages/hello/2.0.0", bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	resp2, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp2.Body.Close()
	if resp2.StatusCode != http.StatusUnauthorized {
		t.Fatalf("auth expected 401, got %d", resp2.StatusCode)
	}
}

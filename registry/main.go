package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
)

const serverVersion = "6.2.0"

const (
	maxMetadataBytes = 1 << 20
	maxArtifactBytes = 256 << 20
)

type App struct {
	store     *Store
	auth      *Authenticator
	publicURL string
	logger    *log.Logger
	started   time.Time
}

type publishRequest struct {
	Name           string `json:"name"`
	Version        string `json:"version"`
	Repo           string `json:"repo"`
	Tag            string `json:"tag"`
	Rev            string `json:"rev"`
	SourceSHA256   string `json:"source_sha256"`
	ManifestSHA256 string `json:"manifest_sha256"`
	ArchiveSHA256  string `json:"archive_sha256"`
	ArchiveSize    int64  `json:"archive_size"`
}

func NewApp(store *Store, auth *Authenticator, publicURL string,
	logger *log.Logger) *App {
	return &App{
		store:     store,
		auth:      auth,
		publicURL: strings.TrimRight(publicURL, "/"),
		logger:    logger,
		started:   time.Now(),
	}
}

func main() {
	addr := envOr("PIC_SERVER_ADDR", ":8080")
	dataDir := envOr("PIC_SERVER_DATA", "./data")
	publicURL := envOr("PIC_PUBLIC_URL", "https://package.codecin.org.cn")
	cdifitBase := envOr("CDIFIT_API_BASE", "https://www.cdifit.cn")

	logger := log.New(os.Stdout, "pic-registry ", log.LstdFlags|log.LUTC)
	store, err := NewStore(dataDir)
	if err != nil {
		logger.Fatalf("store: %v", err)
	}
	client := &http.Client{Timeout: 15 * time.Second}
	app := NewApp(store, NewAuthenticator(cdifitBase, client), publicURL, logger)

	srv := &http.Server{
		Addr:              addr,
		Handler:           app.routes(),
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       60 * time.Second,
		WriteTimeout:      300 * time.Second,
		IdleTimeout:       120 * time.Second,
	}
	logger.Printf("starting version=%s addr=%s data=%s public=%s cdifit=%s",
		serverVersion, addr, dataDir, publicURL, cdifitBase)
	if err := srv.ListenAndServe(); err != nil &&
		!errors.Is(err, http.ErrServerClosed) {
		logger.Fatalf("serve: %v", err)
	}
}

func envOr(key, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}

func (a *App) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/v1/health", a.handleHealth)
	mux.HandleFunc("GET /api/v1/packages", a.handleList)
	mux.HandleFunc("GET /api/v1/packages/{name}", a.handleGetPackage)
	mux.HandleFunc("GET /api/v1/packages/{name}/{version}", a.handleGetVersion)
	mux.HandleFunc("PUT /api/v1/packages/{name}/{version}", a.handlePublish)
	mux.HandleFunc("POST /api/v1/packages/{name}/{version}/artifact", a.handleArtifactUpload)
	mux.HandleFunc("GET /api/v1/packages/{name}/{version}/artifact", a.handleArtifactDownload)
	mux.HandleFunc("POST /api/v1/packages/{name}/{version}/yank", a.handleYank)
	return a.withLogging(mux)
}

func (a *App) handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true,
		"data": map[string]any{
			"status":  "ok",
			"version": serverVersion,
			"time":    time.Now().UTC(),
			"uptime":  time.Since(a.started).String(),
		},
	})
}

func (a *App) handleList(w http.ResponseWriter, r *http.Request) {
	query := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("q")))
	packages, err := a.store.List()
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", err.Error())
		return
	}
	summaries := make([]PackageSummary, 0, len(packages))
	for _, pkg := range packages {
		if query != "" &&
			!strings.Contains(strings.ToLower(pkg.Name), query) &&
			!strings.Contains(strings.ToLower(pkg.Repo), query) {
			continue
		}
		summaries = append(summaries, PackageSummary{
			Name:      pkg.Name,
			Repo:      pkg.Repo,
			Latest:    Latest(pkg.Versions),
			Versions:  len(pkg.Versions),
			UpdatedAt: pkg.UpdatedAt,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":   true,
		"data": map[string]any{"packages": summaries, "count": len(summaries)},
	})
}

func (a *App) handleGetPackage(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("name")
	pkg, err := a.store.Get(name)
	if errors.Is(err, ErrNotFound) {
		writeError(w, http.StatusNotFound, "not_found", "package not found")
		return
	}
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "data": pkg})
}

func (a *App) handleGetVersion(w http.ResponseWriter, r *http.Request) {
	name, version := r.PathValue("name"), r.PathValue("version")
	pkg, err := a.store.Get(name)
	if errors.Is(err, ErrNotFound) {
		writeError(w, http.StatusNotFound, "not_found", "package not found")
		return
	}
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	entry, ok := pkg.Versions[version]
	if !ok {
		writeError(w, http.StatusNotFound, "not_found", "version not found")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true,
		"data": map[string]any{
			"name": name, "repo": pkg.Repo, "version": entry,
		},
	})
}

func (a *App) handlePublish(w http.ResponseWriter, r *http.Request) {
	user, ok := a.requireUser(w, r)
	if !ok {
		return
	}
	name, version := r.PathValue("name"), r.PathValue("version")
	if err := validateName(name); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_name", err.Error())
		return
	}
	if err := validateVersion(version); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_version", err.Error())
		return
	}
	var req publishRequest
	if err := decodeJSON(w, r, &req, maxMetadataBytes); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_json", err.Error())
		return
	}
	if req.Name != "" && req.Name != name {
		writeError(w, http.StatusBadRequest, "name_mismatch",
			"body name does not match URL")
		return
	}
	if req.Version != "" && req.Version != version {
		writeError(w, http.StatusBadRequest, "version_mismatch",
			"body version does not match URL")
		return
	}
	entry := &Version{
		Version:        version,
		Tag:            strings.TrimSpace(req.Tag),
		Rev:            strings.TrimSpace(req.Rev),
		SourceSHA256:   strings.ToLower(strings.TrimSpace(req.SourceSHA256)),
		ManifestSHA256: strings.ToLower(strings.TrimSpace(req.ManifestSHA256)),
		ArchiveSHA256:  strings.ToLower(strings.TrimSpace(req.ArchiveSHA256)),
		ArchiveSize:    req.ArchiveSize,
		Publisher:      user.Publisher(),
	}
	created, err := a.store.PutVersion(name, strings.TrimSpace(req.Repo), entry)
	switch {
	case errors.Is(err, ErrVersionExist):
		writeError(w, http.StatusConflict, "version_exists",
			"version already exists with different contents; bump the version")
		return
	case errors.Is(err, ErrRepoMismatch):
		writeError(w, http.StatusConflict, "repo_mismatch",
			"package is already bound to a different Git repository")
		return
	case err != nil:
		writeError(w, http.StatusBadRequest, "publish_failed", err.Error())
		return
	}
	status := http.StatusOK
	if created {
		status = http.StatusCreated
	}
	writeJSON(w, status, map[string]any{
		"ok": true,
		"data": map[string]any{
			"name": name, "version": version, "created": created,
			"publisher": user.Publisher(),
		},
	})
}

func (a *App) handleArtifactUpload(w http.ResponseWriter, r *http.Request) {
	user, ok := a.requireUser(w, r)
	if !ok {
		return
	}
	name, version := r.PathValue("name"), r.PathValue("version")
	pkg, err := a.store.Get(name)
	if errors.Is(err, ErrNotFound) {
		writeError(w, http.StatusNotFound, "not_found", "publish metadata first")
		return
	}
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}
	entry, ok := pkg.Versions[version]
	if !ok {
		writeError(w, http.StatusNotFound, "not_found", "version not found")
		return
	}
	if entry.Publisher.ID != "" && entry.Publisher.ID != user.Sub {
		writeError(w, http.StatusForbidden, "forbidden",
			"only the publisher can upload the artifact")
		return
	}
	expected := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("sha256")))
	if expected == "" {
		expected = entry.ArchiveSHA256
	}
	if err := validateDigest(expected); err != nil || expected == "" {
		writeError(w, http.StatusBadRequest, "missing_sha256",
			"sha256 query or metadata archive_sha256 is required")
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, maxArtifactBytes)
	temp, err := a.store.TempArtifact(name, version)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", err.Error())
		return
	}
	defer os.Remove(temp)

	hasher := sha256.New()
	file, err := os.OpenFile(temp, os.O_WRONLY|os.O_TRUNC, 0o644)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", err.Error())
		return
	}
	size, copyErr := io.Copy(io.MultiWriter(file, hasher), r.Body)
	closeErr := file.Close()
	if copyErr != nil {
		writeError(w, http.StatusBadRequest, "upload_failed", copyErr.Error())
		return
	}
	if closeErr != nil {
		writeError(w, http.StatusInternalServerError, "store_error", closeErr.Error())
		return
	}
	actual := hex.EncodeToString(hasher.Sum(nil))
	if actual != expected {
		writeError(w, http.StatusBadRequest, "checksum_mismatch",
			fmt.Sprintf("artifact sha256 %s != %s", actual, expected))
		return
	}
	if entry.ArchiveSize != 0 && entry.ArchiveSize != size {
		writeError(w, http.StatusBadRequest, "size_mismatch",
			fmt.Sprintf("artifact size %d != %d", size, entry.ArchiveSize))
		return
	}
	final, err := a.store.CommitArtifact(name, version, temp, actual)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", err.Error())
		return
	}
	updated, err := a.store.SetArtifact(name, version, actual, size, a.publicURL)
	if err != nil {
		os.Remove(final)
		writeError(w, http.StatusInternalServerError, "store_error", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true,
		"data": map[string]any{
			"name": name, "version": version,
			"sha256": actual, "size": size,
			"url": updated.ArtifactURL,
		},
	})
}

func (a *App) handleArtifactDownload(w http.ResponseWriter, r *http.Request) {
	name, version := r.PathValue("name"), r.PathValue("version")
	if err := validateName(name); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_name", err.Error())
		return
	}
	if err := validateVersion(version); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_version", err.Error())
		return
	}
	pkg, err := a.store.Get(name)
	if errors.Is(err, ErrNotFound) {
		writeError(w, http.StatusNotFound, "not_found", "package not found")
		return
	}
	entry, ok := pkg.Versions[version]
	if !ok {
		writeError(w, http.StatusNotFound, "not_found", "version not found")
		return
	}
	path := a.store.ArtifactPath(name, version)
	info, err := os.Stat(path)
	if err != nil || info.IsDir() {
		writeError(w, http.StatusNotFound, "artifact_not_found",
			"artifact not uploaded")
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition",
		fmt.Sprintf("attachment; filename=%q", name+"-"+version+".cinpkg"))
	w.Header().Set("X-PIC-SHA256", entry.ArchiveSHA256)
	w.Header().Set("ETag", `"`+entry.ArchiveSHA256+`"`)
	http.ServeFile(w, r, path)
}

func (a *App) handleYank(w http.ResponseWriter, r *http.Request) {
	user, ok := a.requireUser(w, r)
	if !ok {
		return
	}
	var body struct {
		Yanked *bool `json:"yanked"`
	}
	if err := decodeJSON(w, r, &body, maxMetadataBytes); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_json", err.Error())
		return
	}
	yanked := true
	if body.Yanked != nil {
		yanked = *body.Yanked
	}
	updated, err := a.store.Yank(r.PathValue("name"), r.PathValue("version"),
		yanked, user.Sub)
	switch {
	case errors.Is(err, ErrForbidden):
		writeError(w, http.StatusForbidden, "forbidden",
			"only the publisher can yank this version")
		return
	case errors.Is(err, ErrNotFound):
		writeError(w, http.StatusNotFound, "not_found", "package/version not found")
		return
	case err != nil:
		writeError(w, http.StatusBadRequest, "yank_failed", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true,
		"data": map[string]any{
			"name": r.PathValue("name"), "version": r.PathValue("version"),
			"yanked": updated.Yanked,
		},
	})
}

func (a *App) requireUser(w http.ResponseWriter, r *http.Request) (User, bool) {
	header := r.Header.Get("Authorization")
	if !strings.HasPrefix(strings.ToLower(header), "bearer ") {
		writeError(w, http.StatusUnauthorized, "unauthorized",
			"Bearer <Cdifit access token> is required")
		return User{}, false
	}
	token := strings.TrimSpace(header[len("Bearer "):])
	user, err := a.auth.Authenticate(token)
	if err != nil {
		if errors.Is(err, ErrUnauthorized) {
			writeError(w, http.StatusUnauthorized, "unauthorized",
				"invalid or expired Cdifit token")
		} else {
			writeError(w, http.StatusBadGateway, "auth_unavailable", err.Error())
		}
		return User{}, false
	}
	return user, true
}

func (a *App) withLogging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rec, r)
		a.logger.Printf("%s %s %d %s %s", r.Method, r.URL.Path, rec.status,
			time.Since(start).Round(time.Millisecond), clientIP(r))
	})
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

func clientIP(r *http.Request) string {
	if v := r.Header.Get("X-Forwarded-For"); v != "" {
		return strings.Split(v, ",")[0]
	}
	return r.RemoteAddr
}

func decodeJSON(w http.ResponseWriter, r *http.Request, dst any, limit int64) error {
	r.Body = http.MaxBytesReader(w, r.Body, limit)
	defer r.Body.Close()
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		return err
	}
	return nil
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]any{
		"ok": false,
		"error": map[string]string{
			"code":    code,
			"message": message,
		},
	})
}

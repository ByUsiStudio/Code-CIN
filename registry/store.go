package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

var (
	nameRE    = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_-]{0,63}$`)
	versionRE = regexp.MustCompile(`^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$`)
	shaRE     = regexp.MustCompile(`^[0-9a-f]{64}$`)

	ErrNotFound     = errors.New("not found")
	ErrVersionExist = errors.New("version already exists with different contents")
	ErrRepoMismatch = errors.New("package repository mismatch")
	ErrForbidden    = errors.New("forbidden")
	ErrConflict     = errors.New("conflict")
)

type Publisher struct {
	ID       string `json:"id"`
	Email    string `json:"email"`
	Nickname string `json:"nickname"`
}

type Version struct {
	Version        string    `json:"version"`
	Tag            string    `json:"tag"`
	Rev            string    `json:"rev"`
	SourceSHA256   string    `json:"source_sha256"`
	ManifestSHA256 string    `json:"manifest_sha256"`
	ArchiveSHA256  string    `json:"archive_sha256"`
	ArchiveSize    int64     `json:"archive_size"`
	ArtifactURL    string    `json:"artifact_url,omitempty"`
	PublishedAt    time.Time `json:"published_at"`
	Publisher      Publisher `json:"publisher"`
	Yanked         bool      `json:"yanked"`
}

type Package struct {
	Schema    string              `json:"schema"`
	Name      string              `json:"name"`
	Repo      string              `json:"repo"`
	CreatedAt time.Time           `json:"created_at"`
	UpdatedAt time.Time           `json:"updated_at"`
	Versions  map[string]*Version `json:"versions"`
}

type PackageSummary struct {
	Name      string    `json:"name"`
	Repo      string    `json:"repo"`
	Latest    string    `json:"latest"`
	Versions  int       `json:"versions"`
	UpdatedAt time.Time `json:"updated_at"`
}

type Store struct {
	root string
	mu   sync.Mutex
}

func NewStore(root string) (*Store, error) {
	s := &Store{root: root}
	for _, dir := range []string{s.packagesDir(), s.artifactsDir(), s.tmpDir()} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, err
		}
	}
	return s, nil
}

func (s *Store) packagesDir() string  { return filepath.Join(s.root, "packages") }
func (s *Store) artifactsDir() string { return filepath.Join(s.root, "artifacts") }
func (s *Store) tmpDir() string       { return filepath.Join(s.root, "tmp") }

func (s *Store) packagePath(name string) string {
	return filepath.Join(s.packagesDir(), name+".json")
}

func (s *Store) artifactPath(name, version string) string {
	return filepath.Join(s.artifactsDir(), name, version+".cinpkg")
}

func (s *Store) TempArtifact(name, version string) (string, error) {
	dir := filepath.Join(s.tmpDir(), name)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	f, err := os.CreateTemp(dir, version+"-*.part")
	if err != nil {
		return "", err
	}
	path := f.Name()
	if err := f.Close(); err != nil {
		os.Remove(path)
		return "", err
	}
	return path, nil
}

func validateName(name string) error {
	if !nameRE.MatchString(name) {
		return fmt.Errorf("invalid package name %q", name)
	}
	return nil
}

func validateVersion(version string) error {
	if !versionRE.MatchString(version) {
		return fmt.Errorf("invalid version %q", version)
	}
	return nil
}

func validateDigest(digest string) error {
	if digest != "" && !shaRE.MatchString(digest) {
		return fmt.Errorf("invalid sha256 %q", digest)
	}
	return nil
}

func (s *Store) Get(name string) (*Package, error) {
	if err := validateName(name); err != nil {
		return nil, err
	}
	data, err := os.ReadFile(s.packagePath(name))
	if errors.Is(err, os.ErrNotExist) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	var pkg Package
	if err := json.Unmarshal(data, &pkg); err != nil {
		return nil, fmt.Errorf("decode %s: %w", name, err)
	}
	if pkg.Versions == nil {
		pkg.Versions = map[string]*Version{}
	}
	return &pkg, nil
}

func (s *Store) List() ([]*Package, error) {
	entries, err := os.ReadDir(s.packagesDir())
	if err != nil {
		return nil, err
	}
	var out []*Package
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		name := strings.TrimSuffix(entry.Name(), ".json")
		pkg, err := s.Get(name)
		if err != nil {
			continue
		}
		out = append(out, pkg)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out, nil
}

func (s *Store) PutVersion(name, repo string, version *Version) (bool, error) {
	if err := validateName(name); err != nil {
		return false, err
	}
	if err := validateVersion(version.Version); err != nil {
		return false, err
	}
	if strings.TrimSpace(repo) == "" {
		return false, errors.New("repo is required")
	}
	if err := validateDigest(version.SourceSHA256); err != nil {
		return false, err
	}
	if version.SourceSHA256 == "" {
		return false, errors.New("source_sha256 is required")
	}
	if err := validateDigest(version.ManifestSHA256); err != nil {
		return false, err
	}
	if version.ManifestSHA256 == "" {
		return false, errors.New("manifest_sha256 is required")
	}
	if err := validateDigest(version.ArchiveSHA256); err != nil {
		return false, err
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	pkg, err := s.Get(name)
	created := false
	if errors.Is(err, ErrNotFound) {
		pkg = &Package{
			Schema:    "pic.package/v1",
			Name:      name,
			Repo:      repo,
			CreatedAt: time.Now().UTC(),
			Versions:  map[string]*Version{},
		}
		created = true
	} else if err != nil {
		return false, err
	}
	if pkg.Repo != "" && pkg.Repo != repo {
		return false, ErrRepoMismatch
	}
	if existing, ok := pkg.Versions[version.Version]; ok {
		if existing.SourceSHA256 == version.SourceSHA256 &&
			existing.ArchiveSHA256 == version.ArchiveSHA256 {
			if version.Tag != "" {
				existing.Tag = version.Tag
			}
			if version.Rev != "" {
				existing.Rev = version.Rev
			}
			pkg.UpdatedAt = time.Now().UTC()
			if err := s.writePackage(pkg); err != nil {
				return false, err
			}
			return false, nil
		}
		return false, ErrVersionExist
	}
	version.Version = strings.TrimSpace(version.Version)
	version.PublishedAt = time.Now().UTC()
	if version.Publisher.ID == "" {
		return false, errors.New("publisher is required")
	}
	pkg.Versions[version.Version] = version
	pkg.UpdatedAt = version.PublishedAt
	if err := s.writePackage(pkg); err != nil {
		return false, err
	}
	return created, nil
}

func (s *Store) SetArtifact(name, version, digest string, size int64,
	publicURL string) (*Version, error) {
	if err := validateDigest(digest); err != nil {
		return nil, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()

	pkg, err := s.Get(name)
	if err != nil {
		return nil, err
	}
	v, ok := pkg.Versions[version]
	if !ok {
		return nil, ErrNotFound
	}
	if v.ArchiveSHA256 != "" && v.ArchiveSHA256 != digest {
		return nil, ErrConflict
	}
	if v.ArchiveSize != 0 && v.ArchiveSize != size {
		return nil, ErrConflict
	}
	v.ArchiveSHA256 = digest
	v.ArchiveSize = size
	v.ArtifactURL = strings.TrimRight(publicURL, "/") + "/api/v1/packages/" +
		name + "/" + version + "/artifact"
	pkg.UpdatedAt = time.Now().UTC()
	if err := s.writePackage(pkg); err != nil {
		return nil, err
	}
	return v, nil
}

func (s *Store) Yank(name, version string, yanked bool, userID string) (*Version, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	pkg, err := s.Get(name)
	if err != nil {
		return nil, err
	}
	v, ok := pkg.Versions[version]
	if !ok {
		return nil, ErrNotFound
	}
	if v.Publisher.ID != "" && v.Publisher.ID != userID {
		return nil, ErrForbidden
	}
	v.Yanked = yanked
	pkg.UpdatedAt = time.Now().UTC()
	if err := s.writePackage(pkg); err != nil {
		return nil, err
	}
	return v, nil
}

func (s *Store) writePackage(pkg *Package) error {
	data, err := json.MarshalIndent(pkg, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return atomicWrite(s.packagePath(pkg.Name), data, 0o644)
}

func (s *Store) CommitArtifact(name, version, tempPath, digest string) (string, error) {
	final := s.artifactPath(name, version)
	if err := os.MkdirAll(filepath.Dir(final), 0o755); err != nil {
		return "", err
	}
	// Windows 的 os.Rename 不能覆盖已存在文件; 重复上传同版本时先移除。
	if _, err := os.Stat(final); err == nil {
		if err := os.Remove(final); err != nil {
			return "", err
		}
	}
	if err := os.Rename(tempPath, final); err != nil {
		return "", err
	}
	return final, nil
}

func (s *Store) ArtifactPath(name, version string) string {
	return s.artifactPath(name, version)
}

func atomicWrite(path string, data []byte, mode os.FileMode) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	f, err := os.CreateTemp(dir, ".tmp-*")
	if err != nil {
		return err
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if _, err := f.Write(data); err != nil {
		f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	if err := os.Chmod(tmp, mode); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func Latest(versions map[string]*Version) string {
	var keys []string
	for v := range versions {
		keys = append(keys, v)
	}
	sort.Slice(keys, func(i, j int) bool { return versionLess(keys[j], keys[i]) })
	if len(keys) == 0 {
		return ""
	}
	return keys[0]
}

func versionLess(a, b string) bool {
	pa, pb := versionParts(a), versionParts(b)
	for i := 0; i < 3; i++ {
		if pa[i] != pb[i] {
			return pa[i] < pb[i]
		}
	}
	return a < b
}

func versionParts(v string) [3]int {
	base := strings.SplitN(v, "-", 2)[0]
	base = strings.SplitN(base, "+", 2)[0]
	parts := strings.Split(base, ".")
	var out [3]int
	for i := 0; i < len(parts) && i < 3; i++ {
		out[i], _ = strconv.Atoi(parts[i])
	}
	return out
}

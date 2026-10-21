package antigravity

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
)

var versionPattern = regexp.MustCompile(`^2\.\d+\.\d+(?:[-.][\w.]+)?$`)

// FindInstallation discovers a native 2.x language_server and reads its ASAR
// package version. An explicit path or ANTIGRAVITY_ENGINE_PATH disables fallback.
func FindInstallation(requested string) (*Installation, error) {
	if requested == "" {
		requested = os.Getenv("ANTIGRAVITY_ENGINE_PATH")
	}
	var candidates []string
	if requested != "" {
		candidates = []string{requested}
	} else {
		home, _ := os.UserHomeDir()
		switch runtime.GOOS {
		case "windows":
			if local := os.Getenv("LOCALAPPDATA"); local != "" {
				candidates = []string{filepath.Join(local, "Programs", "antigravity", "resources", "bin", "language_server.exe")}
			}
		case "darwin":
			for _, root := range []string{"/Applications", filepath.Join(home, "Applications")} {
				candidates = append(candidates, filepath.Join(root, "Antigravity.app", "Contents", "Resources", "bin", "language_server"))
			}
		default:
			for _, root := range []string{"/opt/Antigravity", "/opt/antigravity", "/usr/share/antigravity"} {
				candidates = append(candidates, filepath.Join(root, "resources", "bin", "language_server"))
			}
		}
	}
	for _, path := range candidates {
		name := filepath.Base(path)
		if name != "language_server" && name != "language_server.exe" {
			continue
		}
		info, err := os.Stat(path)
		if err != nil || !info.Mode().IsRegular() {
			continue
		}
		version, err := packageVersion(filepath.Join(filepath.Dir(filepath.Dir(path)), "app.asar"))
		if err != nil {
			continue
		}
		absolute, err := filepath.Abs(path)
		if err != nil {
			continue
		}
		return &Installation{absolute, version}, nil
	}
	return nil, fault("engine_unavailable", "Install Antigravity 2.x and sign in once, or set ANTIGRAVITY_ENGINE_PATH.", nil)
}
func packageVersion(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	header := make([]byte, 16)
	if _, err = io.ReadFull(f, header); err != nil {
		return "", err
	}
	headerSize := binary.LittleEndian.Uint32(header[4:8])
	jsonSize := binary.LittleEndian.Uint32(header[12:16])
	if jsonSize > 8*1024*1024 {
		return "", fmt.Errorf("invalid ASAR header")
	}
	data := make([]byte, int(jsonSize))
	if _, err = io.ReadFull(f, data); err != nil {
		return "", err
	}
	var archive struct {
		Files map[string]struct {
			Offset string
			Size   int
		}
	}
	if err = json.Unmarshal(data, &archive); err != nil {
		return "", err
	}
	entry, ok := archive.Files["package.json"]
	if !ok || entry.Size < 1 || entry.Size > 100000 {
		return "", fmt.Errorf("invalid package entry")
	}
	offset, err := strconv.ParseInt(entry.Offset, 10, 64)
	if err != nil || offset < 0 {
		return "", fmt.Errorf("invalid package offset")
	}
	if _, err = f.Seek(8+int64(headerSize)+offset, io.SeekStart); err != nil {
		return "", err
	}
	data = make([]byte, entry.Size)
	if _, err = io.ReadFull(f, data); err != nil {
		return "", err
	}
	var pkg struct{ Version string }
	if err = json.Unmarshal(data, &pkg); err != nil {
		return "", err
	}
	if !versionPattern.MatchString(pkg.Version) {
		return "", fmt.Errorf("Antigravity 2.x required")
	}
	return pkg.Version, nil
}

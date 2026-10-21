package antigravity

import (
	"encoding/binary"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func installFixture(t *testing.T, version string) string {
	t.Helper()
	root := t.TempDir()
	bin := filepath.Join(root, "bin", "language_server.exe")
	if err := os.MkdirAll(filepath.Dir(bin), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(bin, []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	pkg, _ := json.Marshal(Object{"version": version})
	header, _ := json.Marshal(Object{"files": Object{"package.json": Object{"offset": "0", "size": len(pkg)}}})
	prefix := make([]byte, 16)
	binary.LittleEndian.PutUint32(prefix[4:8], uint32(8+len(header)))
	binary.LittleEndian.PutUint32(prefix[12:16], uint32(len(header)))
	data := append(append(prefix, header...), pkg...)
	if err := os.WriteFile(filepath.Join(root, "app.asar"), data, 0600); err != nil {
		t.Fatal(err)
	}
	return bin
}
func TestInstallationDiscovery(t *testing.T) {
	bin := installFixture(t, "2.24.0")
	i, err := FindInstallation(bin)
	if err != nil || i.Version != "2.24.0" {
		t.Fatal(i, err)
	}
	t.Setenv("ANTIGRAVITY_ENGINE_PATH", bin)
	i, err = FindInstallation("")
	if err != nil || i.Executable != bin {
		t.Fatal(i, err)
	}
	_, err = FindInstallation(filepath.Join(t.TempDir(), "language_server.exe"))
	requireCode(t, err, "engine_unavailable")
	_, err = FindInstallation(installFixture(t, "1.99.0"))
	requireCode(t, err, "engine_unavailable")
	if err = os.WriteFile(filepath.Join(filepath.Dir(filepath.Dir(bin)), "app.asar"), []byte("broken"), 0600); err != nil {
		t.Fatal(err)
	}
	_, err = FindInstallation(bin)
	requireCode(t, err, "engine_unavailable")
}

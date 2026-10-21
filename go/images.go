package antigravity

import (
	"encoding/base64"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

var imageTypes = map[string]string{".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}

func collectImages(steps []any, directory, expected string) ([]ImageArtifact, error) {
	var imageSteps []Object
	for _, v := range steps {
		if s := field(object(v), "generateImage"); len(s) > 0 {
			imageSteps = append(imageSteps, s)
		}
	}
	if len(imageSteps) == 0 {
		return nil, fault("image_missing", "No native image generation step returned.", nil)
	}
	var images []ImageArtifact
	for _, s := range imageSteps {
		model := str(s, "modelName")
		if expected != "" && model != expected {
			return nil, &Error{Code: "image_model_mismatch", Message: "Returned image model differs from ExpectedModel.", Details: model}
		}
		media := field(s, "generatedMedia")
		data := str(media, "inlineData")
		if data == "" {
			data = str(field(s, "generatedImage"), "base64Data")
		}
		if data != "" {
			decoded, err := base64.StdEncoding.Strict().DecodeString(data)
			if err != nil {
				return nil, fault("incompatible_format", "Invalid generated image encoding.", err)
			}
			kind := str(media, "mimeType")
			if kind == "" {
				kind = "application/octet-stream"
			}
			images = append(images, ImageArtifact{decoded, kind, model})
		}
	}
	// Ignore external URIs. Walk only our output directory; resolve each path
	// and reject symlinks escaping the owned root before reading any bytes.
	if len(images) == 0 {
		root, err := filepath.EvalSymlinks(directory)
		if err != nil {
			return nil, err
		}
		err = filepath.WalkDir(directory, func(path string, entry fs.DirEntry, walkErr error) error {
			if walkErr != nil {
				return walkErr
			}
			if entry.IsDir() {
				return nil
			}
			kind := imageTypes[strings.ToLower(filepath.Ext(path))]
			if kind == "" {
				return nil
			}
			resolved, err := filepath.EvalSymlinks(path)
			if err != nil {
				return nil
			}
			rel, err := filepath.Rel(root, resolved)
			if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
				return nil
			}
			info, err := os.Stat(resolved)
			if err != nil || !info.Mode().IsRegular() {
				return nil
			}
			data, err := os.ReadFile(resolved)
			if err != nil {
				return err
			}
			model := ""
			if len(imageSteps) == 1 {
				model = str(imageSteps[0], "modelName")
			}
			images = append(images, ImageArtifact{data, kind, model})
			return nil
		})
		if err != nil {
			return nil, err
		}
	}
	if len(images) == 0 {
		return nil, fault("image_missing", "Native image step returned no image bytes.", nil)
	}
	for _, a := range images {
		if len(a.Data) == 0 {
			return nil, fault("image_missing", "Native image step returned empty bytes.", nil)
		}
	}
	return images, nil
}

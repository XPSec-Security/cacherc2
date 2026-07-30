//go:build !windows

package command

import (
	"context"
	"os/exec"
)

func hideWindow(cmd *exec.Cmd) {
	// no-op on Unix/macOS
}

func newShellCmd(ctx context.Context, command string) *exec.Cmd {
	return exec.CommandContext(ctx, "sh", "-c", command)
}
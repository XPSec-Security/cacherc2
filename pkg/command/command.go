package command

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func ExecuteCommand(cmdStr string) string {
	return ExecuteCommandTimeout(cmdStr, 0)
}

func ExecuteCommandTimeout(cmdStr string, timeout time.Duration) string {
	cmdStr = strings.TrimSpace(cmdStr)
	if cmdStr == "" {
		return ""
	}

	args, err := tokenize(cmdStr)
	if err != nil {
		return strings.TrimSpace(fmt.Sprintf("Error: %v", err))
	}

	return run(args, cmdStr, timeout)
}

func ExecuteArgs(args []string) string {
	return run(args, "", 0)
}

func ExecuteArgsTimeout(args []string, timeout time.Duration) string {
	return run(args, "", timeout)
}

func run(args []string, original string, timeout time.Duration) string {
	if len(args) == 0 {
		return ""
	}

	ctx := context.Background()
	if timeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, timeout)
		defer cancel()
	}

	output, err := execute(ctx, args)

	// Fallback to the shell only when the executable does not exist in the PATH
	// (built-ins like dir/echo/cd, or use of pipes/redirection). Requires the
	// original string and a still-valid context.
	if err != nil &&
		original != "" &&
		ctx.Err() == nil &&
		errors.Is(err, exec.ErrNotFound) {

		output, err = executeShell(ctx, original)
	}

	if ctx.Err() == context.DeadlineExceeded {
		return strings.TrimSpace(fmt.Sprintf("Error: timeout %s", timeout))
	}

	if err != nil && output == "" {
		output = fmt.Sprintf("Error: %v", err)
	}

	return strings.TrimSpace(output)
}

func execute(ctx context.Context, args []string) (string, error) {
	return runCmd(exec.CommandContext(ctx, args[0], args[1:]...))
}

func executeShell(ctx context.Context, command string) (string, error) {
	return runCmd(newShellCmd(ctx, command))
}

func runCmd(cmd *exec.Cmd) (string, error) {
	hideWindow(cmd)

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Run()

	output := stdout.String()
	if err != nil && stderr.Len() > 0 {
		output = stderr.String()
	}

	return output, err
}

func tokenize(s string) ([]string, error) {
	const (
		stateNormal = iota
		stateSingle
		stateDouble
	)

	var args []string
	var current strings.Builder
	hasToken := false
	state := stateNormal

	runes := []rune(s)
	for i := 0; i < len(runes); i++ {
		r := runes[i]

		switch state {
		case stateNormal:
			switch r {
			case ' ', '\t', '\n', '\r':
				if hasToken {
					args = append(args, current.String())
					current.Reset()
					hasToken = false
				}
			case '\'':
				state = stateSingle
				hasToken = true
			case '"':
				state = stateDouble
				hasToken = true
			case '\\':
				if i+1 < len(runes) {
					switch n := runes[i+1]; n {
					case '"', '\'', '\\', ' ', '\t':
						current.WriteRune(n)
						i++
						hasToken = true
						continue
					}
				}
				current.WriteRune(r)
				hasToken = true
			default:
				current.WriteRune(r)
				hasToken = true
			}

		case stateSingle:
			if r == '\'' {
				state = stateNormal
			} else {
				current.WriteRune(r)
			}

		case stateDouble:
			switch r {
			case '"':
				state = stateNormal
			case '\\':
				if i+1 < len(runes) {
					if n := runes[i+1]; n == '"' || n == '\\' {
						current.WriteRune(n)
						i++
						continue
					}
				}
				current.WriteRune(r)
			default:
				current.WriteRune(r)
			}
		}
	}

	if state != stateNormal {
		return nil, fmt.Errorf("unclosed quotes in the command")
	}

	if hasToken {
		args = append(args, current.String())
	}

	return args, nil
}

func InvokeHostCommand(cmdStr string) string {
	if cmdStr == "" {
		return ""
	}
	return ExecuteCommand(strings.TrimSpace(cmdStr))
}

func ParseTitleCommand(title string) (uuid, command string) {
	title = strings.TrimSpace(title)

	openBracket := strings.Index(title, "[")
	if openBracket == -1 {
		return "", ""
	}

	closeBracket := strings.Index(title[openBracket:], "]")
	if closeBracket == -1 {
		return "", ""
	}

	closeBracket += openBracket

	uuid = title[openBracket+1 : closeBracket]
	command = strings.TrimSpace(title[closeBracket+1:])

	return uuid, command
}
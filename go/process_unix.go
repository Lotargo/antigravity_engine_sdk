//go:build !windows

package antigravity

import (
	"os/exec"
	"syscall"
)

type processJob struct{}

func configureProcess(cmd *exec.Cmd)      { cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true} }
func attachJob(cmd *exec.Cmd) *processJob { return nil }
func (j *processJob) close()              {}
func killTree(cmd *exec.Cmd, job *processJob) {
	_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	_ = cmd.Process.Kill()
}

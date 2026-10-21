//go:build windows

package antigravity

import (
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

type processJob struct{ handle windows.Handle }

func configureProcess(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: windows.CREATE_NO_WINDOW | windows.CREATE_NEW_PROCESS_GROUP}
}
func attachJob(cmd *exec.Cmd) *processJob {
	h, err := windows.CreateJobObject(nil, nil)
	if err != nil {
		return nil
	}
	info := windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION{}
	info.BasicLimitInformation.LimitFlags = windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
	_, err = windows.SetInformationJobObject(h, windows.JobObjectExtendedLimitInformation, uintptr(unsafe.Pointer(&info)), uint32(unsafe.Sizeof(info)))
	if err != nil {
		_ = windows.CloseHandle(h)
		return nil
	}
	p, err := windows.OpenProcess(windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE, false, uint32(cmd.Process.Pid))
	if err != nil {
		_ = windows.CloseHandle(h)
		return nil
	}
	defer windows.CloseHandle(p)
	if err = windows.AssignProcessToJobObject(h, p); err != nil {
		_ = windows.CloseHandle(h)
		return nil
	}
	return &processJob{h}
}
func (j *processJob) close() { _ = windows.CloseHandle(j.handle) }
func killTree(cmd *exec.Cmd, job *processJob) {
	if job != nil {
		_ = windows.TerminateJobObject(job.handle, 1)
	}
	kill := exec.Command(filepath.Join(os.Getenv("SystemRoot"), "System32", "taskkill.exe"), "/PID", strconv.Itoa(cmd.Process.Pid), "/T", "/F")
	configureProcess(kill)
	_ = kill.Run()
	_ = cmd.Process.Kill()
}

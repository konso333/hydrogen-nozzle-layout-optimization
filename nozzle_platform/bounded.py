"""Cancellable, serial background helpers for CAD and saved-data rendering."""
import os
import signal
import subprocess
import time
from fluent_execution.process import WindowsJob


def run(argv,*,cwd,log,timeout,cancel):
    if cancel.is_set():raise InterruptedError('任务已停止，已生成文件保留。')
    env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONUTF8='1')
    options=dict(creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name=='nt' else dict(start_new_session=True)
    process=job=None;start=time.monotonic()
    try:
        with open(log,'wb') as stream:
            process=subprocess.Popen(argv,cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,**options)
            if os.name=='nt':job=WindowsJob();job.assign(process)
            while process.poll() is None:
                if cancel.wait(.1):raise InterruptedError('任务已停止，已生成文件保留。')
                if time.monotonic()-start>timeout:raise TimeoutError('本步骤超过运行时限，已停止并保留文件。')
            return process.returncode
    finally:
        if job:job.close()
        elif process:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        if process:process.wait(timeout=10)

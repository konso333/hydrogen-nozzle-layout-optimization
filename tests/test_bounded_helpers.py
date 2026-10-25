import os
import sys
import threading
from pathlib import Path
import pytest
from nozzle_platform.bounded import run


def test_cancelled_helper_does_not_start_a_process(tmp_path):
    cancel=threading.Event();cancel.set();log=tmp_path/'log.txt'
    with pytest.raises(InterruptedError):run([sys.executable,'-c','raise SystemExit(99)'],cwd=tmp_path,log=log,timeout=1,cancel=cancel)
    assert not log.exists()


def test_helper_timeout_and_user_stop_clean_the_owned_process(tmp_path):
    import psutil
    for mode in ['timeout','cancel']:
        cancel=threading.Event();log=tmp_path/(mode+'.txt')
        timer=threading.Timer(.5,cancel.set) if mode=='cancel' else None
        if timer:timer.start()
        try:
            with pytest.raises(InterruptedError if timer else TimeoutError):
                run([sys.executable,'-u','-c','import os,time;print(os.getpid(),flush=True);time.sleep(20)'],cwd=tmp_path,log=log,timeout=.5 if not timer else 5,cancel=cancel)
        finally:
            if timer:timer.cancel();timer.join()
        text=log.read_text(encoding='utf8').strip()
        if text:
            pid=int(text.splitlines()[-1]);assert not psutil.pid_exists(pid)

"""n8n wrapper — 让 rrclaw 能以 Python 进程方式管理 n8n"""
import os
import signal
import subprocess
import sys

def main():
    n8n_bin = "/opt/homebrew/bin/n8n"
    if not os.path.exists(n8n_bin):
        print("n8n not found at", n8n_bin, file=sys.stderr)
        sys.exit(1)

    env = os.environ.copy()
    env.setdefault("N8N_PORT", "5678")
    env.setdefault("N8N_PROTOCOL", "http")
    env.setdefault("GENERIC_TIMEZONE", "Asia/Shanghai")

    proc = subprocess.Popen([n8n_bin, "start"], env=env)

    def _shutdown(sig, frame):
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    proc.wait()
    sys.exit(proc.returncode)

if __name__ == "__main__":
    main()

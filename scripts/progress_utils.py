import sys, time

def progress_start(task: str):
    print(f"[*] {task} 시작", flush=True)
    return time.time()

def progress_update(task: str, i: int, total: int, t0: float, every: int = 200):
    if total <= 0:
        return
    if not (i == 1 or i % every == 0 or i == total):
        return
    elapsed = time.time() - t0
    rate = (i / elapsed) if elapsed > 0 else 0.0
    remain = int((total - i) / rate) if rate > 0 else -1
    pct = (i * 100.0 / total)
    bar_len = 24
    filled = int(bar_len * i / total)
    bar = '#' * filled + '-' * (bar_len - filled)
    eta = f"{remain}s" if remain >= 0 else "??s"
    sys.stdout.write(f"\r    [{bar}] {pct:5.1f}%  {i}/{total}  ETA {eta}")
    sys.stdout.flush()
    if i == total:
        sys.stdout.write("\n")

def progress_end(task: str, t0: float):
    spent = time.time() - t0
    print(f"[✓] {task} 완료 ({spent:.1f}s)", flush=True)

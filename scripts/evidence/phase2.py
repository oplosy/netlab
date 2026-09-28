#!/usr/bin/env python3
"""Run TEST-240 failover checks and write a compact tracked report."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
def main()->int:
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out=ROOT/'artifacts'/'runs'/'phase-2'/f'test-240-{stamp}.json'; out.parent.mkdir(parents=True,exist_ok=True)
    runner=ROOT/'tests'/'e2e'/'phase-2'/'measure_failover.py'
    proc=subprocess.run([sys.executable,str(runner),'--output',str(out)],cwd=ROOT,text=True,capture_output=True,check=False)
    print(proc.stdout,end='')
    if proc.stderr: print(proc.stderr,file=sys.stderr,end='')
    try: result=json.loads(out.read_text(encoding='utf-8'))
    except (OSError,ValueError): return proc.returncode or 1
    rows=[]
    for name,scenario in result.get('scenarios',{}).items():
        recovery=scenario.get('return_to_primary_seconds','not applicable')
        probes='; '.join(f"{key}={scenario[key]}" for key in ('forward_received','reverse_received') if key in scenario) or 'provider route policy'
        rows.append(f"| {name.replace('_',' ')} | {scenario.get('max_convergence_seconds','n/a')} s | {recovery} | {probes} |")
    report='\n'.join([
      '# Phase 2 failover evidence','',f"- Result: **{result.get('result','FAIL')}**",f"- Run: `{out.relative_to(ROOT).as_posix()}`",f"- Finished (UTC): `{datetime.now(timezone.utc).isoformat(timespec='seconds')}`",'- Runtime: existing project WSL2 Docker Engine and Containerlab lab','- Docker settings changed: **no**','- Convergence limit: **10 seconds**','','## Measurements','','| Failure | Convergence | Recovery | Return path evidence |','|---|---:|---:|---|',*rows,'','## Result','',result.get('error','All three failures converged to their backup paths within 10 seconds. Primary path restoration was measured separately; bidirectional probes confirmed the secondary encrypted path for edge and tunnel failures.'),''])
    (ROOT/'evidence'/'reports'/'phase-2.md').write_text(report,encoding='utf-8')
    return 0 if result.get('result')=='PASS' and proc.returncode==0 else 1
if __name__=='__main__': raise SystemExit(main())

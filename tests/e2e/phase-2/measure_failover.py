#!/usr/bin/env python3
"""Measure provider, edge, and IPsec tunnel failover for TEST-240."""
from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from config.ipsec.apply import LAB_NAME, load_inventory
from config.ipsec.apply import build_plan as ipsec_plan
from config.routing.bgp.render import PRIMARY_PROVIDER, _interface_map

LIMIT=10.0
PROBE='203.0.113.10'
def run(*args: str, check: bool=True, input_data: bytes|None=None) -> subprocess.CompletedProcess[str]:
    result=subprocess.run(args,input=input_data,text=input_data is None,capture_output=True,check=False)
    if check and result.returncode: raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result
def box(node: str)->str: return f"clab-{LAB_NAME}-{node}"
def ex(node: str,*args: str,check: bool=True,input_data: bytes|None=None)->subprocess.CompletedProcess[str]:
    return run('docker','exec',*(['-i'] if input_data is not None else []),box(node),*args,check=check,input_data=input_data)
def route(node: str,target: str)->str:
    r=ex(node,'ip','route','get',target,check=False); return r.stdout.strip() or r.stderr.strip()
def wait_all(name: str, checks: dict[str,Callable[[],bool]], timeout: float=LIMIT)->float:
    start=time.monotonic(); deadline=start+timeout; pending=set(checks)
    while pending and time.monotonic()<deadline:
        pending={key for key in pending if not checks[key]()}
        if pending: time.sleep(.1)
    if pending: raise TimeoutError(f"{name} not converged in {timeout}s: {sorted(pending)}")
    return round(time.monotonic()-start,3)
def link_ips(data: dict[str,Any], a: str,b: str,kind: str)->tuple[str,str]:
    link=next(x for x in data['links'] if x.get('kind')==kind and {e['node'] for e in x['endpoints']}=={a,b})
    ends={e['node']:ipaddress.ip_interface(e['address']) for e in link['endpoints']}
    return str(ends[a].ip),str(ends[b].ip)
def probe(node: str,target: str)->dict[str,Any]:
    r=ex(node,'python3','/tmp/netlab-phase2-traffic.py','probe',target,'--count','4','--timeout','.75','--interval','.1','--expect','up')
    return json.loads(r.stdout)
def main()->int:
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args()
    down:list[tuple[str,str]]=[]; paused:list[str]=[]; result:dict[str,Any]={'task':'TEST-240','limit_seconds':LIMIT,'scenarios':{}}
    try:
        data=load_inventory(); nodes={n['id']:n for n in data['nodes']}; sessions:dict[str,dict[str,dict[str,str]]]={}
        for link in data['links']:
            if link.get('kind')!='ebgp': continue
            edge=next((e for e in link['endpoints'] if nodes[e['node']].get('role')=='edge'),None)
            if edge is None: continue
            provider=next(e for e in link['endpoints'] if e is not edge)
            sessions.setdefault(edge['node'],{})[provider['node']]={'interface':_interface_map(nodes[edge['node']])[edge['interface']], 'peer':str(ipaddress.ip_interface(provider['address']).ip)}
        if not sessions or any(PRIMARY_PROVIDER not in p for p in sessions.values()): raise ValueError('ISP-1 session missing on an edge')
        peers=ipsec_plan(data)['peers']; traffic=(ROOT/'tests/integration/ipsec/traffic.py').read_bytes()
        for peer in peers: ex(peer['node'],'sh','-ec','umask 077; cat > /tmp/netlab-phase2-traffic.py; chmod 0700 /tmp/netlab-phase2-traffic.py',input_data=traffic)
        for edge,p in sessions.items():
            primary=p[PRIMARY_PROVIDER]
            if f"via {primary['peer']} dev {primary['interface']}" not in route(edge,PROBE): raise RuntimeError(f"{edge} is not on ISP-1 before test")
        # Withdraw all ISP-1 sessions to emulate a provider-wide outage.
        for edge,p in sessions.items():
            intf=p[PRIMARY_PROVIDER]['interface']; ex(edge,'ip','link','set','dev',intf,'down'); down.append((edge,intf))
        backup={edge:p[next(k for k in p if k!=PRIMARY_PROVIDER)] for edge,p in sessions.items()}
        provider_time=wait_all('provider failover',{edge:(lambda e=edge,b=backup[edge]:f"via {b['peer']} dev {b['interface']}" in route(e,PROBE)) for edge in sessions})
        result['scenarios']['provider_failure']={'max_convergence_seconds':provider_time,'selected_provider':'isp2-core-1','edges':sorted(sessions)}
        for edge,intf in reversed(down): ex(edge,'ip','link','set','dev',intf,'up')
        down.clear()
        provider_return=wait_all('ISP-1 policy restoration',{edge:(lambda e=edge,p=p[PRIMARY_PROVIDER]:f"via {p['peer']} dev {p['interface']}" in route(e,PROBE)) for edge,p in sessions.items()},20)
        result['scenarios']['provider_failure']['return_to_primary_seconds']=provider_return
        # Pause the primary HQ edge. Check both directions select edge-2 and carry ICMP.
        hq_backup,_=link_ips(data,'hq-edge-2','hq-dist-1','routed'); br_backup,_=link_ips(data,'br1-edge-2','br1-dist-1','routed')
        run('docker','pause',box('hq-edge-1')); paused.append('hq-edge-1'); started=time.monotonic()
        wait_all('HQ edge failover',{'route':lambda:f'{hq_backup}' in route('hq-dist-1','10.20.0.1')})
        wait_all('BR1 return route failover',{'route':lambda:f'{br_backup}' in route('br1-dist-1','10.10.0.1')})
        edge_elapsed=round(time.monotonic()-started,3)
        pair_h=next(p for p in peers if p['node']=='hq-edge-2'); pair_b=next(p for p in peers if p['node']=='br1-edge-2')
        fwd=probe('hq-edge-2',pair_h['peer_address']); rev=probe('br1-edge-2',pair_b['peer_address'])
        result['scenarios']['edge_failure']={'max_convergence_seconds':edge_elapsed,'hq_next_hop':hq_backup,'br1_next_hop':br_backup,'forward_received':fwd['received'],'reverse_received':rev['received']}
        run('docker','unpause',box('hq-edge-1')); paused.remove('hq-edge-1')
        hq_primary,_=link_ips(data,'hq-edge-1','hq-dist-1','routed'); br_primary,_=link_ips(data,'br1-edge-1','br1-dist-1','routed')
        edge_recovery=wait_all('edge recovery',{'hq':lambda:f'{hq_primary}' in route('hq-dist-1','10.20.0.1'),'br1':lambda:f'{br_primary}' in route('br1-dist-1','10.10.0.1')},30)
        result['scenarios']['edge_failure']['return_to_primary_seconds']=edge_recovery
        # Disable both endpoints of XFRM-1 and verify symmetric XFRM-2 routing/data flow.
        for node in ('hq-edge-1','br1-edge-1'): ex(node,'ip','link','set','dev','xfrm0','down'); down.append((node,'xfrm0'))
        started=time.monotonic()
        tunnel_hq=wait_all('HQ tunnel failover',{'route':lambda:f'{hq_backup}' in route('hq-dist-1','10.20.0.1')})
        tunnel_br=wait_all('BR1 return tunnel failover',{'route':lambda:f'{br_backup}' in route('br1-dist-1','10.10.0.1')})
        tunnel_elapsed=round(time.monotonic()-started,3)
        fwd=probe('hq-edge-2',pair_h['peer_address']); rev=probe('br1-edge-2',pair_b['peer_address'])
        result['scenarios']['tunnel_failure']={'max_convergence_seconds':max(tunnel_elapsed,tunnel_hq,tunnel_br),'hq_next_hop':hq_backup,'br1_next_hop':br_backup,'forward_received':fwd['received'],'reverse_received':rev['received']}
        for node,intf in reversed(down): ex(node,'ip','link','set','dev',intf,'up')
        down.clear()
        tunnel_recovery=wait_all('tunnel recovery',{'hq':lambda:f'{hq_primary}' in route('hq-dist-1','10.20.0.1'),'br1':lambda:f'{br_primary}' in route('br1-dist-1','10.10.0.1')},30)
        result['scenarios']['tunnel_failure']['return_to_primary_seconds']=tunnel_recovery
        for name,scenario in result['scenarios'].items():
            if scenario['max_convergence_seconds']>LIMIT: raise TimeoutError(f'{name} exceeded {LIMIT}s')
            if 'forward_received' in scenario and min(scenario['forward_received'],scenario['reverse_received'])<1: raise RuntimeError(f'{name} return-path probe failed')
        result['result']='PASS'
    except (OSError,KeyError,ValueError,RuntimeError,TimeoutError,subprocess.CalledProcessError) as exc:
        result['result']='FAIL'; result['error']=str(exc); print(f'TEST-240 failed: {exc}',file=sys.stderr)
    finally:
        for node,intf in reversed(down): ex(node,'ip','link','set','dev',intf,'up',check=False)
        for node in reversed(paused): run('docker','unpause',box(node),check=False)
        args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8'); print(json.dumps(result,indent=2))
    return 0 if result.get('result')=='PASS' else 1
if __name__=='__main__': raise SystemExit(main())

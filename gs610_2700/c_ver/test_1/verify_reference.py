"""Read-only reference comparison; never writes into the Python source directory."""
import sys, pathlib, subprocess, random
sys.dont_write_bytecode=True
source=pathlib.Path(__file__).resolve().parents[2]/'python_ver'/'test_2'
sys.path.insert(0,str(source))
from gs610_driver import plan_sweep
exe=pathlib.Path(__file__).with_name('gs610_2700.exe')
rng=random.Random(610)
cases=[('-3.2','3.2','0.08','12'),('-.5','.5','.08','12'),('.5','-.5','.08','12'),('0','0','.001','1')]
for _ in range(40):
 a,b=rng.randint(-320,320),rng.randint(-320,320)
 cases.append((str(a/100),str(b/100),str(rng.randint(1,50)/100),'12'))
for a,b,step,limit in cases:
 ref,_,_=plan_sweep(a,b,step,limit,False,allow_partial=True)
 result=subprocess.run([str(exe),'--dry-run','--start',a,'--stop',b,'--step',step,'--voltage-limit',limit,'--round-trip'],capture_output=True,text=True,check=True)
 lines=result.stdout.splitlines()
 values=list(map(float,lines[1:-1]))
 expected=list(map(float,ref))+list(map(float,reversed(ref)))
 assert len(values)==len(expected) and all(abs(x-y)<1e-10 for x,y in zip(values,expected)),(a,b,step)
for option,value in [('--step','0'),('--period','nan'),('--samples','1.5'),('--voltage-limit','13'),('--start','3.21'),('--nplc','11')]:
 result=subprocess.run([str(exe),'--dry-run',option,value],capture_output=True)
 assert result.returncode!=0,(option,value)
print(f'{len(cases)} reference comparisons and 6 invalid-parameter cases passed')

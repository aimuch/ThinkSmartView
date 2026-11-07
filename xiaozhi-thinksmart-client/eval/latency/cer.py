import re, sys
refs=[l.strip() for l in open(sys.argv[1])]
P=re.compile(r'[\s，。？！、,.?!；;：:“”"]')
n=lambda t:P.sub('',t).lower()
def cer(r,h):
    r,h=n(r),n(h); d=list(range(len(h)+1))
    for i in range(1,len(r)+1):
        prev,d[0]=d[0],i
        for j in range(1,len(h)+1): prev,d[j]=d[j],min(d[j]+1,d[j-1]+1,prev+(r[i-1]!=h[j-1]))
    return d[len(h)],len(r)
E=N=0;segs=0;rows=0
for l in open(sys.argv[2]):
    f,s,t=l.rstrip('\n').split('\t'); ref=refs[int(f.split('-')[0])-1]; e,k=cer(ref,t); E+=e;N+=k; segs+=int(s.split()[0]); rows+=1
    if e: print(f,s,t)
print('CER',E,N,f'{E/N:.2%}','avg segs',round(segs/rows,2), rows)

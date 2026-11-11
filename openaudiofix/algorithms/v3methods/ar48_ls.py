"""BASELINE = the method used by the current ensemble: 48th-order AR, 1536 samples on each side, least-squares
interpolation."""
import numpy as np
from scipy.linalg import solve_toeplitz, solve
ORDER=48; CTX=1536
def ar_coefs(seg, order=ORDER):
    seg=seg-seg.mean(); r=np.correlate(seg,seg,'full')[len(seg)-1:len(seg)+order]
    if r[0]<=0: return None
    r=r.copy(); r[0]*=1.0001; return solve_toeplitz(r[:order], r[1:order+1])
def fill(ctx, a, b, sr):
    x=ctx.copy(); gap=b-a; x[a:b]=0.0
    left=x[a-CTX:a]; right=x[b:b+CTX]
    coef=ar_coefs(np.concatenate([left,right]))
    if coef is None: return np.linspace(x[a-1],x[b],gap+2)[1:-1]
    A=np.concatenate([[1.0],-coef]); P=ORDER
    seg=x[a-P:b+P].copy(); n=len(seg); unk=np.arange(P,P+gap); Mrows=n-P
    B=np.zeros((Mrows,n))
    for k in range(P+1): B[np.arange(Mrows),np.arange(Mrows)+P-k]=A[k]
    known=np.ones(n,bool); known[unk]=False
    Bu=B[:,unk]; rhs=-B[:,known]@seg[known]
    return solve(Bu.T@Bu+1e-9*np.eye(gap),Bu.T@rhs,assume_a='pos')

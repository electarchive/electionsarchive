import re,sys,subprocess,json,os
S = "/tmp/ri_countbooks"
def clean(s): return [int(x.replace(",","")) for x in re.findall(r"\d[\d,]*",re.sub(r"[•'`·]","",s))]
def totals(y,kind,pdf,which=0):
    L=open(f"{S}/c{y}.txt").read().split("\n")
    pg=[];c=1
    for l in L:
        c+=l.count("\f"); pg.append(c)
    pat=r"VOTE\s*FOR\s*GOVERNOR" if kind=="g" else r"VOTE\s*FOR\s*SEC"
    cand=[]
    for i,l in enumerate(L):
        if re.search(pat,l,re.I):
            for j in range(i,min(i+130,len(L))):
                if re.match(r"\s*(state )?totals?[ .:•·,]",L[j],re.I) and "county" not in L[j].lower() and "votes" not in L[j].lower():
                    cand.append((i,j));break
    i,j=cand[which]
    line=L[j]
    nums=clean(re.sub(r"^\s*\w+( \w+)?\s*[.:•·, ]+","",line,count=1))
    return pg[j],nums,line.strip()
def tess(pdf,page,n):
    out=f"{S}/t{os.path.basename(pdf)[:8]}_{page}"
    if not os.path.exists(out+".txt"):
        subprocess.run(["pdftoppm","-r","300","-gray","-png","-f",str(page),"-l",str(page),"-singlefile",pdf,out],check=True)
        subprocess.run(["tesseract",out+".png",out,"--psm","6"],check=True,capture_output=True)
    txt=open(out+".txt").read().split("\n")
    best=None
    for l in txt:
        v=[int(x.replace(",","")) for x in re.findall(r"\d[\d,]{2,}",re.sub(r"[•'`·]","",l))]
        if len(v)>=n: best=(v[-n:],l)
    return best if best else (None,None)

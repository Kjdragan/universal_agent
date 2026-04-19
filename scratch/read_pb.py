import sys, re
with open("/home/kjdragan/.gemini/antigravity/conversations/698f926e-01ea-40cf-9b99-5267bf1cb436.pb", "rb") as f:
    data = f.read()
texts = re.findall(b"[a-zA-Z0-9 \n,.\-!?:_\"'()\[\]{}<>]{50,}", data)
for t in texts[-100:]:
    print("-------")
    print(t.decode("utf-8", errors="ignore"))

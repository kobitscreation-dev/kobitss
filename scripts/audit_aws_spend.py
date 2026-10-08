import sqlite3
import urllib.request
import json
from datetime import datetime, timezone, timedelta

print("================ LOCAL DB AUDIT ================")
if True:
    conn = sqlite3.connect("kobits.db")
    cur = conn.cursor()
    cur.execute("SELECT started_at, model, tokens_input, tokens_output, estimated_cost FROM agent_runs WHERE model LIKE '%claude%' OR model LIKE '%anthropic%' ORDER BY started_at DESC LIMIT 30")
    rows = cur.fetchall()
    print(f"Total Bedrock/Claude rows found: {len(rows)}")
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)
    print("Now (UTC):", now.isoformat())
    print("24h ago (UTC):", day_ago.isoformat())
    
    cost_24h = 0.0
    input_tokens_24h = 0
    output_tokens_24h = 0
    runs_24h = 0
    
    cost_all = 0.0
    input_tokens_all = 0
    output_tokens_all = 0
    
    for r in rows:
        st_str, model, t_in, t_out, cost = r
        t_in = t_in or 0
        t_out = t_out or 0
        cost = cost or 0.0
        
        cost_all += cost
        input_tokens_all += t_in
        output_tokens_all += t_out
        
        # Check timestamp
        try:
            # Handle ISO format
            st_dt = datetime.fromisoformat(st_str.replace("Z", "+00:00"))
            if st_dt.tzinfo is None:
                st_dt = st_dt.replace(tzinfo=timezone.utc)
            if st_dt >= day_ago:
                cost_24h += cost
                input_tokens_24h += t_in
                output_tokens_24h += t_out
                runs_24h += 1
                print(f"  [24h RUN] {st_str} | Model: {model} | In: {t_in} | Out: {t_out} | Cost: ${cost:.4f}")
            else:
                print(f"  [OLD RUN] {st_str} | Model: {model} | Cost: ${cost:.4f}")
        except Exception as e:
            print(f"  [PARSE ERR] {st_str}: {e}")
            
    print(f"\nLOCAL 24h Summary:")
    print(f"  Runs: {runs_24h}")
    print(f"  Input Tokens: {input_tokens_24h:,}")
    print(f"  Output Tokens: {output_tokens_24h:,}")
    print(f"  Estimated Cost (24h): ${cost_24h:.4f}")
    print(f"  Estimated Cost (All-Time Claude): ${cost_all:.4f}")
    conn.close()

print("\n================ RENDER CLOUD AUDIT ================")
try:
    login_req = urllib.request.Request(
        "https://kobitss.onrender.com/api/v1/auth/login",
        data=json.dumps({"email": "realuser@kobits.space", "password": "MyPass12345!"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    token = json.loads(urllib.request.urlopen(login_req).read().decode())["access_token"]
    
    runs_req = urllib.request.Request(
        "https://kobitss.onrender.com/api/v1/ai/runs",
        headers={"Authorization": f"Bearer {token}"}
    )
    res = urllib.request.urlopen(runs_req)
    cloud_runs = json.loads(res.read().decode())
    print(f"Total cloud runs: {len(cloud_runs)}")
    
    r_cost_24h = 0.0
    r_tin_24h = 0
    r_tout_24h = 0
    r_count_24h = 0
    
    for cr in cloud_runs:
        c_model = cr.get("model") or ""
        st_str = cr.get("started_at")
        tin = cr.get("tokens_input") or 0
        tout = cr.get("tokens_output") or 0
        cost = cr.get("estimated_cost") or 0.0
        
        # Bedrock pricing: Claude 3.5 Sonnet / Sonnet 4.6 is $3.00 per million input, $15.00 per million output
        calc_cost = (tin * 3.0 / 1_000_000) + (tout * 15.0 / 1_000_000) if cost == 0.0 else cost
        
        if st_str:
            try:
                st_dt = datetime.fromisoformat(st_str.replace("Z", "+00:00"))
                if st_dt.tzinfo is None:
                    st_dt = st_dt.replace(tzinfo=timezone.utc)
                if st_dt >= day_ago:
                    r_cost_24h += calc_cost
                    r_tin_24h += tin
                    r_tout_24h += tout
                    r_count_24h += 1
                    print(f"  [CLOUD 24h] {st_str} | Model: {c_model} | In: {tin} | Out: {tout} | Cost: ${calc_cost:.4f}")
            except Exception as e:
                pass
                
    print(f"\nRENDER CLOUD 24h Summary:")
    print(f"  Runs: {r_count_24h}")
    print(f"  Input Tokens: {r_tin_24h:,}")
    print(f"  Output Tokens: {r_tout_24h:,}")
    print(f"  Estimated Cost (24h): ${r_cost_24h:.4f}")
except Exception as e:
    print(f"Render Cloud audit error: {e}")

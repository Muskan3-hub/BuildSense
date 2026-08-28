import requests
import json

res = requests.post("http://localhost:5000/api/query", json={
    "query": "Generate interior design using modern_minimalist style",
    "image_path": "",
    "style_preset": "modern_minimalist"
})
print(res.status_code)
try:
    data = res.json()
    print(json.dumps(data, indent=2))
except Exception as e:
    print(res.text)

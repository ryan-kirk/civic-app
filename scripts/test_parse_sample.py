import json
from app.parser import parse_agenda_html

PATH = "/mnt/data/meetingDocuments_1408.html"

with open(PATH, "r", encoding="utf-8") as f:
    data = json.load(f)

# Find the agenda container
agenda_html = None
for d in data:
    if int(d.get("DocumentType") or 0) == 1 and d.get("Html"):
        agenda_html = d["Html"]
        break

items = parse_agenda_html(agenda_html)
print("Items:", len(items))
print(items[0])
print(items[5] if len(items) > 5 else None)
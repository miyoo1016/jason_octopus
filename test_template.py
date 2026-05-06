from fastapi.templating import Jinja2Templates
try:
    templates = Jinja2Templates(directory="frontend")
    template = templates.get_template("templates/report_screen.html")
    print(template.render({"as_of_date": "2026", "node_id": "test", "columns": [], "rows": [], "row_count": 0, "latency_ms": 0}))
except Exception as e:
    import traceback
    traceback.print_exc()

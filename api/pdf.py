"""PDF report generator using Jinja2 + WeasyPrint."""
import os
import json
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "reports")
os.makedirs(OUTPUT_DIR, exist_ok=True)

env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))


async def generate_pdf(result_id: int, tg_user_id: int, scores: dict) -> str:
    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    max_score = max(scores.values()) if scores else 1

    bars = [
        {
            "label": label,
            "score": score,
            "pct": round(score / max_score * 100) if max_score else 0,
            "highlight": i < 3,
        }
        for i, (label, score) in enumerate(top)
    ]

    template = env.get_template("report.html")
    html_str = template.render(
        result_id=result_id,
        bars=bars,
        top_types=[b["label"] for b in bars[:3]],
    )

    out_path = os.path.join(OUTPUT_DIR, f"report_{result_id}.pdf")
    HTML(string=html_str).write_pdf(out_path)
    return out_path
